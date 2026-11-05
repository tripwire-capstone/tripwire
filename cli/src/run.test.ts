import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CommandError, ProgramNotFoundError, Log, run } from "./run.js";

// Logs go to a temp folder, not ~/.tripwire
process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-run-test-"));

test("run: a program that isn't installed throws ProgramNotFoundError", async () => {
  const log = new Log("test");
  await assert.rejects(
    run(log, "tripwire-no-such-program", []),
    (err: unknown) => {
      assert.ok(err instanceof ProgramNotFoundError);
      assert.match(err.message, /isn't installed/);
      return true;
    },
  );
});

test("run: a program that ran and failed throws CommandError with its stderr", async () => {
  const log = new Log("test");
  await assert.rejects(
    run(log, process.execPath, ["-e", "console.error('boom'); process.exit(3)"]),
    (err: unknown) => {
      assert.ok(err instanceof CommandError);
      assert.ok(!(err instanceof ProgramNotFoundError));
      assert.equal(err.stderr, "boom");
      return true;
    },
  );
});

test("run: a missing cwd is not reported as a missing program", async () => {
  const log = new Log("test");
  await assert.rejects(
    run(log, process.execPath, ["-e", ""], { cwd: join(tmpdir(), "tripwire-no-such-dir") }),
    (err: unknown) => {
      assert.ok(err instanceof CommandError);
      assert.ok(!(err instanceof ProgramNotFoundError));
      return true;
    },
  );
});
