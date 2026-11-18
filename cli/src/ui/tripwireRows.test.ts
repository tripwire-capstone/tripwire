import { test } from "node:test";
import assert from "node:assert/strict";
import { tripwireRows } from "./tripwireRows.js";
import type { ServiceProgress } from "../types/ecs.js";
import type { TripwireStart } from "../types/tripwire.js";

const databaseRunning: TripwireStart = { stage: "database", startedAt: 0 };
const servicesStarting: TripwireStart = { stage: "services", startedAt: 0, databaseDoneAt: 50_000 };

const running = (phase: ServiceProgress["phase"]): ServiceProgress => ({ phase, failed: false, retried: false });

test("tripwireRows shows the database running and the rest waiting for it", () => {
  assert.deepEqual(tripwireRows(databaseRunning, {}, 20_000), [
    { name: "Database", status: "running", seconds: 20, detail: "creating and updating tables" },
    { name: "Dashboard", status: "waiting", seconds: 0, detail: "waiting for the database" },
    { name: "Backend", status: "waiting", seconds: 0, detail: "waiting for the database" },
  ]);
});

test("tripwireRows marks a failed database step and leaves the rest waiting", () => {
  const rows = tripwireRows({ ...databaseRunning, databaseFailed: true }, {}, 30_000);
  assert.equal(rows[0].status, "failed");
  assert.equal(rows[1].status, "waiting");
  assert.equal(rows[2].status, "waiting");
});

test("tripwireRows times the services from when the database finished", () => {
  const rows = tripwireRows(servicesStarting, { "tripwire-api": running("downloading") }, 60_000);
  assert.deepEqual(rows[0], { name: "Database", status: "done", seconds: 50 });
  assert.deepEqual(rows[1], { name: "Dashboard", status: "running", seconds: 10, detail: "downloading" });
});

test("tripwireRows shows Backend at its slowest worker's phase", () => {
  const rows = tripwireRows(servicesStarting, {
    "tripwire-poller": { doneAt: 55_000, failed: false, retried: false },
    "tripwire-runner": running("checking health"),
    "tripwire-clusterer": running("starting"),
    "tripwire-notifier": { doneAt: 58_000, failed: false, retried: false },
  }, 60_000);
  assert.deepEqual(rows[2], { name: "Backend", status: "running", seconds: 10, detail: "starting" });
});

test("tripwireRows marks Backend done when all its workers are, at the last one's time", () => {
  const rows = tripwireRows(servicesStarting, {
    "tripwire-poller": { doneAt: 55_000, failed: false, retried: false },
    "tripwire-runner": { doneAt: 70_000, failed: false, retried: false },
    "tripwire-clusterer": { doneAt: 62_000, failed: false, retried: false },
    "tripwire-notifier": { doneAt: 58_000, failed: false, retried: false },
  }, 80_000);
  assert.deepEqual(rows[2], { name: "Backend", status: "done", seconds: 20 });
});

test("tripwireRows waits for the notifier too", () => {
  const rows = tripwireRows(servicesStarting, {
    "tripwire-poller": { doneAt: 55_000, failed: false, retried: false },
    "tripwire-runner": { doneAt: 70_000, failed: false, retried: false },
    "tripwire-clusterer": { doneAt: 62_000, failed: false, retried: false },
    "tripwire-notifier": running("checking health"),
  }, 80_000);
  assert.deepEqual(rows[2], { name: "Backend", status: "running", seconds: 30, detail: "checking health" });
});

test("tripwireRows names the worker that failed under Backend", () => {
  const rows = tripwireRows(servicesStarting, {
    "tripwire-poller": running("starting"),
    "tripwire-runner": running("starting"),
    "tripwire-clusterer": { phase: "restarting", failed: true, retried: true },
    "tripwire-notifier": running("starting"),
  }, 90_000);
  assert.deepEqual(rows[2], { name: "Backend", status: "failed", seconds: 40, detail: "Clusterer failed" });
});

test("tripwireRows says when a service is restarting after a failed start", () => {
  const rows = tripwireRows(servicesStarting, {
    "tripwire-api": { phase: "starting", failed: false, retried: true },
  }, 60_000);
  assert.equal(rows[1].detail, "restarting after a failed start");
});
