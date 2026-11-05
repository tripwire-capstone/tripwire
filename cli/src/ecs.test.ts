import { test } from "node:test";
import assert from "node:assert/strict";
import { checkRollouts } from "./ecs.js";

test("done once both services complete", () => {
  const check = checkRollouts({ frontend: "COMPLETED", "app-server": "COMPLETED" }, []);
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