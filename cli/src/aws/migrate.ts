import { UserError } from "../core/errors.js";
import { DEPLOYMENT_NAME } from "../core/paths.js";
import { run, type Log } from "../core/run.js";
import type { MigrationOutcome, MigrationTarget, MigrationWait } from "../types/tripwire.js";

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

// One task as describeArgs asks for it
interface DescribedTask {
  lastStatus?: string;
  containers?: { exitCode?: number | null }[];
  stoppedReason?: string;
}

const firstTask = (described: string): DescribedTask => (JSON.parse(described) as DescribedTask[])[0] ?? {};

// The outcome from describe-tasks' JSON for one stopped task
export const outcomeFrom = (described: string, taskArn: string): MigrationOutcome => {
  const task = firstTask(described);
  return {
    exitCode: task.containers?.[0]?.exitCode ?? null,
    stoppedReason: task.stoppedReason,
    taskId: taskArn.split("/").pop() ?? taskArn,
  };
};

// Finished = the migration's container has an exit code (the program ended), or the task stopped
// without one (it never ran). Doesn't wait for AWS to finish tearing the task down afterwards.
export const migrationFinished = (described: string): boolean => {
  const task = firstTask(described);
  return typeof task.containers?.[0]?.exitCode === "number" || task.lastStatus === "STOPPED";
};

const describeArgs = (region: string, cluster: string, taskArn: string): string[] => [
  "ecs", "describe-tasks", "--region", region, "--cluster", cluster, "--tasks", taskArn,
  "--query", "tasks[].{lastStatus: lastStatus, containers: containers[].{exitCode: exitCode}, stoppedReason: stoppedReason}",
  "--output", "json",
];

const MIGRATION_CHECK_MS = 5_000;
const MIGRATION_LIMIT_MS = 10 * 60_000;
const defaultSleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

// Asks ECS every 5 s whether the migration has finished; returns the last answer. Replaces
// `aws ecs wait tasks-stopped`, which waits for the whole task to be torn down and returned about
// 6 minutes after the migration ended on the Nov 17 deploy.
const waitForMigration = async (
  log: Log,
  region: string,
  cluster: string,
  taskArn: string,
  wait: MigrationWait,
): Promise<string> => {
  const { intervalMs = MIGRATION_CHECK_MS, limitMs = MIGRATION_LIMIT_MS, sleep = defaultSleep, now = Date.now } = wait;
  const startedAt = now();
  for (;;) {
    const described = await run(log, "aws", describeArgs(region, cluster, taskArn));
    if (migrationFinished(described)) return described;
    if (now() - startedAt >= limitMs) {
      throw new UserError(
        `Tripwire's database migration didn't finish within ${Math.round(limitMs / 60_000)} minutes.` +
          `\n\nCheck its logs: aws logs tail ${MIGRATION_LOG_GROUP} --region ${region} --since 1h` +
          "\nTripwire's services weren't changed. Run `tripwire deploy --lite` again once it's sorted.",
      );
    }
    await sleep(intervalMs);
  }
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

// Runs the one-off migration task (tripwire/migrate.py) and waits for it to finish; throws if it failed
export const runMigration = async (
  log: Log,
  region: string,
  target: MigrationTarget,
  environment: Record<string, string> = {},
  wait: MigrationWait = {},
): Promise<void> => {
  const taskArn = (await run(log, "aws", runTaskArgs(target, region, environment))).trim();
  if (!taskArn || taskArn === "None") throw new Error("ECS didn't start the migration task.");

  const described = await waitForMigration(log, region, target.cluster, taskArn, wait);
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
