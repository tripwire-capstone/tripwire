import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration, parseTerraformEvent } from "./tfEvents.js";

// A hook line as `terraform apply -json` prints it
function hookLine(type: string, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    "@level": "info",
    "@message": "aws_db_instance.main: Creating...",
    type,
    hook: {
      resource: { addr: "aws_db_instance.main", resource_type: "aws_db_instance" },
      action: "create",
      ...extra,
    },
  });
}

test("parseTerraformEvent reads apply_start with the resource type and Terraform's message", () => {
  assert.deepEqual(parseTerraformEvent(hookLine("apply_start")), {
    type: "apply_start",
    message: "aws_db_instance.main: Creating...",
    address: "aws_db_instance.main",
    resourceType: "aws_db_instance",
    action: "create",
  });
});

test("parseTerraformEvent reads elapsed time on progress and complete", () => {
  const progress = parseTerraformEvent(hookLine("apply_progress", { elapsed_seconds: 30 }));
  const complete = parseTerraformEvent(hookLine("apply_complete", { elapsed_seconds: 312 }));
  assert.equal(progress?.type === "apply_progress" && progress.elapsedSeconds, 30);
  assert.equal(complete?.type === "apply_complete" && complete.elapsedSeconds, 312);
});

test("parseTerraformEvent reads apply_errored", () => {
  assert.equal(parseTerraformEvent(hookLine("apply_errored"))?.type, "apply_errored");
});

test("parseTerraformEvent takes the resource type from the address when it's missing", () => {
  const line = JSON.stringify({
    type: "apply_start",
    hook: { resource: { addr: "module.x.aws_vpc.main" }, action: "create" },
  });
  const event = parseTerraformEvent(line);
  assert.equal(event?.type === "apply_start" && event.resourceType, "module");
  const plain = JSON.stringify({
    type: "apply_start",
    hook: { resource: { addr: "aws_vpc.main" }, action: "create" },
  });
  const plainEvent = parseTerraformEvent(plain);
  assert.equal(plainEvent?.type === "apply_start" && plainEvent.resourceType, "aws_vpc");
});

test("parseTerraformEvent reads an error diagnostic and the resource it's about", () => {
  const line = JSON.stringify({
    "@message": "Error: creating EC2 Instance",
    type: "diagnostic",
    diagnostic: {
      severity: "error",
      summary: "creating EC2 Instance",
      detail: "InsufficientInstanceCapacity",
      address: "aws_instance.data",
    },
  });
  assert.deepEqual(parseTerraformEvent(line), {
    type: "diagnostic",
    message: "Error: creating EC2 Instance",
    severity: "error",
    summary: "creating EC2 Instance",
    detail: "InsufficientInstanceCapacity",
    address: "aws_instance.data",
  });
});

test("parseTerraformEvent reads the change summary", () => {
  const line = JSON.stringify({
    "@message": "Apply complete! Resources: 82 added, 0 changed, 0 destroyed.",
    type: "change_summary",
    changes: { add: 82, change: 0, remove: 0, operation: "apply" },
  });
  const event = parseTerraformEvent(line);
  assert.equal(event?.type === "change_summary" && event.add, 82);
});

test("parseTerraformEvent keeps the message of events it doesn't use, and skips non-JSON", () => {
  assert.deepEqual(parseTerraformEvent(JSON.stringify({ "@message": "Terraform 1.17.1", type: "version" })), {
    type: "other",
    message: "Terraform 1.17.1",
  });
  assert.equal(parseTerraformEvent("Warning: plain text"), undefined);
  assert.equal(parseTerraformEvent("42"), undefined);
});

test("formatDuration matches the CLI's style", () => {
  assert.equal(formatDuration(4), "4s");
  assert.equal(formatDuration(100), "1m 40s");
  assert.equal(formatDuration(490.7), "8m 10s");
  assert.equal(formatDuration(-3), "0s");
});
