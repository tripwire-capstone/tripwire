import { test } from "node:test";
import assert from "node:assert/strict";
import { PassThrough } from "node:stream";
import { stripVTControlCharacters } from "node:util";
import { applyEvent, startProgress } from "./progressGroups.js";
import { fitToWidth, progressBlock, renderBlock, showProgress } from "./progressView.js";
import type { Block } from "../types/progress.js";
import type { Plan, TerraformEvent } from "../types/terraform.js";

function plain(lines: string[]): string[] {
  return lines.map(stripVTControlCharacters);
}

function planOf(addresses: string[]): Plan {
  return {
    resource_changes: addresses.map((address) => ({
      address,
      type: address.split(".")[0],
      change: { actions: ["create"] },
    })),
  };
}

// Returns the "apply_start" kind of event specifically (not any TerraformEvent), so complete() can
// copy it and TypeScript knows the copy still has address, resourceType and action.
function start(address: string): Extract<TerraformEvent, { type: "apply_start" }> {
  return { type: "apply_start", message: "", address, resourceType: address.split(".")[0], action: "create" };
}

function complete(address: string): TerraformEvent {
  return { ...start(address), type: "apply_complete", elapsedSeconds: 0 };
}

const running: Block = {
  title: "Building Laminar",
  status: "running",
  seconds: 252,
  estimate: "usually 8–10 minutes",
  rows: [
    { name: "Network", status: "done", seconds: 100 },
    { name: "Database", status: "running", seconds: 252, detail: "usually the slowest" },
    { name: "Laminar app", status: "pending", seconds: 0 },
  ],
};

test("renderBlock draws a running step with its rows", () => {
  assert.deepEqual(plain(renderBlock(running, 0)), [
    "│",
    "◆  Building Laminar · 4m 12s (usually 8–10 minutes)",
    "│    ✓ Network               1m 40s",
    "│    ◒ Database              4m 12s   usually the slowest",
    "│    · Laminar app                 ",
  ]);
});

test("renderBlock animates the spinner by frame", () => {
  assert.match(plain(renderBlock(running, 1))[3], /◐ Database/);
});

test("renderBlock draws a finished and a stopped step like clack's other steps", () => {
  assert.equal(plain(renderBlock({ ...running, status: "done", seconds: 490 }, 0))[1], "◇  Building Laminar [8m 10s]");
  const failed: Block = {
    ...running,
    status: "failed",
    rows: [{ name: "Data server", status: "failed", seconds: 12 }],
  };
  const lines = plain(renderBlock(failed, 0));
  assert.equal(lines[1], "▲  Building Laminar stopped [4m 12s]");
  assert.equal(lines[2], "│    ✗ Data server              12s");
});

test("renderBlock shows a warning line under the rows", () => {
  const lines = plain(renderBlock({ ...running, warning: "Still working…" }, 0));
  assert.equal(lines.at(-1), "│    Still working…");
});

test("fitToWidth cuts lines that would wrap", () => {
  assert.equal(fitToWidth("short", 10), "short");
  assert.equal(fitToWidth("a much longer line", 10), "a much lo…");
  assert.equal(renderBlock(running, 0, 20).every((line) => stripVTControlCharacters(line).length <= 20), true);
});

test("progressBlock turns Terraform progress into rows with notes while running", () => {
  const progress = startProgress(planOf(["aws_vpc.main", "aws_db_instance.main"]), "build", 0);
  applyEvent(progress, start("aws_vpc.main"), 0);
  applyEvent(progress, complete("aws_vpc.main"), 100_000);
  applyEvent(progress, start("aws_db_instance.main"), 0);
  const block = progressBlock(progress, { title: "Building Laminar", estimate: "usually 8–10 minutes", now: 252_000 });
  assert.deepEqual(block.rows, [
    { name: "Network", status: "done", seconds: 100 },
    { name: "Database", status: "running", seconds: 252, detail: "usually the slowest" },
  ]);
  assert.equal(block.seconds, 252);
  assert.equal(block.warning, undefined);
});

test("progressBlock adds the calm warning past the usual time, only while running", () => {
  const progress = startProgress(planOf(["aws_db_instance.main"]), "build", 0);
  const options = { title: "Building Laminar", usualSeconds: 15 * 60, now: 16 * 60_000 };
  assert.match(progressBlock(progress, options).warning ?? "", /Taking longer than usual/);
  assert.equal(progressBlock(progress, { ...options, status: "done" }).warning, undefined);
});

test("showProgress prints plain lines when output isn't a terminal", () => {
  const output = new PassThrough();
  let written = "";
  output.on("data", (chunk) => (written += chunk));
  let block: Block = { ...running, rows: [{ name: "Network", status: "running", seconds: 5 }] };
  const display = showProgress(() => block, output);
  block = { ...block, status: "done", seconds: 490, rows: [{ name: "Network", status: "done", seconds: 100 }] };
  display.stop();
  assert.equal(
    written,
    [
      "│",
      "◆  Building Laminar · 4m 12s (usually 8–10 minutes)",
      "│    ✓ Network               1m 40s",
      "◇  Building Laminar [8m 10s]",
      "",
    ].join("\n"),
  );
  assert.doesNotMatch(written, /\x1b\[/, "no escape codes in plain output");
});

test("renderBlock shows a waiting row with no time", () => {
  const block: Block = { ...running, rows: [{ name: "Laminar app", status: "waiting", seconds: 2, detail: "waiting" }] };
  assert.equal(plain(renderBlock(block, 0))[2], "│    · Laminar app                    waiting");
});

test("progressBlock marks a group that's only waiting", () => {
  const progress = startProgress(planOf(["aws_cloudwatch_log_group.laminar", "aws_ecs_service.frontend"]), "build", 0);
  applyEvent(progress, start("aws_cloudwatch_log_group.laminar"), 0);
  applyEvent(progress, complete("aws_cloudwatch_log_group.laminar"), 2000);
  const block = progressBlock(progress, { title: "Building Laminar", now: 300_000 });
  assert.deepEqual(block.rows, [{ name: "Laminar app", status: "waiting", seconds: 2, detail: "waiting" }]);
});

test("renderBlock widens the name column for long names so times line up", () => {
  const block: Block = {
    ...running,
    rows: [
      { name: "Storage bucket", status: "done", seconds: 4 },
      { name: "Old-version cleanup rule", status: "done", seconds: 57 },
    ],
  };
  const [first, second] = plain(renderBlock(block, 0)).slice(2);
  assert.equal(first.indexOf("4s") + 1, second.indexOf("57s") + 2, "times end in the same column");
});
