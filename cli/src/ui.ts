import * as p from "@clack/prompts";

// The answer, or exit if the user pressed Ctrl-C
export function answer<T>(value: T): Exclude<T, symbol> {
  if (p.isCancel(value)) {
    p.cancel("Cancelled, nothing changed.");
    process.exit(0);
  }
  return value as Exclude<T, symbol>;
}

// Runs fn behind a spinner
export async function step<T>(
  message: string,
  fn: () => Promise<T>,
  done?: (result: T) => string,
): Promise<T> {
  const spin = p.spinner({ indicator: "timer" });
  spin.start(message);
  try {
    const result = await fn();
    spin.stop(done ? done(result) : message);
    return result;
  } catch (err) {
    spin.error(message);
    throw err;
  }
}