import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBackendHcl, buildTfvars, hclValue } from "./tfvars.js";
import type { TripwireConfig } from "../types/config.js";

const config: TripwireConfig = {
  accountId: "123456789012",
  region: "us-east-1",
  domain: "laminar.example.com",
  zoneId: null,
  alertEmail: "ops@example.com",
  laminarUsers: ["ops@example.com", "dev@example.com"],
  freePlan: false,
};

test("bootstrap gets only name and region", () => {
  assert.equal(
    buildTfvars("bootstrap", config),
    '# Written by tripwire; edits are overwritten\nname = "tripwire"\nregion = "us-east-1"\n',
  );
});

test("persistent gets the domain, and zone_id only when set", () => {
  const without = buildTfvars("persistent", config);
  assert.match(without, /^domain = "laminar.example.com"$/m);
  assert.doesNotMatch(without, /zone_id/);

  const withZone = buildTfvars("persistent", { ...config, zoneId: "Z123" });
  assert.match(withZone, /^zone_id = "Z123"$/m);
});

test("stack gets alerts and logins", () => {
  const out = buildTfvars("stack", config);
  assert.match(out, /^alert_email = "ops@example.com"$/m);
  assert.match(out, /^laminar_users = \["ops@example.com", "dev@example.com"\]$/m);
});

test("free plan adds the two smaller settings, paid plan doesn't", () => {
  const paid = buildTfvars("stack", config);
  assert.doesNotMatch(paid, /db_backup_retention_days|data_instance_type/);

  const free = buildTfvars("stack", { ...config, freePlan: true });
  assert.match(free, /^db_backup_retention_days = 1$/m);
  assert.match(free, /^data_instance_type = "c7i-flex.large"$/m);
});

test("snapshot IDs appear only on a restore", () => {
  assert.doesNotMatch(buildTfvars("stack", config), /snapshot/);

  const out = buildTfvars("stack", config, {
    dbSnapshotId: "tripwire-db-final-abc",
    dataVolumeSnapshotId: "snap-0123",
  });
  assert.match(out, /^db_snapshot_identifier = "tripwire-db-final-abc"$/m);
  assert.match(out, /^data_volume_snapshot_id = "snap-0123"$/m);
});

test("values are valid Terraform literals", () => {
  assert.equal(hclValue(7), "7");
  assert.equal(hclValue([]), "[]");
  assert.equal(hclValue('say "hi"'), '"say \\"hi\\""');
  assert.equal(hclValue("${oops}"), '"$${oops}"');
  assert.equal(hclValue("%{oops}"), '"%%{oops}"');
});

test("backend.hcl points at the state bucket", () => {
  assert.equal(
    buildBackendHcl("123456789012", "us-east-1"),
    "# Written by tripwire; edits are overwritten\n" +
      'bucket = "tripwire-tfstate-123456789012-us-east-1"\nregion = "us-east-1"\n',
  );
});