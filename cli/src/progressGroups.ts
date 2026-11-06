import type { Plan } from "./terraform.js";
import type { TerraformEvent } from "./tfEvents.js";
import type { StackName } from "./tfvars.js";

export type ProgressAction = "build" | "remove";

interface GroupDef {
  name: string;
  // Resource types; a trailing * matches a prefix (aws_cognito_* → every Cognito type)
  types: string[];
  // Shown next to the group while it runs
  notes?: Partial<Record<ProgressAction, string>>;
}

// The groups a user sees while the stack builds, in display order (approved 2026-11-05)
export const GROUPS: GroupDef[] = [
  {
    name: "Network",
    types: [
      "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_nat_gateway", "aws_eip",
      "aws_route_table", "aws_route", "aws_route_table_association", "aws_vpc_endpoint",
      "aws_security_group", "aws_vpc_security_group_*",
    ],
  },
  {
    name: "Database",
    types: ["aws_db_instance", "aws_db_subnet_group"],
    notes: { build: "usually the slowest", remove: "taking a final snapshot" },
  },
  {
    name: "Data server",
    types: ["aws_instance", "aws_ebs_volume", "aws_volume_attachment", "aws_iam_instance_profile"],
    notes: { remove: "taking a final snapshot" },
  },
  {
    name: "Load balancer",
    types: ["aws_lb", "aws_lb_listener", "aws_lb_listener_rule", "aws_lb_target_group", "aws_route53_record"],
  },
  { name: "Login page", types: ["aws_cognito_*"] },
  {
    name: "Laminar app",
    types: ["aws_ecs_*", "aws_service_discovery_*", "aws_cloudwatch_log_group"],
  },
  {
    name: "Backups and alerts",
    types: ["aws_dlm_lifecycle_policy", "aws_cloudwatch_metric_alarm", "aws_sns_*"],
  },
];

// Types that count toward the step but get no line: quick, and meaningless to a user
export const NO_LINE = ["aws_iam_role", "aws_iam_role_policy", "aws_iam_role_policy_attachment", "aws_ssm_parameter", "random_*"];

interface GroupTable {
  groups: GroupDef[];
  noLine: string[];
}

// Each project folder's groups (approved 2026-11-05)
export const GROUPS_BY_STACK: Record<StackName, GroupTable> = {
  bootstrap: {
    groups: [
      {
        name: "Storage bucket",
        types: [
          "aws_s3_bucket", "aws_s3_bucket_versioning", "aws_s3_bucket_server_side_encryption_configuration",
          "aws_s3_bucket_public_access_block", "aws_s3_bucket_policy",
        ],
      },
      {
        name: "Old-version cleanup rule",
        types: ["aws_s3_bucket_lifecycle_configuration"],
        notes: { build: "AWS takes about a minute to confirm this" },
      },
    ],
    noLine: [],
  },
  persistent: {
    groups: [
      { name: "DNS zone", types: ["aws_route53_zone"] },
      { name: "Secrets", types: ["random_id", "aws_ssm_parameter"] },
      {
        name: "HTTPS certificate",
        types: ["aws_acm_certificate", "aws_acm_certificate_validation", "aws_route53_record"],
        notes: { build: "AWS verifying you own the domain, ~30 s" },
      },
    ],
    noLine: [],
  },
  stack: { groups: GROUPS, noLine: NO_LINE },
};

function matches(pattern: string, resourceType: string): boolean {
  return pattern.endsWith("*") ? resourceType.startsWith(pattern.slice(0, -1)) : pattern === resourceType;
}

// The group a resource type is shown under; undefined for no-line and unknown types
export function groupFor(resourceType: string, stack: StackName = "stack"): GroupDef | undefined {
  return GROUPS_BY_STACK[stack].groups.find((group) => group.types.some((pattern) => matches(pattern, resourceType)));
}

export function isNoLine(resourceType: string, stack: StackName = "stack"): boolean {
  return GROUPS_BY_STACK[stack].noLine.some((pattern) => matches(pattern, resourceType));
}

export interface GroupProgress {
  name: string;
  note?: string;
  expected: Set<string>;
  running: Set<string>;
  completed: Set<string>;
  failed: boolean;
  // Milliseconds; first resource started, last one finished
  firstStart?: number;
  lastEnd?: number;
  // Time with at least one of its resources running; waiting on other groups doesn't count
  activeMs: number;
  runningSince?: number;
}

// waiting: started, but nothing of its own is being built right now (it waits on other parts)
export type GroupStatus = "pending" | "running" | "waiting" | "done" | "failed";

export interface Progress {
  action: ProgressAction;
  startedAt: number;
  groups: GroupProgress[];
}

// Changes that do something (Terraform lists unchanged resources as no-op, data sources as read)
function realChanges(plan: Plan): { address: string; type: string }[] {
  return (plan.resource_changes ?? [])
    .filter((change) => change.change.actions.some((action) => action !== "no-op" && action !== "read"))
    .map((change) => ({ address: change.address, type: change.type ?? change.address.split(".")[0] }));
}

// The groups this plan touches, in display order; groups with nothing to do aren't shown
export function startProgress(
  plan: Plan,
  action: ProgressAction,
  now: number,
  stack: StackName = "stack",
): Progress {
  const defs = GROUPS_BY_STACK[stack].groups;
  const groups: GroupProgress[] = defs.map((def) => ({
    name: def.name,
    note: def.notes?.[action],
    expected: new Set<string>(),
    running: new Set<string>(),
    completed: new Set<string>(),
    failed: false,
    activeMs: 0,
  }));
  for (const change of realChanges(plan)) {
    const def = groupFor(change.type, stack);
    if (def) groups[defs.indexOf(def)].expected.add(change.address);
  }
  return { action, startedAt: now, groups: groups.filter((group) => group.expected.size > 0) };
}

function groupOf(progress: Progress, address: string): GroupProgress | undefined {
  return progress.groups.find((group) => group.expected.has(address));
}

// Updates progress with one Terraform event; events for unshown resources are ignored
export function applyEvent(progress: Progress, event: TerraformEvent, now: number): void {
  if (event.type === "diagnostic") {
    if (event.severity === "error" && event.address) {
      const group = groupOf(progress, event.address);
      if (group) group.failed = true;
    }
    return;
  }
  if (event.type !== "apply_start" && event.type !== "apply_complete" && event.type !== "apply_errored") {
    return;
  }
  const group = groupOf(progress, event.address);
  if (!group) return;

  if (event.type === "apply_start") {
    if (group.running.size === 0) group.runningSince = now;
    group.running.add(event.address);
    group.firstStart ??= now;
    return;
  }
  // A replacement can report the same address twice; done only when nothing is still running
  group.running.delete(event.address);
  if (event.type === "apply_complete") group.completed.add(event.address);
  else group.failed = true;
  group.lastEnd = now;
  stopClockIfIdle(group, now);
}

// Adds the time just spent working once none of the group's resources are running
function stopClockIfIdle(group: GroupProgress, now: number): void {
  if (group.running.size > 0 || group.runningSince === undefined) return;
  group.activeMs += now - group.runningSince;
  group.runningSince = undefined;
}

// After a successful apply everything is done, even if an event was missed
export function finishProgress(progress: Progress, now: number): void {
  for (const group of progress.groups) {
    group.running.clear();
    stopClockIfIdle(group, now);
    for (const address of group.expected) group.completed.add(address);
    group.firstStart ??= now;
    group.lastEnd ??= now;
  }
}

export function statusOf(group: GroupProgress): GroupStatus {
  if (group.failed) return "failed";
  const allCompleted = [...group.expected].every((address) => group.completed.has(address));
  if (allCompleted && group.running.size === 0) return "done";
  if (group.firstStart === undefined) return "pending";
  return group.running.size > 0 ? "running" : "waiting";
}

// Seconds the group spent working (not waiting); counts up while it runs
export function groupSeconds(group: GroupProgress, now: number): number {
  const current = group.runningSince === undefined ? 0 : now - group.runningSince;
  return (group.activeMs + current) / 1000;
}

export function isLongerThanUsual(progress: Progress, now: number, usualSeconds: number): boolean {
  return (now - progress.startedAt) / 1000 > usualSeconds;
}
