import { test } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { keyHint, putParameterInput, putSecret, secretExists, secretName } from "./secrets.js";
import { Log } from "../core/run.js";

// Logs go to a temp folder, not ~/.tripwire
process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-secrets-test-"));

const realPath = process.env.PATH;

// A fake `aws` that saves its arguments and, for put-parameter, the request file's contents, path and
// permissions; then prints `output`
const fakeAws = (output: string): { dir: string; argsFile: string; requestCopy: string; requestPath: string; modeFile: string } => {
  const dir = mkdtempSync(join(tmpdir(), "tripwire-fake-aws-"));
  const argsFile = join(dir, "args");
  const requestCopy = join(dir, "request-copy");
  const requestPath = join(dir, "request-path");
  const modeFile = join(dir, "request-mode");
  writeFileSync(join(dir, "aws"), [
    "#!/bin/sh",
    `echo "$@" > "${argsFile}"`,
    'for arg in "$@"; do case "$arg" in file://*)',
    '  path="${arg#file://}"',
    `  cat "$path" > "${requestCopy}"; echo "$path" > "${requestPath}"; stat -f %Lp "$path" > "${modeFile}" 2>/dev/null || stat -c %a "$path" > "${modeFile}"`,
    ";; esac; done",
    `echo "${output}"`,
    "",
  ].join("\n"));
  chmodSync(join(dir, "aws"), 0o755);
  return { dir, argsFile, requestCopy, requestPath, modeFile };
};

const withPath = async (dir: string, run: () => Promise<void>): Promise<void> => {
  process.env.PATH = `${dir}:/bin:/usr/bin`;
  try {
    await run();
  } finally {
    process.env.PATH = realPath;
  }
};

test("secretName matches the names infra/stack/tripwire.tf reads", () => {
  assert.equal(secretName("openai_api_key"), "/tripwire/tripwire/openai_api_key");
  assert.equal(secretName("laminar_api_key"), "/tripwire/tripwire/laminar_api_key");
});

test("keyHint keeps only the last 4 characters", () => {
  assert.equal(keyHint("sk-secret-abcd"), "abcd");
});

test("putParameterInput is an encrypted, overwriting put-parameter request", () => {
  assert.deepEqual(JSON.parse(putParameterInput("openai_api_key", "sk-secret")), {
    Name: "/tripwire/tripwire/openai_api_key",
    Value: "sk-secret",
    Type: "SecureString",
    Overwrite: true,
  });
});

test("putSecret passes the key in a private file, never as an argument or into the log", async () => {
  const fake = fakeAws("1");
  const log = new Log("test");
  await withPath(fake.dir, () => putSecret(log, "us-east-1", "openai_api_key", "sk-very-secret-1234"));
  assert.ok(!readFileSync(fake.argsFile, "utf8").includes("sk-very-secret-1234"));
  assert.ok(readFileSync(fake.requestCopy, "utf8").includes("sk-very-secret-1234"));
  assert.equal(readFileSync(fake.modeFile, "utf8").trim(), "600");
  assert.ok(!readFileSync(log.path, "utf8").includes("sk-very-secret-1234"));
});

test("putSecret deletes the request file afterwards", async () => {
  const fake = fakeAws("1");
  await withPath(fake.dir, () => putSecret(new Log("test"), "us-east-1", "openai_api_key", "sk-secret"));
  assert.equal(existsSync(readFileSync(fake.requestPath, "utf8").trim()), false);
});

test("secretExists is true when SSM lists the parameter", async () => {
  const fake = fakeAws("1");
  await withPath(fake.dir, async () => {
    assert.equal(await secretExists(new Log("test"), "us-east-1", "laminar_api_key"), true);
  });
  assert.match(readFileSync(fake.argsFile, "utf8"), /Values=\/tripwire\/tripwire\/laminar_api_key/);
});

test("secretExists is false when SSM lists nothing", async () => {
  const fake = fakeAws("0");
  await withPath(fake.dir, async () => {
    assert.equal(await secretExists(new Log("test"), "us-east-1", "laminar_api_key"), false);
  });
});
