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
}

// Runs a program and logs its output
export async function run(
  log: Log,
  file: string,
  args: string[],
  options: RunOptions = {},
): Promise<string> {
  const { cwd, logStdout = true } = options;
  log.write(`\n$ ${file} ${args.join(" ")}\n${cwd ? `  (in ${cwd})\n` : ""}`);

  const toLog = { file: log.path, append: true };
  const result = await execa(file, args, {
    cwd,
    reject: false,
    env: { TF_IN_AUTOMATION: "1" },
    stdout: logStdout ? ["pipe", toLog] : "pipe",
    stderr: ["pipe", toLog],
  });

  if (result.failed) {
    throw new CommandError(
      `${result.shortMessage ?? `${file} failed`}\n\n${log.tail(20)}\n\nFull log: ${log.path}`,
    );
  }
  return result.stdout;
}