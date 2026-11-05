import { test } from "node:test";
import assert from "node:assert/strict";
import { sameNameServers } from "./dns.js";
import { dataLosses, findResource, type Plan, type State } from "./terraform.js";

function plan(changes: Record<string, string[]>): Plan {
  return {
    resource_changes: Object.entries(changes).map(([address, actions]) => ({
      address,
      change: { actions },
    })),
  };
}

test("replacing the database or data volume is a data loss", () => {
  const losses = dataLosses(
    plan({
      "aws_db_instance.main": ["delete", "create"],
      "aws_ebs_volume.data": ["create", "delete"],
      "aws_ecs_service.frontend": ["delete", "create"],
    }),
  );
  assert.deepEqual(losses, ["aws_db_instance.main", "aws_ebs_volume.data"]);
});

test("updating in place or creating is not", () => {
  const losses = dataLosses(
    plan({
      "aws_db_instance.main": ["update"],
      "aws_ebs_volume.data": ["create"],
      "aws_instance.data": ["no-op"],
    }),
  );
  assert.deepEqual(losses, []);
  assert.deepEqual(dataLosses({}), []);
});

test("finds a resource in state, or nothing in an empty state", () => {
  const state: State = {
    values: {
      root_module: {
        resources: [{ address: "aws_route53_zone.main[0]", values: { name_servers: ["ns-1"] } }],
      },
    },
  };
  assert.deepEqual(findResource(state, "aws_route53_zone.main[0]")?.values.name_servers, ["ns-1"]);
  assert.equal(findResource(state, "aws_acm_certificate.main"), undefined);
  assert.equal(findResource({}, "aws_route53_zone.main[0]"), undefined);
});

test("name servers match ignoring order, case and trailing dots", () => {
  const zone = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.com"];
  assert.equal(sameNameServers(["NS-2.awsdns-02.com.", "ns-1.awsdns-01.org"], zone), true);
  assert.equal(sameNameServers(["ns-9.other.net", "ns-2.awsdns-02.com"], zone), false);
  assert.equal(sameNameServers([], []), false);
});