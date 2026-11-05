import { test } from "node:test";
import assert from "node:assert/strict";
import { isOwnZone, matchingZones } from "./aws.js";
import { isTerraformRecentEnough } from "./checks.js";

const zones = [
  { id: "Z1", name: "example.com" },
  { id: "Z2", name: "laminar.example.com" },
  { id: "Z3", name: "notexample.com" },
  { id: "Z4", name: "other.org" },
];

test("finds the domain's zone or a parent, closest first", () => {
  const ids = matchingZones(zones, "laminar.example.com").map((z) => z.id);
  assert.deepEqual(ids, ["Z2", "Z1"]);
});

test("ignores zones that only share the last letters", () => {
  const ids = matchingZones(zones, "app.notexample.com").map((z) => z.id);
  assert.deepEqual(ids, ["Z3"]);
  assert.deepEqual(matchingZones(zones, "laminar.io"), []);
});

test("knows the zone the persistent stack created", () => {
  const ours = [
    { Key: "Project", Value: "tripwire" },
    { Key: "Stack", Value: "persistent" },
  ];
  assert.equal(isOwnZone(ours), true);
  assert.equal(isOwnZone([{ Key: "Project", Value: "tripwire" }]), false);
  assert.equal(isOwnZone([]), false);
});

test("needs Terraform 1.10 or newer", () => {
  assert.equal(isTerraformRecentEnough("1.10.0"), true);
  assert.equal(isTerraformRecentEnough("1.17.1"), true);
  assert.equal(isTerraformRecentEnough("2.0.0"), true);
  assert.equal(isTerraformRecentEnough("1.9.8"), false);
  assert.equal(isTerraformRecentEnough("0.15.5"), false);
});