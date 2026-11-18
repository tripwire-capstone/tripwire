import { setTimeout as sleep } from "node:timers/promises";
import * as p from "@clack/prompts";
import { bucketExists } from "../terraform/bootstrap.js";
import { checkLogin } from "../aws/checks.js";
import { readConfig, writeConfig } from "../core/config.js";
import { publicNameServers, sameNameServers } from "../aws/dns.js";
import { LAMINAR, TRIPWIRE, startupRows, trackStartup, waitForServices } from "../aws/ecs.js";
import { UserError } from "../core/errors.js";
import { syncInfra } from "../terraform/infra.js";
import { bundledInfraDir, deploymentDir } from "../core/paths.js";
import { applyWithProgress, withHint } from "../ui/applyProgress.js";
import { showProgress } from "../ui/progressView.js";
import { CommandError, Log } from "../core/run.js";
import { formatDate, latestFinal, listSnapshots } from "../aws/snapshots.js";
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
} from "../terraform/terraform.js";
import { stateBucketName } from "../terraform/tfvars.js";
import { servicesImageFromState, withServicesImage } from "../terraform/tripwireState.js";
import { answer, downloadingNote, step } from "../ui/ui.js";
import { migrationTarget, runMigration } from "../aws/migrate.js";
import { removeOldKeys } from "../aws/oldKeys.js";
import { tripwireRows } from "../ui/tripwireRows.js";
import { CLI_VERSION } from "../core/version.js";
import type { DataPlan, DeployOptions, StepLabels } from "../types/commands.js";
import type { TripwireConfig } from "../types/config.js";
import type { ServiceProgress, ServiceSet } from "../types/ecs.js";
import type { Block } from "../types/progress.js";
import type { Plan, Restore, StackName } from "../types/terraform.js";
import type { TripwireStart } from "../types/tripwire.js";

// Laminar about $135, Tripwire's four services about $37 (Fargate, us-east-1)
const LITE_PRICE = "about $172/mo in us-east-1, plus usage and OpenAI calls";

function showModes(): void {
  console.log("Choose a mode:\n");
  console.log(`  tripwire deploy --lite    Lite, ${LITE_PRICE}\n`);
  process.exitCode = 1;
}

const HINT = "Run `tripwire deploy --lite` again to continue where it stopped.";

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
    await tfInit(log, stack, () => spin.message(downloadingNote(labels.title)));
    spin.message(labels.title);
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

// "Starting Laminar" / "Starting Tripwire", with a live line per service
async function startServices(log: Log, region: string, set: ServiceSet): Promise<void> {
  const startedAt = Date.now();
  let tracked: Record<string, ServiceProgress> = {};
  let status: Block["status"] = "running";
  const display = showProgress(() => {
    const now = Date.now();
    return {
      title: `Starting ${set.product}`,
      status,
      seconds: (now - startedAt) / 1000,
      rows: startupRows(tracked, startedAt, now, set),
    };
  });
  try {
    await waitForServices(log, region, set, (update) => {
      tracked = trackStartup(tracked, update, Date.now(), set.services);
    });
    status = "done";
  } catch (err) {
    status = "failed";
    throw err;
  } finally {
    display.stop();
  }
}

// Gives Tripwire's services a new image: plan, check nothing loses data, apply. No display of its own:
// it runs inside "Starting Tripwire" and takes seconds (task definitions and services only).
async function applyServicesImage(log: Log): Promise<void> {
  const changed = await tfPlan(log, "stack");
  if (!changed) {
    await discardPlan("stack");
    return;
  }
  const losses = dataLosses(await tfPlanJson(log, "stack"));
  if (losses.length > 0) {
    await discardPlan("stack");
    throw new UserError(`Updating Tripwire would replace ${losses.join(", ")} and lose its data. Stopped.`);
  }
  try {
    await tfApply(log, "stack");
  } catch (err) {
    if (err instanceof CommandError) throw new CommandError(withHint(err.message, HINT), err.stderr);
    throw err;
  }
}

// "Starting Tripwire": the database (migration task), then the services on the new image.
// Dashboard and Backend wait while the database step runs. Returns the config with the image saved.
async function startTripwire(
  log: Log,
  config: TripwireConfig,
  outputs: Record<string, unknown>,
  restore: Restore | undefined,
): Promise<TripwireConfig> {
  let start: TripwireStart = { stage: "database", startedAt: Date.now() };
  let tracked: Record<string, ServiceProgress> = {};
  let status: Block["status"] = "running";
  let current = config;
  const display = showProgress(() => {
    const now = Date.now();
    return {
      title: "Starting Tripwire",
      status,
      seconds: (now - start.startedAt) / 1000,
      rows: tripwireRows(start, tracked, now),
    };
  });
  try {
    try {
      await runMigration(log, config.region, migrationTarget(outputs));
    } catch (err) {
      start = { ...start, databaseFailed: true };
      throw err;
    }
    start = { ...start, stage: "services", databaseDoneAt: Date.now() };

    const image = String(outputs.tripwire_image);
    if (current.tripwire?.servicesImage !== image) {
      current = { ...current, tripwire: { ...current.tripwire, servicesImage: image } };
      await writeSettings(current, restore);
      await applyServicesImage(log);
      await writeConfig(current);
    }
    await waitForServices(log, config.region, TRIPWIRE, (update) => {
      tracked = trackStartup(tracked, update, Date.now(), TRIPWIRE.services);
    });
    status = "done";
    return current;
  } catch (err) {
    status = "failed";
    throw err;
  } finally {
    display.stop();
  }
}

// Waits until public DNS sends the domain to the zone
// Keys live in Tripwire → Settings, not in the deploy. A restored database brings back keys saved
// there, so the steps only matter while Tripwire shows "Not connected yet"; the CLI can't tell from
// here (the page is behind the login), so it always shows them.
export const connectSteps = (config: TripwireConfig, laminarUrl: string): string =>
  [
    "If Tripwire says \"Not connected yet\":",
    `1. Open ${laminarUrl} and sign in. First time: use the temporary password AWS`,
    `   emailed to ${config.laminarUsers[0] ?? "the address you gave tripwire init"}.`,
    "2. Create a project (or open the one your app sends traces to), and create a",
    "   project API key in its settings.",
    "3. Open Tripwire → Settings and add that key and your OpenAI API key.",
    "   Signals start reading traces within a minute.",
  ].join("\n");

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
        {
          title: `Creating a DNS zone for ${config.domain}`,
          unchanged: null,
          live: { action: "build", stack: "persistent", usualSeconds: 5 * 60 },
        },
        ["-target=aws_route53_zone.main"],
      );
    }
    const zone = findResource(await tfState(log, "persistent"), ZONE);
    await waitForDelegation(config.domain, (zone?.values.name_servers as string[]) ?? []);
  }
  await applyStack(log, "persistent", yes, {
    title: "Domain and HTTPS certificate",
    unchanged: "Domain and HTTPS certificate · unchanged",
    live: { action: "build", stack: "persistent", estimate: "usually under a minute", usualSeconds: 5 * 60 },
  });
}

// Whether the stack is empty, and what data it starts with
async function planData(
  log: Log,
  config: TripwireConfig,
  fresh: boolean,
  setMessage: (text: string) => void,
): Promise<DataPlan> {
  const bucket = stateBucketName(config.accountId, config.region);
  if (!(await bucketExists(log, bucket))) return { stackEmpty: true, summary: "new, empty" };

  await tfInit(log, "stack", () => setMessage(downloadingNote("Checking for saved data")));
  setMessage("Checking for saved data");
  const state = await tfState(log, "stack");
  if (findResource(state, "aws_db_instance.main")) {
    return { stackEmpty: false, summary: "kept, the stack is running", servicesImage: servicesImageFromState(state) };
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
  const stored = await readConfig();
  if (!stored) throw new UserError("Run `tripwire init` first.");

  const log = new Log("deploy");
  await step(
    "Checking Terraform and your AWS login",
    () => checkLogin(log, stored),
    () => `AWS account ${stored.accountId}`,
  );

  await syncInfra(bundledInfraDir(), deploymentDir());
  await writeSettings(stored);
  const data = await step("Checking for saved data", (setMessage) =>
    planData(log, stored, options.fresh ?? false, setMessage),
  );
  // Whether Tripwire's services exist comes from the stack's state, not the local note: services that
  // don't exist yet are created only after the migration (startTripwire)
  const config = withServicesImage(stored, data.servicesImage);
  await writeSettings(config, data.restore);

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
      // Empty: stack ~10 min (RDS ~8), Laminar ~1.5, migration ~1, Tripwire's apply + start ~3
      `Time     ${data.stackEmpty ? "about 16 minutes" : "usually a few minutes"}`,
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

  await applyStack(log, "bootstrap", yes, {
    title: "Preparing your AWS account",
    unchanged: null,
    live: { action: "build", stack: "bootstrap", estimate: "usually about a minute", usualSeconds: 5 * 60 },
  });
  await deployPersistent(log, config, yes);
  await applyStack(log, "stack", yes, {
    title: data.stackEmpty ? "Building Laminar" : "Updating Laminar",
    unchanged: "Laminar · unchanged",
    live: {
      action: "build",
      stack: "stack",
      ...(data.stackEmpty ? { estimate: "usually 8–10 minutes" } : {}),
      usualSeconds: 15 * 60,
    },
  });
  await startServices(log, config.region, LAMINAR);

  // Tripwire: its database, then its services. Its keys are added later, in Tripwire → Settings.
  let outputs = await tfOutputs(log, "stack");
  const current = await startTripwire(log, config, outputs, data.restore);
  outputs = await tfOutputs(log, "stack");

  // The keys older deploys stored in SSM: unused since keys moved to Settings (aws/oldKeys.ts).
  // Only after the services run on task definitions that no longer name them.
  const removed = await removeOldKeys(log, config.region);
  if (removed.length > 0) p.log.info("Removed the OpenAI and Laminar keys earlier deploys stored in AWS: keys now live in Tripwire → Settings.");

  await writeConfig({
    ...current,
    lastDeploy: { mode: "lite", cliVersion: CLI_VERSION, at: new Date().toISOString() },
  });

  p.note(
    [
      `Tripwire  ${outputs.tripwire_url}`,
      `Laminar   ${outputs.laminar_url}`,
      `Log       ${log.path}`,
    ].join("\n"),
    "Deployed",
  );
  p.note(connectSteps(config, String(outputs.laminar_url)), "Connect Tripwire");

  const others = config.laminarUsers.slice(1);
  p.outro(
    data.stackEmpty && others.length > 0
      ? `Done. Tripwire uses the same login as Laminar. ${others.join(", ")} also got AWS's email with a temporary password.`
      : "Done. Tripwire uses the same login as Laminar.",
  );
}