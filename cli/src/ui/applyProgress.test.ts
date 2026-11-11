import { test } from "node:test";
import assert from "node:assert/strict";
import { withHint } from "./applyProgress.js";

test("withHint puts the hint before the log path", () => {
  assert.equal(
    withHint("creating EC2 Instance\nInsufficientInstanceCapacity\n\nFull log: /tmp/deploy.log", "Run it again."),
    "creating EC2 Instance\nInsufficientInstanceCapacity\n\nRun it again.\nFull log: /tmp/deploy.log",
  );
});

test("withHint adds the hint at the end when there's no log path", () => {
  assert.equal(withHint("Something failed", "Run it again."), "Something failed\n\nRun it again.");
});
