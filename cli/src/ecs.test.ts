import { test } from "node:test";
import assert from "node:assert/strict";
import { checkRollouts, phaseFor, startupRows, trackStartup } from "./ecs.js";

test("done once every service completes", () => {
  const check = checkRollouts({ frontend: "COMPLETED", "app-server": "COMPLETED", "mock-ui": "COMPLETED" }, []);
  assert.deepEqual(check, { done: true, redeploy: [], failed: [] });
});

test("keeps waiting while a rollout is in progress", () => {
  const check = checkRollouts({ frontend: "IN_PROGRESS", "app-server": "COMPLETED" }, []);
  assert.deepEqual(check, { done: false, redeploy: [], failed: [] });
});

test("a first failure gets a redeploy", () => {
  const check = checkRollouts({ frontend: "FAILED", "app-server": "COMPLETED" }, []);
  assert.deepEqual(check.redeploy, ["frontend"]);
  assert.deepEqual(check.failed, []);
});

test("a second failure is final", () => {
  const check = checkRollouts({ frontend: "FAILED", "app-server": "COMPLETED" }, ["frontend"]);
  assert.deepEqual(check.redeploy, []);
  assert.deepEqual(check.failed, ["frontend"]);
});
test("phaseFor: no task yet, or still pulling the image, is downloading", () => {
  assert.equal(phaseFor(undefined, []), "downloading");
  assert.equal(phaseFor({ lastStatus: "PENDING", pullStartedAt: "t" }, []), "downloading");
});

test("phaseFor: image pulled but not running yet is starting", () => {
  assert.equal(phaseFor({ lastStatus: "PENDING", pullStartedAt: "t", pullStoppedAt: "t" }, []), "starting");
});

test("phaseFor: running is checking health until every target group sees it healthy", () => {
  const running = { lastStatus: "RUNNING" };
  assert.equal(phaseFor(running, [["initial"]]), "checking health");
  assert.equal(phaseFor(running, [["unhealthy"]]), "checking health");
  assert.equal(phaseFor(running, [["healthy"], ["initial"]]), "checking health", "API healthy, gRPC not yet");
  assert.equal(phaseFor(running, []), "checking health", "not registered yet");
  assert.equal(phaseFor(running, [["healthy"], ["healthy"]]), "healthy");
});

test("phaseFor: a stopping task is being replaced", () => {
  assert.equal(phaseFor({ lastStatus: "STOPPED" }, []), "restarting");
});

test("startupRows shows each service's phase, then its time once its rollout completes", () => {
  let tracked = trackStartup(
    {},
    { states: { frontend: "IN_PROGRESS", "app-server": "IN_PROGRESS", "mock-ui": "COMPLETED" }, phases: { frontend: "checking health", "app-server": "downloading" }, retried: [] },
    10_000,
  );
  assert.deepEqual(startupRows(tracked, 0, 10_000), [
    { name: "Dashboard", status: "running", seconds: 10, detail: "checking health" },
    { name: "API", status: "running", seconds: 10, detail: "downloading" },
    { name: "Tripwire UI", status: "done", seconds: 10 },
  ]);

  // The next poll's phase lookup failed: keep the last known phases
  tracked = trackStartup(tracked, { states: { frontend: "COMPLETED", "app-server": "IN_PROGRESS" }, retried: [] }, 70_000);
  assert.deepEqual(startupRows(tracked, 0, 90_000), [
    { name: "Dashboard", status: "done", seconds: 70 },
    { name: "API", status: "running", seconds: 90, detail: "downloading" },
    { name: "Tripwire UI", status: "done", seconds: 10 },
  ]);
});

test("startupRows explains a redeploy, and shows a second failure", () => {
  const retrying = trackStartup(
    {},
    { states: { frontend: "IN_PROGRESS", "app-server": "COMPLETED" }, phases: { frontend: "starting", "app-server": "healthy" }, retried: ["frontend"] },
    5_000,
  );
  assert.equal(startupRows(retrying, 0, 5_000)[0].detail, "restarting after a failed start");
  const failed = trackStartup(retrying, { states: { frontend: "FAILED", "app-server": "COMPLETED" }, retried: ["frontend"] }, 9_000);
  assert.equal(startupRows(failed, 0, 9_000)[0].status, "failed");
});
