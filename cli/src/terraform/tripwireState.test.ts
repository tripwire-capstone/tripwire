import { test } from "node:test";
import assert from "node:assert/strict";
import { servicesImageFromState, withServicesImage } from "./tripwireState.js";
import type { TripwireConfig } from "../types/config.js";
import type { State } from "../types/terraform.js";

const IMAGE = `ghcr.io/tripwire-capstone/tripwire-backend@sha256:${"e".repeat(64)}`;

const stateWith = (resources: { address: string; values: Record<string, unknown> }[]): State => ({
  values: { root_module: { resources } },
});

const taskDefinition = (service: string, image: string) => ({
  address: `aws_ecs_task_definition.tripwire["${service}"]`,
  values: { container_definitions: JSON.stringify([{ name: service, image }]) },
});

const config: TripwireConfig = {
  accountId: "123456789012",
  region: "us-east-1",
  domain: "laminar.example.com",
  zoneId: null,
  alertEmail: "ops@example.com",
  laminarUsers: ["ops@example.com"],
  freePlan: false,
};

test("servicesImageFromState reads the image Tripwire's services run", () => {
  const state = stateWith([
    { address: "aws_db_instance.main", values: {} },
    taskDefinition("api", IMAGE),
    taskDefinition("poller", IMAGE),
  ]);
  assert.equal(servicesImageFromState(state), IMAGE);
});

test("servicesImageFromState is undefined when the services don't exist", () => {
  const state = stateWith([
    { address: "aws_db_instance.main", values: {} },
    // The migration task exists from the first apply; it isn't one of the services
    { address: "aws_ecs_task_definition.tripwire_migrate", values: { container_definitions: JSON.stringify([{ image: IMAGE }]) } },
  ]);
  assert.equal(servicesImageFromState(state), undefined);
});

test("servicesImageFromState is undefined for an empty or unreadable state", () => {
  assert.equal(servicesImageFromState({}), undefined);
  assert.equal(servicesImageFromState(stateWith([])), undefined);
  assert.equal(
    servicesImageFromState(stateWith([{ address: 'aws_ecs_task_definition.tripwire["api"]', values: { container_definitions: "not json" } }])),
    undefined,
  );
});

test("withServicesImage replaces a stale local note with the stack's answer", () => {
  const stale = { ...config, tripwire: { servicesImage: "old" } };
  assert.deepEqual(withServicesImage(stale, undefined).tripwire, {});
  assert.deepEqual(withServicesImage(stale, IMAGE).tripwire, { servicesImage: IMAGE });
});

test("withServicesImage works when the config has no Tripwire settings yet", () => {
  assert.deepEqual(withServicesImage(config, undefined).tripwire, {});
  assert.deepEqual(withServicesImage(config, IMAGE).tripwire, { servicesImage: IMAGE });
});
