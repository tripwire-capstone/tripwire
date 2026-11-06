import { test } from "node:test";
import assert from "node:assert/strict";
import { sameNameServers } from "./dns.js";
import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Log } from "./run.js";
import {
  applyErrorMessage,
  dataLosses,
  findResource,
  isDownloadLine,
  stackDir,
  tfInit,
  type Plan,
  type State,
} from "./terraform.js";

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
test("applyErrorMessage shows each Terraform error's summary and detail", () => {
  const message = applyErrorMessage([
    {
      type: "diagnostic",
      message: "",
      severity: "error",
      summary: "creating EC2 Instance",
      detail: "InsufficientInstanceCapacity: not enough t4g.medium capacity",
      address: "aws_instance.data",
    },
    { type: "diagnostic", message: "", severity: "error", summary: "second error", detail: "" },
  ]);
  assert.equal(
    message,
    "creating EC2 Instance\nInsufficientInstanceCapacity: not enough t4g.medium capacity\n\nsecond error",
  );
});

test("isDownloadLine is true only when init really downloads", () => {
  assert.equal(isDownloadLine("- Installing hashicorp/aws v6.71.0..."), true);
  assert.equal(isDownloadLine("- Using hashicorp/aws v6.71.0 from the shared cache directory"), false);
  assert.equal(isDownloadLine("- Using previously-installed hashicorp/aws v6.71.0"), false);
  assert.equal(isDownloadLine("- Installed hashicorp/aws v6.71.0 (signed by HashiCorp)"), false);
});

// Runs tfInit against a fake terraform that prints lines
async function initWith(lines: string[]): Promise<number> {
  process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-init-test-"));
  delete process.env.TF_PLUGIN_CACHE_DIR;
  process.env.TF_CLI_CONFIG_FILE = join(tmpdir(), "tripwire-no-such-terraformrc");
  mkdirSync(stackDir("stack"), { recursive: true });
  const bin = mkdtempSync(join(tmpdir(), "tripwire-fake-terraform-"));
  const script = join(bin, "terraform");
  writeFileSync(script, `#!/bin/sh\n${lines.map((line) => `echo '${line}'`).join("\n")}\n`);
  chmodSync(script, 0o755);
  const realPath = process.env.PATH;
  process.env.PATH = bin;
  let calls = 0;
  try {
    await tfInit(new Log("test"), "stack", () => calls++);
  } finally {
    process.env.PATH = realPath;
  }
  return calls;
}

test("tfInit reports a download once, however many providers download", async () => {
  const calls = await initWith([
    "Initializing provider plugins...",
    "- Installing hashicorp/aws v6.71.0...",
    "- Installing hashicorp/random v3.9.1...",
  ]);
  assert.equal(calls, 1);
});

test("tfInit reports nothing when every provider comes from the cache", async () => {
  const calls = await initWith([
    "- Using hashicorp/aws v6.71.0 from the shared cache directory",
    "- Using previously-installed hashicorp/random v3.9.1",
  ]);
  assert.equal(calls, 0);
});
