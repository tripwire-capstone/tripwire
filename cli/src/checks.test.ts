import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checkTerraform, getAccountId } from "./checks.js";
import { UserError } from "./errors.js";
import { Log } from "./run.js";

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
