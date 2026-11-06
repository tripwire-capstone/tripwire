import * as p from "@clack/prompts";

// The answer, or exit if the user pressed Ctrl-C
export function answer<T>(value: T): Exclude<T, symbol> {
  if (p.isCancel(value)) {
    p.cancel("Cancelled, nothing changed.");
    process.exit(0);
  }
  return value as Exclude<T, symbol>;
}

// Runs fn behind a spinner; fn can change the spinner's text while it works
export async function step<T>(
  message: string,
  fn: (setMessage: (text: string) => void) => Promise<T>,
  done?: (result: T) => string,
): Promise<T> {
  const spin = p.spinner({ indicator: "timer" });
  spin.start(message);
  try {
    const result = await fn((text) => spin.message(text));
    spin.stop(done ? done(result) : message);
    return result;
  } catch (err) {
    spin.error(message);
    throw err;
  }
}
// Shown after a step's title while Terraform downloads providers
export function downloadingNote(title: string): string {
  return `${title} · downloading Terraform's AWS tools (first run only)`;
}
