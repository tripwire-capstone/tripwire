import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checkTerraform, describeIdentity, getAccountId } from "./checks.js";
import { UserError } from "../core/errors.js";
import { Log } from "../core/run.js";

// Logs go to a temp folder, not ~/.tripwire
process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-checks-test-"));

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

// A folder holding a fake `aws` that prints message on stderr and fails
function fakeAws(message: string): string {
  const dir = mkdtempSync(join(tmpdir(), "tripwire-fake-aws-"));
  const script = join(dir, "aws");
  writeFileSync(script, `#!/bin/sh\necho "${message}" >&2\nexit 255\n`);
  chmodSync(script, 0o755);
  return dir;
}

test("getAccountId shows the AWS CLI's own message when the login fails", async () => {
  const expired = "Your session has expired. Please reauthenticate using 'aws login'.";
  await withPath(fakeAws(expired), async () => {
    await assert.rejects(getAccountId(new Log("test")), (err: unknown) => {
      assert.ok(err instanceof UserError);
      assert.ok(err.message.includes(expired));
      assert.ok(!err.message.includes("isn't installed"));
      return true;
    });
  });
});

test("getAccountId says the AWS CLI isn't installed when it's missing", async () => {
  const empty = mkdtempSync(join(tmpdir(), "tripwire-empty-path-"));
  await withPath(empty, async () => {
    await assert.rejects(getAccountId(new Log("test")), (err: unknown) => {
      assert.ok(err instanceof UserError);
      assert.match(err.message, /AWS CLI isn't installed/);
      return true;
    });
  });
});

test("checkTerraform says Terraform isn't installed when it's missing", async () => {
  const empty = mkdtempSync(join(tmpdir(), "tripwire-empty-path-"));
  await withPath(empty, async () => {
    await assert.rejects(checkTerraform(new Log("test")), (err: unknown) => {
      assert.ok(err instanceof UserError);
      assert.match(err.message, /Terraform isn't installed/);
      return true;
    });
  });
});

test("describeIdentity names an IAM user, even one with a path", () => {
  assert.deepEqual(describeIdentity("arn:aws:iam::123456789012:user/admin"), {
    text: "user admin",
    isRoot: false,
  });
  assert.equal(describeIdentity("arn:aws:iam::123456789012:user/team/ops/nestor").text, "user nestor");
});

test("describeIdentity names the role of an SSO or assumed-role login", () => {
  const arn = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_AdministratorAccess_ab12/nestor";
  assert.deepEqual(describeIdentity(arn), {
    text: "role AWSReservedSSO_AdministratorAccess_ab12",
    isRoot: false,
  });
});

test("describeIdentity flags the root user", () => {
  assert.deepEqual(describeIdentity("arn:aws:iam::123456789012:root"), {
    text: "the root user",
    isRoot: true,
  });
});

test("describeIdentity names a federated user", () => {
  assert.equal(
    describeIdentity("arn:aws:sts::123456789012:federated-user/nestor").text,
    "federated user nestor",
  );
});

test("describeIdentity falls back to the ARN for shapes it doesn't know", () => {
  const unknown = "arn:aws:sts::123456789012:something-new/x";
  assert.deepEqual(describeIdentity(unknown), { text: unknown, isRoot: false });
  assert.deepEqual(describeIdentity("not an arn"), { text: "not an arn", isRoot: false });
});
