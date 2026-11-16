import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { execa } from "execa";
import { deploymentDir } from "./paths.js";
import { terraformEnv } from "../terraform/pluginCache.js";
import type { RunOptions } from "../types/run.js";

// One log file per command run
export class Log {
  readonly path: string;

  constructor(command: string) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    this.path = join(deploymentDir(), "logs", `${command}-${stamp}.log`);
    mkdirSync(dirname(this.path), { recursive: true });
  }

  write(text: string): void {
    appendFileSync(this.path, text);
  }

  // Last lines of the log
  tail(lines: number): string {
    return readFileSync(this.path, "utf8").trimEnd().split("\n").slice(-lines).join("\n");
  }
}

// A program that failed: it ran and exited with an error, or couldn't start
export class CommandError extends Error {
  // What the program printed on stderr, trimmed; empty if it never started
  readonly stderr: string;

  constructor(message: string, stderr = "") {
    super(message);
    this.stderr = stderr;
  }
}

// A program that couldn't start because it isn't installed or isn't on the PATH
export class ProgramNotFoundError extends CommandError {}

// Runs a program and logs its output; returns stdout and exit code
export async function runWithExitCode(
  log: Log,
  file: string,
  args: string[],
  options: RunOptions = {},
): Promise<{ stdout: string; exitCode: number }> {
  const { cwd, logStdout = true, okExitCodes = [], onLine } = options;
  log.write(`\n$ ${file} ${args.join(" ")}\n${cwd ? `  (in ${cwd})\n` : ""}`);

  const toLog = { file: log.path, append: true };
  // execa's file target gets the raw line even after a transform, so with onLine we log ourselves
  const stdout = onLine
    ? [
        function* (line: string) {
          const replacement = onLine(line);
          if (replacement === undefined) return;
          if (logStdout) log.write(`${replacement}\n`);
          yield replacement;
        },
        "pipe" as const,
      ]
    : logStdout
      ? (["pipe", toLog] as const)
      : ("pipe" as const);
  const result = await execa(file, args, {
    cwd,
    reject: false,
    env: { TF_IN_AUTOMATION: "1", ...(file === "terraform" ? terraformEnv() : {}) },
    stdout,
    stderr: ["pipe", toLog],
  });

  // A missing cwd also gives ENOENT; that's a bug here, not a missing program
  if (result.code === "ENOENT" && (!cwd || existsSync(cwd))) {
    throw new ProgramNotFoundError(`${file} isn't installed or isn't on your PATH.`);
  }

  const exitCode = result.exitCode ?? -1;
  if (result.failed && !okExitCodes.includes(exitCode)) {
    throw new CommandError(
      `${result.shortMessage ?? `${file} failed`}\n\n${log.tail(20)}\n\nFull log: ${log.path}`,
      String(result.stderr ?? "").trim(),
    );
  }
  return { stdout: result.stdout, exitCode };
}

// Runs a program and logs its output; returns stdout
export async function run(
  log: Log,
  file: string,
  args: string[],
  options: RunOptions = {},
): Promise<string> {
  return (await runWithExitCode(log, file, args, options)).stdout;
}