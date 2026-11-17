import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { OLD_KEY_NAMES, removeOldKeys } from "./oldKeys.js";
import { Log } from "../core/run.js";

process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-old-keys-test-"));

const realPath = process.env.PATH;

// A fake `aws` that saves its arguments and prints what delete-parameters would answer
const fakeAws = (deleted: string): { dir: string; argsFile: string } => {
  const dir = mkdtempSync(join(tmpdir(), "tripwire-fake-aws-"));
  const argsFile = join(dir, "args");
  writeFileSync(join(dir, "aws"), ["#!/bin/sh", `echo "$@" > "${argsFile}"`, `printf '%s' '${deleted}'`, ""].join("\n"));
  chmodSync(join(dir, "aws"), 0o755);
  return { dir, argsFile };
};

const withPath = async <T>(dir: string, body: () => Promise<T>): Promise<T> => {
  process.env.PATH = `${dir}:/bin:/usr/bin`;
  try {
    return await body();
  } finally {
    process.env.PATH = realPath;
  }
};

test("OLD_KEY_NAMES are the two names the old deploy stored the keys under", () => {
  assert.deepEqual(OLD_KEY_NAMES, ["/tripwire/tripwire/openai_api_key", "/tripwire/tripwire/laminar_api_key"]);
});

test("removeOldKeys deletes both names in one call and returns the ones that existed", async () => {
  const aws = fakeAws("/tripwire/tripwire/openai_api_key\t/tripwire/tripwire/laminar_api_key\n");
  const deleted = await withPath(aws.dir, () => removeOldKeys(new Log("test"), "us-east-1"));
  assert.deepEqual(deleted, OLD_KEY_NAMES);
  assert.equal(
    readFileSync(aws.argsFile, "utf8").trim(),
    `ssm delete-parameters --region us-east-1 --names ${OLD_KEY_NAMES.join(" ")} --query DeletedParameters --output text`,
  );
});

test("removeOldKeys returns nothing when the keys are already gone", async () => {
  const aws = fakeAws("\n");
  assert.deepEqual(await withPath(aws.dir, () => removeOldKeys(new Log("test"), "us-east-1")), []);
});
