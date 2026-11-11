import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  findExistingZone,
  formatAccount,
  getAccountLabel,
  isOwnZone,
  listUserZones,
  matchingZones,
  recordTypesAt,
  zoneNameServers,
} from "./aws.js";
import { isTerraformRecentEnough } from "./checks.js";
import { Log } from "../core/run.js";

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

// A folder holding a fake `aws`; answers maps "<service> <command>" to a shell snippet
function fakeAws(answers: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "tripwire-fake-aws-"));
  const script = join(dir, "aws");
  const cases = Object.entries(answers)
    .map(([command, snippet]) => `  "${command}") ${snippet} ;;`)
    .join("\n");
  writeFileSync(script, `#!/bin/sh\ncase "$1 $2" in\n${cases}\nesac\n`);
  chmodSync(script, 0o755);
  return dir;
}

// The shell snippet that prints value as JSON
function prints(value: unknown): string {
  return `echo '${JSON.stringify(value)}'`;
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
    "account get-account-information": prints({ AccountName: "Acme" }),
    "iam list-account-aliases": prints({ AccountAliases: ["acme-alias"] }),
  });
  await withPath(dir, async () => {
    assert.equal(await getAccountLabel(new Log("test")), "Acme");
  });
});

test("getAccountLabel falls back to the alias when the name is denied", async () => {
  const dir = fakeAws({
    "account get-account-information": DENIED,
    "iam list-account-aliases": prints({ AccountAliases: ["acme-alias"] }),
  });
  await withPath(dir, async () => {
    assert.equal(await getAccountLabel(new Log("test")), "acme-alias");
  });
});

test("getAccountLabel gives null when both are denied or there's no alias", async () => {
  await withPath(fakeAws({ "account get-account-information": DENIED, "iam list-account-aliases": DENIED }), async () => {
    assert.equal(await getAccountLabel(new Log("test")), null);
  });
  await withPath(fakeAws({
    "account get-account-information": DENIED,
    "iam list-account-aliases": prints({ AccountAliases: [] }),
  }), async () => {
    assert.equal(await getAccountLabel(new Log("test")), null);
  });
});

const OWN_TAGS = [
  { Key: "Project", Value: "tripwire" },
  { Key: "Stack", Value: "persistent" },
];

// A fake Route 53 with a user zone, a private zone and a zone Tripwire created
const route53 = {
  "route53 list-hosted-zones": prints({
    HostedZones: [
      { Id: "/hostedzone/ZUSER", Name: "example.com.", Config: { PrivateZone: false } },
      { Id: "/hostedzone/ZPRIV", Name: "tripwire.internal.", Config: { PrivateZone: true } },
      { Id: "/hostedzone/ZOWN", Name: "laminar.other.org.", Config: { PrivateZone: false } },
    ],
  }),
  "route53 list-tags-for-resources": prints({
    ResourceTagSets: [
      { ResourceId: "ZUSER", Tags: [{ Key: "Project", Value: "tripwire" }] },
      { ResourceId: "ZOWN", Tags: OWN_TAGS },
    ],
  }),
};

test("listUserZones lists public zones, leaving out the ones Tripwire created", async () => {
  await withPath(fakeAws(route53), async () => {
    assert.deepEqual(await listUserZones(new Log("test")), [{ id: "ZUSER", name: "example.com" }]);
  });
});

test("findExistingZone finds the parent zone, but not a zone Tripwire created", async () => {
  await withPath(fakeAws(route53), async () => {
    assert.deepEqual(await findExistingZone(new Log("test"), "laminar.example.com"), {
      id: "ZUSER",
      name: "example.com",
    });
    assert.equal(await findExistingZone(new Log("test"), "laminar.other.org"), null);
    assert.equal(await findExistingZone(new Log("test"), "laminar.io"), null);
  });
});

test("zoneNameServers reads the zone's delegation set", async () => {
  const servers = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.com"];
  const dir = fakeAws({ "route53 get-hosted-zone": prints({ DelegationSet: { NameServers: servers } }) });
  await withPath(dir, async () => {
    assert.deepEqual(await zoneNameServers(new Log("test"), "ZUSER"), servers);
  });
});

test("recordTypesAt reports only records that would clash with Laminar's", async () => {
  await withPath(fakeAws({ "route53 list-resource-record-sets": prints(["CNAME", "TXT"]) }), async () => {
    assert.deepEqual(await recordTypesAt(new Log("test"), "ZUSER", "laminar.example.com"), ["CNAME"]);
  });
  await withPath(fakeAws({ "route53 list-resource-record-sets": prints(["NS", "SOA"]) }), async () => {
    assert.deepEqual(await recordTypesAt(new Log("test"), "ZUSER", "example.com"), []);
  });
});
