import { setTimeout as sleep } from "node:timers/promises";
import { UserError } from "../core/errors.js";
import { DEPLOYMENT_NAME } from "../core/paths.js";
import { run, type Log } from "../core/run.js";
import type { Phase, RawTask, RolloutCheck, ServiceProgress, ServiceSet, StartupUpdate, TaskInfo } from "../types/ecs.js";
import type { Row } from "../types/progress.js";

export const SERVICES = ["frontend", "app-server"];

// Next step given each service's rollout state.
// A failed rollout gets one redeploy; failing again is final.
export function checkRollouts(
  states: Record<string, string>,
  retried: string[],
  services: string[] = SERVICES,
): RolloutCheck {
  const failing = services.filter((s) => states[s] === "FAILED");
  return {
    done: services.every((s) => states[s] === "COMPLETED"),
    redeploy: failing.filter((s) => !retried.includes(s)),
    failed: failing.filter((s) => retried.includes(s)),
  };
}

async function rolloutStates(log: Log, region: string, services: string[]): Promise<Record<string, string>> {
  const out = await run(log, "aws", [
    "ecs", "describe-services", "--cluster", DEPLOYMENT_NAME, "--services", ...services,
    "--region", region,
    "--query", "services[].[serviceName, deployments[0].rolloutState]", "--output", "json",
  ]);
  return Object.fromEntries(JSON.parse(out) as [string, string][]);
}

// What a user sees for each service while Laminar starts
export const SERVICE_NAMES: Record<string, string> = { frontend: "Dashboard", "app-server": "API" };

export const LAMINAR: ServiceSet = {
  product: "Laminar",
  services: SERVICES,
  names: SERVICE_NAMES,
  logGroup: `/ecs/${DEPLOYMENT_NAME}/laminar`,
};

// infra/stack/tripwire.tf names them tripwire-<key>
export const TRIPWIRE: ServiceSet = {
  product: "Tripwire",
  services: ["tripwire-api", "tripwire-poller", "tripwire-runner", "tripwire-clusterer"],
  names: {
    "tripwire-api": "Dashboard and API",
    "tripwire-poller": "Poller",
    "tripwire-runner": "Runner",
    "tripwire-clusterer": "Clusterer",
  },
  logGroup: `/ecs/${DEPLOYMENT_NAME}/tripwire`,
};

// A service's phase from its newest task and the health of that task in each of the service's
// target groups (app-server has two: API and gRPC)
export function phaseFor(task: TaskInfo | undefined, healthPerGroup: string[][]): Phase {
  if (!task) return "downloading";
  switch (task.lastStatus) {
    case "PROVISIONING":
    case "PENDING":
    case "ACTIVATING":
      return task.pullStoppedAt ? "starting" : "downloading";
    case "RUNNING": {
      const healthy = healthPerGroup.length > 0 && healthPerGroup.every((states) => states.includes("healthy"));
      return healthy ? "healthy" : "checking health";
    }
    default:
      // DEACTIVATING, STOPPING, DEPROVISIONING, STOPPED: ECS is replacing it
      return "restarting";
  }
}

function taskIp(task: RawTask): string | undefined {
  for (const attachment of task.attachments ?? []) {
    const detail = attachment.details?.find((d) => d.name === "privateIPv4Address");
    if (detail) return detail.value;
  }
  return undefined;
}

// Each service's phase, for display only; read straight from ECS and the load balancer
export async function servicePhases(
  log: Log,
  region: string,
  services: string[] = SERVICES,
): Promise<Record<string, Phase>> {
  const servicesOut = await run(log, "aws", [
    "ecs", "describe-services", "--cluster", DEPLOYMENT_NAME, "--services", ...services,
    "--region", region,
    "--query", "services[].[serviceName, loadBalancers[].targetGroupArn]", "--output", "json",
  ]);
  const targetGroups = Object.fromEntries(JSON.parse(servicesOut) as [string, string[]][]);

  const taskArns: string[] = [];
  for (const service of services) {
    const out = await run(log, "aws", [
      "ecs", "list-tasks", "--cluster", DEPLOYMENT_NAME, "--service-name", service,
      "--region", region, "--query", "taskArns", "--output", "json",
    ]);
    taskArns.push(...(JSON.parse(out) as string[]));
  }
  const tasks: RawTask[] = [];
  if (taskArns.length > 0) {
    const out = await run(log, "aws", [
      "ecs", "describe-tasks", "--cluster", DEPLOYMENT_NAME, "--tasks", ...taskArns,
      "--region", region, "--query", "tasks", "--output", "json",
    ]);
    tasks.push(...(JSON.parse(out) as RawTask[]));
  }

  const phases: Record<string, Phase> = {};
  for (const service of services) {
    const newest = tasks
      .filter((task) => task.group === `service:${service}`)
      .sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""))[0];
    const ip = newest ? taskIp(newest) : undefined;
    const healthPerGroup: string[][] = [];
    for (const arn of targetGroups[service] ?? []) {
      const out = await run(log, "aws", [
        "elbv2", "describe-target-health", "--target-group-arn", arn, "--region", region,
        "--query", "TargetHealthDescriptions[].[Target.Id, TargetHealth.State]", "--output", "json",
      ]);
      const targets = JSON.parse(out) as [string, string][];
      healthPerGroup.push(targets.filter(([id]) => !ip || id === ip).map(([, state]) => state));
    }
    // A worker behind no load balancer (Tripwire's poller, runner, clusterer) is up once it runs
    const noLoadBalancer = (targetGroups[service] ?? []).length === 0;
    phases[service] = noLoadBalancer && newest?.lastStatus === "RUNNING" ? "healthy" : phaseFor(newest, healthPerGroup);
  }
  return phases;
}

// Waits until every service in the set runs, redeploying a failed one once.
// onUpdate gets each poll's state, for display.
export async function waitForServices(
  log: Log,
  region: string,
  set: ServiceSet,
  onUpdate?: (update: StartupUpdate) => void,
): Promise<void> {
  const retried: string[] = [];
  const deadline = Date.now() + 20 * 60_000;
  const logs = `aws logs tail ${set.logGroup} --region ${region} --since 30m`;

  while (Date.now() < deadline) {
    const states = await rolloutStates(log, region, set.services);
    if (onUpdate) {
      let phases: Record<string, Phase> | undefined;
      try {
        phases = await servicePhases(log, region, set.services);
      } catch {
        // Display only: a task can stop between calls; the rollout state above decides
      }
      onUpdate({ states, phases, retried: [...retried] });
    }

    const check = checkRollouts(states, retried, set.services);
    if (check.failed.length > 0) {
      throw new UserError(`${check.failed.map((s) => set.names[s] ?? s).join(", ")} failed to start twice. Logs: ${logs}`);
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
    await sleep(onUpdate ? 5_000 : 15_000);
  }
  throw new UserError(`${set.product} didn't start within 20 minutes. Logs: ${logs}`);
}

export async function waitForLaminar(
  log: Log,
  region: string,
  onUpdate?: (update: StartupUpdate) => void,
): Promise<void> {
  await waitForServices(log, region, LAMINAR, onUpdate);
}

// Folds one poll into what's known about each service; a service stays done once it's done
export function trackStartup(
  previous: Record<string, ServiceProgress>,
  update: StartupUpdate,
  now: number,
  services: string[] = SERVICES,
): Record<string, ServiceProgress> {
  const next: Record<string, ServiceProgress> = {};
  for (const service of services) {
    const before = previous[service] ?? { failed: false, retried: false };
    const state = update.states[service];
    const retried = update.retried.includes(service);
    next[service] = {
      doneAt: before.doneAt ?? (state === "COMPLETED" ? now : undefined),
      phase: update.phases?.[service] ?? before.phase,
      failed: state === "FAILED" && retried,
      retried,
    };
  }
  return next;
}

// One row per service under "Starting <product>" (Laminar: Dashboard and API)
export function startupRows(
  tracked: Record<string, ServiceProgress>,
  startedAt: number,
  now: number,
  set: ServiceSet = LAMINAR,
): Row[] {
  return set.services.map((service) => {
    const progress = tracked[service] ?? { failed: false, retried: false };
    const name = set.names[service] ?? service;
    if (progress.doneAt !== undefined) {
      return { name, status: "done" as const, seconds: (progress.doneAt - startedAt) / 1000 };
    }
    const seconds = (now - startedAt) / 1000;
    if (progress.failed) return { name, status: "failed" as const, seconds };
    const detail = progress.retried && progress.phase !== "healthy"
      ? "restarting after a failed start"
      : (progress.phase ?? "downloading");
    return { name, status: "running" as const, seconds, detail };
  });
}
