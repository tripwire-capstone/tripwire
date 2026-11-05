import { setTimeout as sleep } from "node:timers/promises";
import { UserError } from "./errors.js";
import { DEPLOYMENT_NAME } from "./paths.js";
import { run, type Log } from "./run.js";

export const SERVICES = ["frontend", "app-server"];

export interface RolloutCheck {
  done: boolean;
  redeploy: string[];
  failed: string[];
}

// Next step given each service's rollout state.
// A failed rollout gets one redeploy; failing again is final.
export function checkRollouts(states: Record<string, string>, retried: string[]): RolloutCheck {
  const failing = SERVICES.filter((s) => states[s] === "FAILED");
  return {
    done: SERVICES.every((s) => states[s] === "COMPLETED"),
    redeploy: failing.filter((s) => !retried.includes(s)),
    failed: failing.filter((s) => retried.includes(s)),
  };
}

async function rolloutStates(log: Log, region: string): Promise<Record<string, string>> {
  const out = await run(log, "aws", [
    "ecs", "describe-services", "--cluster", DEPLOYMENT_NAME, "--services", ...SERVICES,
    "--region", region,
    "--query", "services[].[serviceName, deployments[0].rolloutState]", "--output", "json",
  ]);
  return Object.fromEntries(JSON.parse(out) as [string, string][]);
}

// Waits until both services run, redeploying a failed one once
export async function waitForLaminar(log: Log, region: string): Promise<void> {
  const retried: string[] = [];
  const deadline = Date.now() + 20 * 60_000;
  const logs = `aws logs tail /ecs/${DEPLOYMENT_NAME}/laminar --region ${region} --since 30m`;

  while (Date.now() < deadline) {
    const check = checkRollouts(await rolloutStates(log, region), retried);
    if (check.failed.length > 0) {
      throw new UserError(`${check.failed.join(", ")} failed to start twice. Logs: ${logs}`);
    }
    if (check.done) return;

    for (const service of check.redeploy) {
      await run(log, "aws", [
        "ecs", "update-service", "--cluster", DEPLOYMENT_NAME, "--service", service,
        "--force-new-deployment", "--region", region,
        "--query", "service.serviceName", "--output", "text",
      ]);
      retried.push(service);
    }
    await sleep(15_000);
  }
  throw new UserError(`Laminar didn't start within 20 minutes. Logs: ${logs}`);
}