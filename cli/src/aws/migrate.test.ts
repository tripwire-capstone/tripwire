import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { migrationTarget, outcomeFrom, runMigration, runTaskArgs } from "./migrate.js";
import { UserError } from "../core/errors.js";
import { Log } from "../core/run.js";
import type { MigrationTarget } from "../types/tripwire.js";

process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-migrate-test-"));

const realPath = process.env.PATH;

const target: MigrationTarget = {
  cluster: "tripwire",
  taskDefinition: "arn:aws:ecs:us-east-1:123456789012:task-definition/tripwire-tripwire-migrate:3",
  subnets: ["subnet-a", "subnet-b"],
  securityGroup: "sg-app",
};

const outputs = {
  ecs_cluster_name: "tripwire",
  tripwire_migrate_task_definition: target.taskDefinition,
  private_subnet_ids: { "us-east-1a": "subnet-a", "us-east-1b": "subnet-b" },
  app_security_group_id: "sg-app",
};

// A fake `aws` answering run-task, wait and describe-tasks, with the migration's exit code
const fakeAws = (describeTasks: string): string => {
  const dir = mkdtempSync(join(tmpdir(), "tripwire-fake-aws-"));
  writeFileSync(join(dir, "aws"), [
    "#!/bin/sh",
    'case "$1 $2" in',
    '  "ecs run-task") echo "arn:aws:ecs:us-east-1:123456789012:task/tripwire/abc123" ;;',
    '  "ecs wait") ;;',
    `  "ecs describe-tasks") echo '${describeTasks}' ;;`,
    '  "logs get-log-events") printf "applied 001.sql\\tmigration failed" ;;',
    "esac",
    "",
  ].join("\n"));
  chmodSync(join(dir, "aws"), 0o755);
  return dir;
};

const withPath = async (dir: string, run: () => Promise<void>): Promise<void> => {
  process.env.PATH = `${dir}:/bin:/usr/bin`;
  try {
    await run();
  } finally {
    process.env.PATH = realPath;
  }
};

test("migrationTarget reads the stack's outputs", () => {
  assert.deepEqual(migrationTarget(outputs), target);
});

test("migrationTarget refuses outputs from a stack without Tripwire", () => {
  assert.throws(() => migrationTarget({ ecs_cluster_name: "tripwire" }), /apply the stack first/);
});

test("runTaskArgs runs one Fargate task in the private subnets without a public IP", () => {
  const args = runTaskArgs(target, "us-east-1");
  assert.ok(args.includes("FARGATE"));
  assert.ok(args.includes(target.taskDefinition));
  assert.ok(args.includes("awsvpcConfiguration={subnets=[subnet-a,subnet-b],securityGroups=[sg-app],assignPublicIp=DISABLED}"));
  assert.ok(!args.includes("--overrides"));
});

test("runTaskArgs passes extra settings as container overrides", () => {
  const args = runTaskArgs(target, "us-east-1", { LMNR_PROJECT_ID: "42fc1e36-6c0e-4a69-9850-b0e849bb4656" });
  const overrides = JSON.parse(args[args.indexOf("--overrides") + 1]);
  assert.deepEqual(overrides, {
    containerOverrides: [{
      name: "migrate",
      environment: [{ name: "LMNR_PROJECT_ID", value: "42fc1e36-6c0e-4a69-9850-b0e849bb4656" }],
    }],
  });
});

test("outcomeFrom reads the exit code, or why the task never ran", () => {
  assert.deepEqual(outcomeFrom('[{"containers":[{"exitCode":0}]}]', "arn:aws:ecs:x:1:task/tripwire/abc"), {
    exitCode: 0, stoppedReason: undefined, taskId: "abc",
  });
  assert.deepEqual(
    outcomeFrom('[{"containers":[{}],"stoppedReason":"CannotPullContainerError"}]', "arn:aws:ecs:x:1:task/tripwire/abc"),
    { exitCode: null, stoppedReason: "CannotPullContainerError", taskId: "abc" },
  );
});

test("runMigration succeeds when the task exits 0", async () => {
  await withPath(fakeAws('[{"containers":[{"exitCode":0}]}]'), () => runMigration(new Log("test"), "us-east-1", target));
});

test("runMigration fails with the exit code and the last log lines", async () => {
  await withPath(fakeAws('[{"containers":[{"exitCode":1}]}]'), async () => {
    await assert.rejects(runMigration(new Log("test"), "us-east-1", target), (error: unknown) => {
      assert.ok(error instanceof UserError);
      assert.match(error.message, /exited with code 1/);
      assert.match(error.message, /applied 001.sql\nmigration failed/);
      assert.match(error.message, /services weren't changed/);
      return true;
    });
  });
});

test("runMigration explains a task that never ran", async () => {
  await withPath(fakeAws('[{"containers":[{}],"stoppedReason":"ResourceInitializationError: unable to pull secrets"}]'), async () => {
    await assert.rejects(runMigration(new Log("test"), "us-east-1", target), /never ran: ResourceInitializationError/);
  });
});
