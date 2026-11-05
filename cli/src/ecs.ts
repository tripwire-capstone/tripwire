import { setTimeout as sleep } from "node:timers/promises";
import { UserError } from "./errors.js";
import { DEPLOYMENT_NAME } from "./paths.js";
import type { Row } from "./progressView.js";
import { run, type Log } from "./run.js";

export const SERVICES = ["frontend", "app-server", "mock-ui"];

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

// What a user sees for each service while Laminar starts
export const SERVICE_NAMES: Record<string, string> = {
  frontend: "Dashboard",
  "app-server": "API",
  "mock-ui": "Tripwire UI",
};

export type Phase = "downloading" | "starting" | "checking health" | "healthy" | "restarting";

export interface TaskInfo {
  lastStatus: string;
  pullStartedAt?: string;
  pullStoppedAt?: string;
}

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

interface RawTask {
  lastStatus: string;
  pullStartedAt?: string;
  pullStoppedAt?: string;
  createdAt?: string;
  group?: string;
  attachments?: { details?: { name: string; value: string }[] }[];
}

function taskIp(task: RawTask): string | undefined {
  for (const attachment of task.attachments ?? []) {
    const detail = attachment.details?.find((d) => d.name === "privateIPv4Address");
    if (detail) return detail.value;
  }
  return undefined;
}

// Each service's phase, for display only; read straight from ECS and the load balancer
export async function servicePhases(log: Log, region: string): Promise<Record<string, Phase>> {
  const servicesOut = await run(log, "aws", [
    "ecs", "describe-services", "--cluster", DEPLOYMENT_NAME, "--services", ...SERVICES,
    "--region", region,
    "--query", "services[].[serviceName, loadBalancers[].targetGroupArn]", "--output", "json",
  ]);
  const targetGroups = Object.fromEntries(JSON.parse(servicesOut) as [string, string[]][]);

  const taskArns: string[] = [];
  for (const service of SERVICES) {
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
  for (const service of SERVICES) {
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
    phases[service] = phaseFor(newest, healthPerGroup);
  }
  return phases;
}

export interface StartupUpdate {
  states: Record<string, string>;
  // Missing when the phase lookup failed; show the last known one
  phases?: Record<string, Phase>;
  retried: string[];
}

// Waits until both services run, redeploying a failed one once.
// onUpdate gets each poll's state, for display.
export async function waitForLaminar(
  log: Log,
  region: string,
  onUpdate?: (update: StartupUpdate) => void,
): Promise<void> {
  const retried: string[] = [];
  const deadline = Date.now() + 20 * 60_000;
  const logs = `aws logs tail /ecs/${DEPLOYMENT_NAME}/laminar --region ${region} --since 30m`;

  while (Date.now() < deadline) {
    const states = await rolloutStates(log, region);
    if (onUpdate) {
      let phases: Record<string, Phase> | undefined;
      try {
        phases = await servicePhases(log, region);
      } catch {
        // Display only: a task can stop between calls; the rollout state above decides
      }
      onUpdate({ states, phases, retried: [...retried] });
    }

    const check = checkRollouts(states, retried);
    if (check.failed.length > 0) {
      throw new UserError(`${check.failed.map((s) => SERVICE_NAMES[s] ?? s).join(", ")} failed to start twice. Logs: ${logs}`);
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
  throw new UserError(`Laminar didn't start within 20 minutes. Logs: ${logs}`);
}

export interface ServiceProgress {
  doneAt?: number;
  phase?: Phase;
  failed: boolean;
  retried: boolean;
}

// Folds one poll into what's known about each service; a service stays done once it's done
export function trackStartup(
  previous: Record<string, ServiceProgress>,
  update: StartupUpdate,
  now: number,
): Record<string, ServiceProgress> {
  const next: Record<string, ServiceProgress> = {};
  for (const service of SERVICES) {
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

// The Dashboard and API rows under "Starting Laminar"
export function startupRows(tracked: Record<string, ServiceProgress>, startedAt: number, now: number): Row[] {
  return SERVICES.map((service) => {
    const progress = tracked[service] ?? { failed: false, retried: false };
    const name = SERVICE_NAMES[service] ?? service;
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
