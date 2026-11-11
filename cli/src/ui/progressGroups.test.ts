import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { bundledInfraDir } from "../core/paths.js";
import {
  applyEvent,
  finishProgress,
  groupFor,
  groupSeconds,
  isLongerThanUsual,
  isNoLine,
  startProgress,
  statusOf,
} from "./progressGroups.js";
import type { Plan, ResourceAction, TerraformEvent } from "../types/terraform.js";

// A plan creating these addresses (type taken from the address)
function planOf(addresses: string[], actions = ["create"]): Plan {
  return {
    resource_changes: addresses.map((address) => ({
      address,
      type: address.split(".")[0],
      change: { actions },
    })),
  };
}

function hook(
  type: "apply_start" | "apply_complete" | "apply_errored",
  address: string,
  action: ResourceAction = "create",
): TerraformEvent {
  const base = { message: "", address, resourceType: address.split(".")[0], action };
  return type === "apply_complete" ? { type, elapsedSeconds: 0, ...base } : { type, ...base };
}

function named(progress: ReturnType<typeof startProgress>, name: string) {
  const group = progress.groups.find((candidate) => candidate.name === name);
  assert.ok(group, `no group ${name}`);
  return group;
}

// Every resource type declared in one infra folder
function resourceTypes(folder: string): Set<string> {
  const dir = join(bundledInfraDir(), folder);
  const types = new Set<string>();
  for (const file of readdirSync(dir).filter((name) => name.endsWith(".tf"))) {
    for (const match of readFileSync(join(dir, file), "utf8").matchAll(/^resource "([a-z0-9_]+)"/gm)) {
      types.add(match[1]);
    }
  }
  return types;
}

test("every resource type in each infra folder is deliberately grouped or has no line", () => {
  for (const stack of ["bootstrap", "persistent", "stack"] as const) {
    const types = resourceTypes(stack);
    assert.ok(types.size > 0, `expected to find ${stack}'s resources`);
    const unplaced = [...types].filter((type) => !groupFor(type, stack) && !isNoLine(type, stack));
    assert.deepEqual(unplaced, [], `${stack} has resource types with no group`);
  }
});

test("groupFor matches exact types and prefixes", () => {
  assert.equal(groupFor("aws_db_instance")?.name, "Database");
  assert.equal(groupFor("aws_cognito_user_pool_client")?.name, "Login page");
  assert.equal(groupFor("aws_vpc_security_group_ingress_rule")?.name, "Network");
  assert.equal(groupFor("aws_iam_role"), undefined);
  assert.equal(isNoLine("random_password"), true);
});

test("startProgress shows only groups the plan changes, in display order", () => {
  const plan: Plan = {
    resource_changes: [
      { address: "aws_ecs_service.frontend", type: "aws_ecs_service", change: { actions: ["update"] } },
      { address: "aws_vpc.main", type: "aws_vpc", change: { actions: ["no-op"] } },
      { address: "aws_iam_role.task", type: "aws_iam_role", change: { actions: ["create"] } },
      { address: "aws_db_instance.main", type: "aws_db_instance", change: { actions: ["delete", "create"] } },
    ],
  };
  const progress = startProgress(plan, "build", 0);
  assert.deepEqual(progress.groups.map((group) => group.name), ["Database", "Laminar app"]);
});

test("startProgress picks the note for the action", () => {
  const plan = planOf(["aws_db_instance.main", "aws_instance.data"]);
  assert.equal(named(startProgress(plan, "build", 0), "Database").note, "usually the slowest");
  assert.equal(named(startProgress(plan, "build", 0), "Data server").note, undefined);
  assert.equal(named(startProgress(plan, "remove", 0), "Data server").note, "taking a final snapshot");
});

test("groups run in parallel and finish when all their resources complete", () => {
  const progress = startProgress(planOf(["aws_vpc.main", "aws_subnet.private[0]", "aws_lb.main"]), "build", 0);
  const network = named(progress, "Network");
  const balancer = named(progress, "Load balancer");
  assert.equal(statusOf(network), "pending");

  applyEvent(progress, hook("apply_start", "aws_vpc.main"), 1000);
  applyEvent(progress, hook("apply_start", "aws_lb.main"), 2000);
  assert.equal(statusOf(network), "running");
  assert.equal(statusOf(balancer), "running");

  applyEvent(progress, hook("apply_complete", "aws_vpc.main"), 5000);
  assert.equal(statusOf(network), "waiting", "the subnet hasn't started yet");

  applyEvent(progress, hook("apply_start", "aws_subnet.private[0]"), 5000);
  applyEvent(progress, hook("apply_complete", "aws_subnet.private[0]"), 9000);
  assert.equal(statusOf(network), "done");
  assert.equal(groupSeconds(network, 60_000), 8);
  assert.equal(groupSeconds(balancer, 60_000), 58, "still running, so it counts to now");
});

test("a replaced resource reporting twice keeps its group running until the second finishes", () => {
  const progress = startProgress(planOf(["aws_lb_target_group.api"], ["create", "delete"]), "build", 0);
  const group = named(progress, "Load balancer");
  applyEvent(progress, hook("apply_start", "aws_lb_target_group.api"), 0);
  applyEvent(progress, hook("apply_complete", "aws_lb_target_group.api"), 1000);
  applyEvent(progress, hook("apply_start", "aws_lb_target_group.api", "delete"), 1000);
  assert.equal(statusOf(group), "running");
  applyEvent(progress, hook("apply_complete", "aws_lb_target_group.api", "delete"), 2000);
  assert.equal(statusOf(group), "done");
});

test("an errored resource or an error diagnostic about it fails its group", () => {
  const progress = startProgress(planOf(["aws_instance.data", "aws_db_instance.main"]), "build", 0);
  applyEvent(progress, hook("apply_start", "aws_instance.data"), 0);
  applyEvent(progress, hook("apply_errored", "aws_instance.data"), 1000);
  assert.equal(statusOf(named(progress, "Data server")), "failed");

  applyEvent(
    progress,
    { type: "diagnostic", message: "", severity: "error", summary: "x", detail: "", address: "aws_db_instance.main" },
    1000,
  );
  assert.equal(statusOf(named(progress, "Database")), "failed");
});

test("events for resources without a line are ignored", () => {
  const progress = startProgress(planOf(["aws_iam_role.task", "aws_vpc.main"]), "build", 0);
  applyEvent(progress, hook("apply_start", "aws_iam_role.task"), 0);
  assert.equal(statusOf(named(progress, "Network")), "pending");
});

test("finishProgress marks every group done after a successful apply", () => {
  const progress = startProgress(planOf(["aws_vpc.main", "aws_sns_topic.alerts"]), "build", 0);
  applyEvent(progress, hook("apply_start", "aws_vpc.main"), 0);
  finishProgress(progress, 4000);
  assert.deepEqual(progress.groups.map(statusOf), ["done", "done"]);
});

test("isLongerThanUsual compares the whole step's time with the usual time", () => {
  const progress = startProgress(planOf(["aws_vpc.main"]), "build", 0);
  assert.equal(isLongerThanUsual(progress, 14 * 60_000, 15 * 60), false);
  assert.equal(isLongerThanUsual(progress, 16 * 60_000, 15 * 60), true);
});

test("bootstrap shows the bucket and the slow cleanup rule with its note", () => {
  const plan = planOf([
    "aws_s3_bucket.state",
    "aws_s3_bucket_versioning.state",
    "aws_s3_bucket_lifecycle_configuration.state",
  ]);
  const progress = startProgress(plan, "build", 0, "bootstrap");
  assert.deepEqual(progress.groups.map((group) => group.name), ["Storage bucket", "Old-version cleanup rule"]);
  assert.equal(named(progress, "Old-version cleanup rule").note, "AWS takes about a minute to confirm this");
});

test("persistent shows secrets and the certificate, and the zone only when it's created", () => {
  const plan = planOf([
    "random_id.auth_secret",
    "aws_ssm_parameter.auth_secret",
    "aws_acm_certificate.main",
    'aws_route53_record.validation["laminar.example.com"]',
    "aws_acm_certificate_validation.main",
  ]);
  const progress = startProgress(plan, "build", 0, "persistent");
  assert.deepEqual(progress.groups.map((group) => group.name), ["Secrets", "HTTPS certificate"]);
  assert.equal(named(progress, "HTTPS certificate").note, "AWS verifying you own the domain, ~30 s");
  const withZone = startProgress(planOf(["aws_route53_zone.main[0]"]), "build", 0, "persistent");
  assert.deepEqual(withZone.groups.map((group) => group.name), ["DNS zone"]);
});

test("a group waits while nothing of its own runs, then runs again", () => {
  const progress = startProgress(planOf(["aws_cloudwatch_log_group.laminar", "aws_ecs_service.frontend"]), "build", 0);
  const app = named(progress, "Laminar app");
  applyEvent(progress, hook("apply_start", "aws_cloudwatch_log_group.laminar"), 0);
  assert.equal(statusOf(app), "running");
  applyEvent(progress, hook("apply_complete", "aws_cloudwatch_log_group.laminar"), 2000);
  assert.equal(statusOf(app), "waiting", "the ECS service waits for the database");
  applyEvent(progress, hook("apply_start", "aws_ecs_service.frontend"), 461_000);
  assert.equal(statusOf(app), "running");
  applyEvent(progress, hook("apply_complete", "aws_ecs_service.frontend"), 462_000);
  assert.equal(statusOf(app), "done");
});

test("groupSeconds counts only time spent working, not waiting", () => {
  const progress = startProgress(planOf(["aws_cloudwatch_log_group.laminar", "aws_ecs_service.frontend"]), "build", 0);
  const app = named(progress, "Laminar app");
  applyEvent(progress, hook("apply_start", "aws_cloudwatch_log_group.laminar"), 0);
  assert.equal(groupSeconds(app, 1500), 1.5, "counts up while running");
  applyEvent(progress, hook("apply_complete", "aws_cloudwatch_log_group.laminar"), 2000);
  assert.equal(groupSeconds(app, 300_000), 2, "frozen while waiting");
  applyEvent(progress, hook("apply_start", "aws_ecs_service.frontend"), 461_000);
  applyEvent(progress, hook("apply_complete", "aws_ecs_service.frontend"), 462_000);
  assert.equal(groupSeconds(app, 500_000), 3, "2 s, then a 7-minute wait, then 1 s");
});
