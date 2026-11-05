import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
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

test("run: onLine sees each line as it arrives, and the log gets what it returns", async () => {
  const log = new Log("test");
  const seen: string[] = [];
  // Computed output, so the logged command line doesn't contain it
  const script = "for (const n of [1, 2]) console.log(JSON.stringify({ n }));";
  const stdout = await run(log, process.execPath, ["-e", script], {
    onLine: (line) => {
      seen.push(line);
      return `line ${JSON.parse(line).n}`;
    },
  });
  assert.deepEqual(seen, ['{"n":1}', '{"n":2}']);
  assert.equal(stdout, "line 1\nline 2");
  const logged = readFileSync(log.path, "utf8");
  assert.match(logged, /line 1\nline 2/);
  assert.doesNotMatch(logged, /"n":1/);
});

test("run: lines onLine drops are left out of the log", async () => {
  const log = new Log("test");
  const script = "console.log('ke' + 'ep'); console.log('dr' + 'op');";
  await run(log, process.execPath, ["-e", script], {
    onLine: (line) => (line === "drop" ? undefined : line),
  });
  const logged = readFileSync(log.path, "utf8");
  assert.match(logged, /^keep$/m);
  assert.doesNotMatch(logged, /^drop$/m);
});
