import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { execa } from "execa";
import { deploymentDir } from "./paths.js";

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

// A program that ran and failed
export class CommandError extends Error {}

interface RunOptions {
  cwd?: string;
  // false for output with secrets
  logStdout?: boolean;
  // Exit codes besides 0 that aren't failures
  okExitCodes?: number[];
}

// Runs a program and logs its output; returns stdout and exit code
export async function runWithExitCode(
  log: Log,
  file: string,
  args: string[],
  options: RunOptions = {},
): Promise<{ stdout: string; exitCode: number }> {
  const { cwd, logStdout = true, okExitCodes = [] } = options;
  log.write(`\n$ ${file} ${args.join(" ")}\n${cwd ? `  (in ${cwd})\n` : ""}`);

  const toLog = { file: log.path, append: true };
  const result = await execa(file, args, {
    cwd,
    reject: false,
    env: { TF_IN_AUTOMATION: "1" },
    stdout: logStdout ? ["pipe", toLog] : "pipe",
    stderr: ["pipe", toLog],
  });

  const exitCode = result.exitCode ?? -1;
  if (result.failed && !okExitCodes.includes(exitCode)) {
    throw new CommandError(
      `${result.shortMessage ?? `${file} failed`}\n\n${log.tail(20)}\n\nFull log: ${log.path}`,
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