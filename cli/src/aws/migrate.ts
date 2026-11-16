import { UserError } from "../core/errors.js";
import { DEPLOYMENT_NAME } from "../core/paths.js";
import { run, type Log } from "../core/run.js";
import type { MigrationOutcome, MigrationTarget } from "../types/tripwire.js";

export const MIGRATION_LOG_GROUP = `/ecs/${DEPLOYMENT_NAME}/tripwire`;

// The stack outputs the migration needs (infra/stack/outputs.tf)
export const migrationTarget = (outputs: Record<string, unknown>): MigrationTarget => {
  const subnets = Object.values((outputs.private_subnet_ids ?? {}) as Record<string, string>);
  const target = {
    cluster: String(outputs.ecs_cluster_name ?? ""),
    taskDefinition: String(outputs.tripwire_migrate_task_definition ?? ""),
    subnets,
    securityGroup: String(outputs.app_security_group_id ?? ""),
  };
  if (!target.cluster || !target.taskDefinition || subnets.length === 0 || !target.securityGroup) {
    throw new Error("The stack's outputs are missing what the migration task needs; apply the stack first.");
  }
  return target;
};

// environment: extra variables for this run (not secrets: arguments are logged)
export const runTaskArgs = (target: MigrationTarget, region: string, environment: Record<string, string> = {}): string[] => [
  "ecs", "run-task", "--region", region,
  "--cluster", target.cluster,
  "--task-definition", target.taskDefinition,
  "--launch-type", "FARGATE",
  "--count", "1",
  "--network-configuration",
  `awsvpcConfiguration={subnets=[${target.subnets.join(",")}],securityGroups=[${target.securityGroup}],assignPublicIp=DISABLED}`,
  ...(Object.keys(environment).length > 0
    ? ["--overrides", JSON.stringify({
        containerOverrides: [{
          name: "migrate",
          environment: Object.entries(environment).map(([name, value]) => ({ name, value })),
        }],
      })]
    : []),
  "--query", "tasks[0].taskArn", "--output", "text",
];

// The outcome from describe-tasks' JSON for one stopped task
export const outcomeFrom = (described: string, taskArn: string): MigrationOutcome => {
  const task = (JSON.parse(described) as { containers?: { exitCode?: number }[]; stoppedReason?: string }[])[0] ?? {};
  return {
    exitCode: task.containers?.[0]?.exitCode ?? null,
    stoppedReason: task.stoppedReason,
    taskId: taskArn.split("/").pop() ?? taskArn,
  };
};

// The migration's last log lines, to show why it failed
const migrationLogTail = async (log: Log, region: string, taskId: string): Promise<string> => {
  try {
    const out = await run(log, "aws", [
      "logs", "get-log-events", "--region", region,
      "--log-group-name", MIGRATION_LOG_GROUP,
      "--log-stream-name", `migrate/migrate/${taskId}`,
      "--limit", "15", "--query", "events[].message", "--output", "text",
    ]);
    return out.replace(/\t/g, "\n").trim();
  } catch {
    return "";
  }
};

// Runs the one-off migration task (tripwire/migrate.py) and waits for it to stop; throws if it failed
export const runMigration = async (
  log: Log,
  region: string,
  target: MigrationTarget,
  environment: Record<string, string> = {},
): Promise<void> => {
  const taskArn = (await run(log, "aws", runTaskArgs(target, region, environment))).trim();
  if (!taskArn || taskArn === "None") throw new Error("ECS didn't start the migration task.");

  // Polls every 6 s for up to 10 minutes; exits as soon as the task stops
  await run(log, "aws", ["ecs", "wait", "tasks-stopped", "--region", region, "--cluster", target.cluster, "--tasks", taskArn]);

  const described = await run(log, "aws", [
    "ecs", "describe-tasks", "--region", region, "--cluster", target.cluster, "--tasks", taskArn,
    "--query", "tasks[].{containers: containers[].{exitCode: exitCode}, stoppedReason: stoppedReason}",
    "--output", "json",
  ]);
  const outcome = outcomeFrom(described, taskArn);
  if (outcome.exitCode === 0) return;

  const tail = await migrationLogTail(log, region, outcome.taskId);
  const why = outcome.exitCode === null
    ? `It never ran: ${outcome.stoppedReason ?? "no reason given"}.`
    : `It exited with code ${outcome.exitCode}.`;
  throw new UserError(
    `Tripwire's database migration failed. ${why}` +
      (tail ? `\n\nLast log lines:\n${tail}` : "") +
      `\n\nFull logs: aws logs tail ${MIGRATION_LOG_GROUP} --region ${region} --since 1h` +
      "\nTripwire's services weren't changed. Fix it and run `tripwire deploy --lite` again.",
  );
};
