import { setTimeout as sleep } from "node:timers/promises";
import * as p from "@clack/prompts";
import { checkTerraform, getAccountId } from "../checks.js";
import { readConfig, writeConfig, type TripwireConfig } from "../config.js";
import { publicNameServers, sameNameServers } from "../dns.js";
import { UserError } from "../errors.js";
import { syncInfra } from "../infra.js";
import { bundledInfraDir, deploymentDir } from "../paths.js";
import { Log } from "../run.js";
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
} from "../terraform.js";
import type { StackName } from "../tfvars.js";
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

// Plans one stack and applies it if anything changed
async function applyStack(
  log: Log,
  stack: StackName,
  yes: boolean,
  planArgs: string[] = [],
): Promise<void> {
  const changed = await step(
    `${stack}: planning`,
    async () => {
      await tfInit(log, stack);
      return tfPlan(log, stack, planArgs);
    },
    (c) => (c ? `${stack}: planned` : `${stack}: no changes`),
  );
  if (!changed) {
    await discardPlan(stack);
    return;
  }

  const losses = dataLosses(await tfPlanJson(log, stack));
  if (losses.length > 0) {
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
      throw new UserError(`Stopped before changing ${stack}.`);
    }
  }

  await step(`${stack}: applying`, () => tfApply(log, stack), () => `${stack}: done`);
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
      await applyStack(log, "persistent", yes, ["-target=aws_route53_zone.main"]);
    }
    const zone = findResource(await tfState(log, "persistent"), ZONE);
    await waitForDelegation(config.domain, (zone?.values.name_servers as string[]) ?? []);
  }
  await applyStack(log, "persistent", yes);
}

export async function deploy(options: DeployOptions): Promise<void> {
  if (!options.lite) return showModes();

  p.intro("tripwire deploy --lite");
  const config = await readConfig();
  if (!config) throw new UserError("Run `tripwire init` first.");

  const log = new Log("deploy");
  const accountId = await step(
    "Checking Terraform and your AWS login",
    async () => {
      await checkTerraform(log);
      return getAccountId(log);
    },
    (account) => `AWS account ${account}`,
  );
  if (accountId !== config.accountId) {
    throw new UserError(
      `This deployment is in AWS account ${config.accountId}, ` +
        `but you're logged into ${accountId}.`,
    );
  }

  await syncInfra(bundledInfraDir(), deploymentDir());
  await writeSettings(config);

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
  await applyStack(log, "bootstrap", yes);
  await deployPersistent(log, config, yes);
  await applyStack(log, "stack", yes);

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
    last
      ? "Done."
      : `Check your email: ${emails} you should have received ` +
          "temporary login credentials from AWS.",
  );
}