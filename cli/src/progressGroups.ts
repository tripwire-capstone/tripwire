import type { Plan } from "./terraform.js";
import type { TerraformEvent } from "./tfEvents.js";

export type ProgressAction = "build" | "remove";

interface GroupDef {
  name: string;
  // Resource types; a trailing * matches a prefix (aws_cognito_* → every Cognito type)
  types: string[];
  // Shown next to the group while it runs
  notes?: Partial<Record<ProgressAction, string>>;
}

// The groups a user sees, in display order (approved 2026-11-05)
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

function matches(pattern: string, resourceType: string): boolean {
  return pattern.endsWith("*") ? resourceType.startsWith(pattern.slice(0, -1)) : pattern === resourceType;
}

// The group a resource type is shown under; undefined for NO_LINE and unknown types
export function groupFor(resourceType: string): GroupDef | undefined {
  return GROUPS.find((group) => group.types.some((pattern) => matches(pattern, resourceType)));
}

export function isNoLine(resourceType: string): boolean {
  return NO_LINE.some((pattern) => matches(pattern, resourceType));
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
}

export type GroupStatus = "pending" | "running" | "done" | "failed";

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
export function startProgress(plan: Plan, action: ProgressAction, now: number): Progress {
  const groups = GROUPS.map((def) => ({
    name: def.name,
    note: def.notes?.[action],
    expected: new Set<string>(),
    running: new Set<string>(),
    completed: new Set<string>(),
    failed: false,
  }));
  for (const change of realChanges(plan)) {
    const def = groupFor(change.type);
    if (def) groups[GROUPS.indexOf(def)].expected.add(change.address);
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
    group.running.add(event.address);
    group.firstStart ??= now;
  } else if (event.type === "apply_complete") {
    // A replacement can report the same address twice; done only when nothing is still running
    group.running.delete(event.address);
    group.completed.add(event.address);
    group.lastEnd = now;
  } else {
    group.running.delete(event.address);
    group.failed = true;
    group.lastEnd = now;
  }
}

// After a successful apply everything is done, even if an event was missed
export function finishProgress(progress: Progress, now: number): void {
  for (const group of progress.groups) {
    group.running.clear();
    for (const address of group.expected) group.completed.add(address);
    group.firstStart ??= now;
    group.lastEnd ??= now;
  }
}

export function statusOf(group: GroupProgress): GroupStatus {
  if (group.failed) return "failed";
  const allCompleted = [...group.expected].every((address) => group.completed.has(address));
  if (allCompleted && group.running.size === 0) return "done";
  return group.firstStart === undefined ? "pending" : "running";
}

// Seconds the group has run, or ran; 0 if it hasn't started
export function groupSeconds(group: GroupProgress, now: number): number {
  if (group.firstStart === undefined) return 0;
  const end = statusOf(group) === "running" ? now : (group.lastEnd ?? now);
  return (end - group.firstStart) / 1000;
}

export function isLongerThanUsual(progress: Progress, now: number, usualSeconds: number): boolean {
  return (now - progress.startedAt) / 1000 > usualSeconds;
}
