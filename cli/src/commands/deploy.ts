import { setTimeout as sleep } from "node:timers/promises";
import * as p from "@clack/prompts";
import { bucketExists } from "../bootstrap.js";
import { checkLogin } from "../checks.js";
import { readConfig, writeConfig, type TripwireConfig } from "../config.js";
import { publicNameServers, sameNameServers } from "../dns.js";
import { startupRows, trackStartup, waitForLaminar, type ServiceProgress } from "../ecs.js";
import { UserError } from "../errors.js";
import { syncInfra } from "../infra.js";
import { bundledInfraDir, deploymentDir } from "../paths.js";
import { applyWithProgress, withHint, type LiveApplyOptions } from "../applyProgress.js";
import { showProgress, type Block } from "../progressView.js";
import { CommandError, Log } from "../run.js";
import { formatDate, latestFinal, listSnapshots } from "../snapshots.js";
import {
  dataLosses,
  discardPlan,
  findResource,
  tfApply,
  tfInit,
  tfOutputs,
  tfPlan,
  tfPlanJson,
  tfState,
  writeSettings,
  type Plan,
} from "../terraform.js";
import { stateBucketName, type Restore, type StackName } from "../tfvars.js";
import { answer, step } from "../ui.js";
import { CLI_VERSION } from "../version.js";

const LITE_PRICE = "about $135/mo in us-east-1, plus usage";

export interface DeployOptions {
  lite?: boolean;
  fresh?: boolean;
  yes?: boolean;
}

function showModes(): void {
  console.log("Choose a mode:\n");
  console.log(`  tripwire deploy --lite    Lite, ${LITE_PRICE}\n`);
  process.exitCode = 1;
}

const HINT = "Run `tripwire deploy --lite` again to continue where it stopped.";

interface StepLabels {
  // Shown while planning and applying, and with its time when done
  title: string;
  // Shown when nothing changes; null removes the line
  unchanged: string | null;
  // Show the apply's groups live instead of a single spinner
  live?: Omit<LiveApplyOptions, "title" | "hint">;
}

// Plans one stack and applies it if anything changed, as a single line (or a live block)
async function applyStack(
  log: Log,
  stack: StackName,
  yes: boolean,
  labels: StepLabels,
  planArgs: string[] = [],
): Promise<void> {
  const spin = p.spinner({ indicator: "timer" });
  spin.start(labels.title);
  let changed: boolean;
  let plan: Plan | undefined;
  try {
    await tfInit(log, stack);
    changed = await tfPlan(log, stack, planArgs);
    if (changed) plan = await tfPlanJson(log, stack);
  } catch (err) {
    spin.error(labels.title);
    throw err;
  }
  if (!changed || !plan) {
    await discardPlan(stack);
    if (labels.unchanged === null) spin.clear();
    else spin.stop(labels.unchanged);
    return;
  }

  const losses = dataLosses(plan);
  if (losses.length > 0) {
    spin.clear();
    const what = losses.join(", ");
    if (yes) {
      await discardPlan(stack);
      throw new UserError(`This deploy would replace ${what} and lose its data. Run without --yes.`);
    }
    const ok = answer(
      await p.confirm({
        message: `This deploy replaces ${what}, losing its data. Continue?`,
        initialValue: false,
      }),
    );
    if (!ok) {
      await discardPlan(stack);
      throw new UserError(`Stopped before changing ${labels.title}.`);
    }
    spin.start(labels.title);
  }

  if (labels.live) {
    spin.clear();
    await applyWithProgress(log, stack, plan, { ...labels.live, title: labels.title, hint: HINT });
    return;
  }
  try {
    await tfApply(log, stack);
  } catch (err) {
    spin.error(labels.title);
    if (err instanceof CommandError) throw new CommandError(withHint(err.message, HINT), err.stderr);
    throw err;
  }
  spin.stop(labels.title);
}

// "Starting Laminar", with a live line per service
async function startLaminar(log: Log, region: string): Promise<void> {
  const startedAt = Date.now();
  let tracked: Record<string, ServiceProgress> = {};
  let status: Block["status"] = "running";
  const display = showProgress(() => {
    const now = Date.now();
    return { title: "Starting Laminar", status, seconds: (now - startedAt) / 1000, rows: startupRows(tracked, startedAt, now) };
  });
  try {
    await waitForLaminar(log, region, (update) => {
      tracked = trackStartup(tracked, update, Date.now());
    });
    status = "done";
  } catch (err) {
    status = "failed";
    throw err;
  } finally {
    display.stop();
  }
}

// Waits until public DNS sends the domain to the zone
async function waitForDelegation(domain: string, expected: string[]): Promise<void> {
  if (sameNameServers(await publicNameServers(domain), expected)) return;

  p.note(
    `At your DNS provider, add NS records for ${domain}:\n\n${expected.join("\n")}`,
    "Point your domain at AWS",
  );
  await step(
    "Waiting for DNS (Ctrl-C is safe, deploy again later)",
    async () => {
      while (!sameNameServers(await publicNameServers(domain), expected)) {
        await sleep(30_000);
      }
    },
    () => "DNS points at AWS",
  );
}

// The zone has count, so its state address has an index
const ZONE = "aws_route53_zone.main[0]";

async function deployPersistent(log: Log, config: TripwireConfig, yes: boolean): Promise<void> {
  if (!config.zoneId) {
    await tfInit(log, "persistent");
    if (!findResource(await tfState(log, "persistent"), ZONE)) {
      await applyStack(
        log,
        "persistent",
        yes,
        { title: `Creating a DNS zone for ${config.domain}`, unchanged: null },
        ["-target=aws_route53_zone.main"],
      );
    }
    const zone = findResource(await tfState(log, "persistent"), ZONE);
    await waitForDelegation(config.domain, (zone?.values.name_servers as string[]) ?? []);
  }
  await applyStack(log, "persistent", yes, {
    title: "Domain and HTTPS certificate",
    unchanged: "Domain and HTTPS certificate · unchanged",
  });
}

interface DataPlan {
  stackEmpty: boolean;
  restore?: Restore;
  summary: string;
}

// Whether the stack is empty, and what data it starts with
async function planData(log: Log, config: TripwireConfig, fresh: boolean): Promise<DataPlan> {
  const bucket = stateBucketName(config.accountId, config.region);
  if (!(await bucketExists(log, bucket))) return { stackEmpty: true, summary: "new, empty" };

  await tfInit(log, "stack");
  if (findResource(await tfState(log, "stack"), "aws_db_instance.main")) {
    return { stackEmpty: false, summary: "kept, the stack is running" };
  }

  const latest = latestFinal(await listSnapshots(log, config.region));
  if (!latest.db && !latest.volume) return { stackEmpty: true, summary: "new, empty" };
  if (fresh) return { stackEmpty: true, summary: "empty, saved snapshots are kept" };
  if (!latest.db || !latest.volume) {
    const found = latest.db ? "database" : "data volume";
    throw new UserError(
      `Found a ${found} snapshot but not its pair, so it can't restore. ` +
        "Use --fresh to start empty.",
    );
  }

  return {
    stackEmpty: true,
    restore: { dbSnapshotId: latest.db.id, dataVolumeSnapshotId: latest.volume.id },
    summary: `restoring from the snapshots taken ${formatDate(latest.volume.created)}`,
  };
}

export async function deploy(options: DeployOptions): Promise<void> {
  if (!options.lite) return showModes();

  p.intro("tripwire deploy --lite");
  const config = await readConfig();
  if (!config) throw new UserError("Run `tripwire init` first.");

  const log = new Log("deploy");
  await step(
    "Checking Terraform and your AWS login",
    () => checkLogin(log, config),
    () => `AWS account ${config.accountId}`,
  );

  await syncInfra(bundledInfraDir(), deploymentDir());
  await writeSettings(config);
  const data = await step("Checking for saved data", () =>
    planData(log, config, options.fresh ?? false),
  );
  if (data.restore) await writeSettings(config, data.restore);

  const last = config.lastDeploy;
  const infra = !last
    ? "first deploy"
    : last.cliVersion === CLI_VERSION
      ? `v${CLI_VERSION}`
      : `updated from v${last.cliVersion} to v${CLI_VERSION}`;
  p.note(
    [
      `Account  ${config.accountId}`,
      `Region   ${config.region}`,
      `Domain   ${config.domain}`,
      `Mode     Lite, ${LITE_PRICE}`,
      `Infra    ${infra}`,
      `Data     ${data.summary}`,
      `Time     ${data.stackEmpty ? "about 12 minutes" : "usually a few minutes"}`,
    ].join("\n"),
    "Deploy",
  );
  if (!options.yes) {
    const ok = answer(await p.confirm({ message: "Deploy?" }));
    if (!ok) {
      p.cancel("Nothing changed.");
      return;
    }
  }

  const yes = options.yes ?? false;
  await applyStack(log, "bootstrap", yes, { title: "Preparing your AWS account", unchanged: null });
  await deployPersistent(log, config, yes);
  await applyStack(log, "stack", yes, {
    title: data.stackEmpty ? "Building Laminar" : "Updating Laminar",
    unchanged: "Laminar · unchanged",
    live: {
      action: "build",
      ...(data.stackEmpty ? { estimate: "usually 8–10 minutes" } : {}),
      usualSeconds: 15 * 60,
    },
  });
  await startLaminar(log, config.region);

  const outputs = await tfOutputs(log, "stack");
  await writeConfig({
    ...config,
    lastDeploy: { mode: "lite", cliVersion: CLI_VERSION, at: new Date().toISOString() },
  });

  p.note(
    [
      `Laminar  ${outputs.laminar_url}`,
      `gRPC     ${outputs.laminar_grpc_endpoint}`,
      `Log      ${log.path}`,
    ].join("\n"),
    "Deployed",
  );

  const emails = config.laminarUsers.join(", ");
  p.outro(
    data.stackEmpty
      ? `Check your email: ${emails} you should have received ` +
          "temporary login credentials from AWS."
      : "Done.",
  );
}