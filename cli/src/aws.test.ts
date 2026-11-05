import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { formatAccount, getAccountLabel, isOwnZone, matchingZones } from "./aws.js";
import { isTerraformRecentEnough } from "./checks.js";
import { Log } from "./run.js";

// Logs go to a temp folder, not ~/.tripwire
process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-aws-test-"));

const realPath = process.env.PATH;

// Runs fn with PATH set to only the given folder
async function withPath(dir: string, fn: () => Promise<void>): Promise<void> {
  process.env.PATH = dir;
  try {
    await fn();
  } finally {
    process.env.PATH = realPath;
  }
}

// A folder holding a fake `aws`; each answer is a shell snippet for `aws account …` / `aws iam …`
function fakeAws(answers: { account: string; iam: string }): string {
  const dir = mkdtempSync(join(tmpdir(), "tripwire-fake-aws-"));
  const script = join(dir, "aws");
  writeFileSync(
    script,
    `#!/bin/sh\ncase "$1" in\n  account) ${answers.account} ;;\n  iam) ${answers.iam} ;;\nesac\n`,
  );
  chmodSync(script, 0o755);
  return dir;
}

const DENIED = "echo 'AccessDenied' >&2; exit 254";

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

test("formatAccount puts the label in brackets, or leaves it out", () => {
  assert.equal(
    formatAccount("123456789012", "Acme", "user admin"),
    "AWS account 123456789012 (Acme), as user admin",
  );
  assert.equal(
    formatAccount("123456789012", null, "user admin"),
    "AWS account 123456789012, as user admin",
  );
});

test("getAccountLabel uses the account name when it can read it", async () => {
  const dir = fakeAws({
    account: `echo '{"AccountName": "Acme"}'`,
    iam: `echo '{"AccountAliases": ["acme-alias"]}'`,
  });
  await withPath(dir, async () => {
    assert.equal(await getAccountLabel(new Log("test")), "Acme");
  });
});

test("getAccountLabel falls back to the alias when the name is denied", async () => {
  const dir = fakeAws({ account: DENIED, iam: `echo '{"AccountAliases": ["acme-alias"]}'` });
  await withPath(dir, async () => {
    assert.equal(await getAccountLabel(new Log("test")), "acme-alias");
  });
});

test("getAccountLabel gives null when both are denied or there's no alias", async () => {
  await withPath(fakeAws({ account: DENIED, iam: DENIED }), async () => {
    assert.equal(await getAccountLabel(new Log("test")), null);
  });
  await withPath(fakeAws({ account: DENIED, iam: `echo '{"AccountAliases": []}'` }), async () => {
    assert.equal(await getAccountLabel(new Log("test")), null);
  });
});
