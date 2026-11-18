import { TRIPWIRE } from "../aws/ecs.js";
import type { Phase, ServiceProgress } from "../types/ecs.js";
import type { Row } from "../types/progress.js";
import type { TripwireStart } from "../types/tripwire.js";

// What a user sees under "Starting Tripwire": the database, then the two parts they'd recognize.
// Backend is the poller, runner, clusterer and notifier together; a user doesn't need those names.
const DASHBOARD = "tripwire-api";
const BACKEND = ["tripwire-poller", "tripwire-runner", "tripwire-clusterer", "tripwire-notifier"];

// The slowest phase wins when several services are combined into one row
const PHASE_ORDER: Phase[] = ["restarting", "downloading", "starting", "checking health", "healthy"];

const WAITING: Row["detail"] = "waiting for the database";

const databaseRow = (start: TripwireStart, now: number): Row => {
  if (start.databaseFailed) return { name: "Database", status: "failed", seconds: (now - start.startedAt) / 1000 };
  if (start.databaseDoneAt !== undefined) {
    return { name: "Database", status: "done", seconds: (start.databaseDoneAt - start.startedAt) / 1000 };
  }
  return { name: "Database", status: "running", seconds: (now - start.startedAt) / 1000, detail: "creating and updating tables" };
};

// One row for one or more services, timed from when the services started
const servicesRow = (
  name: string,
  services: string[],
  start: TripwireStart,
  tracked: Record<string, ServiceProgress>,
  now: number,
): Row => {
  if (start.stage === "database" || start.databaseDoneAt === undefined) {
    return { name, status: "waiting", seconds: 0, detail: WAITING };
  }
  const servicesStartedAt = start.databaseDoneAt;
  const progress = services.map((service) => tracked[service] ?? { failed: false, retried: false });
  const elapsed = (now - servicesStartedAt) / 1000;

  if (progress.every((item) => item.doneAt !== undefined)) {
    const lastDone = Math.max(...progress.map((item) => item.doneAt ?? servicesStartedAt));
    return { name, status: "done", seconds: (lastDone - servicesStartedAt) / 1000 };
  }
  const failed = services.filter((_service, index) => progress[index].failed);
  if (failed.length > 0) {
    const which = services.length > 1 ? `${failed.map((service) => TRIPWIRE.names[service] ?? service).join(", ")} failed` : undefined;
    return { name, status: "failed", seconds: elapsed, detail: which };
  }
  const waitingOn = progress.filter((item) => item.doneAt === undefined);
  if (waitingOn.some((item) => item.retried && item.phase !== "healthy")) {
    return { name, status: "running", seconds: elapsed, detail: "restarting after a failed start" };
  }
  const slowest = waitingOn
    .map((item) => item.phase ?? "downloading")
    .sort((first, second) => PHASE_ORDER.indexOf(first) - PHASE_ORDER.indexOf(second))[0];
  return { name, status: "running", seconds: elapsed, detail: slowest };
};

export const tripwireRows = (
  start: TripwireStart,
  tracked: Record<string, ServiceProgress>,
  now: number,
): Row[] => [
  databaseRow(start, now),
  servicesRow("Dashboard", [DASHBOARD], start, tracked, now),
  servicesRow("Backend", BACKEND, start, tracked, now),
];
