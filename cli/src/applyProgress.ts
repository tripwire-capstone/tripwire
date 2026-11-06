import { applyEvent, finishProgress, startProgress, type ProgressAction } from "./progressGroups.js";
import { progressBlock, showProgress, type Block } from "./progressView.js";
import { CommandError, type Log } from "./run.js";
import { tfApply, type Plan } from "./terraform.js";
import type { StackName } from "./tfvars.js";

export interface LiveApplyOptions {
  title: string;
  action: ProgressAction;
  // Whose group table to use
  stack: StackName;
  // e.g. "usually 8–10 minutes"
  estimate?: string;
  // After this long, a calm "taking longer than usual" line appears
  usualSeconds?: number;
  // Added to a failure's message, e.g. how to continue
  hint: string;
}

// Puts hint before the "Full log:" line of an error message, or at the end
export function withHint(message: string, hint: string): string {
  const marker = "\n\nFull log:";
  const at = message.lastIndexOf(marker);
  return at === -1 ? `${message}\n\n${hint}` : `${message.slice(0, at)}\n\n${hint}${message.slice(at + 1)}`;
}

// Applies the saved plan while showing its groups live
export async function applyWithProgress(
  log: Log,
  stack: StackName,
  plan: Plan,
  options: LiveApplyOptions,
): Promise<void> {
  const progress = startProgress(plan, options.action, Date.now(), options.stack);
  let status: Block["status"] = "running";
  const display = showProgress(() =>
    progressBlock(progress, {
      title: options.title,
      estimate: options.estimate,
      usualSeconds: options.usualSeconds,
      now: Date.now(),
      status,
    }),
  );

  try {
    await tfApply(log, stack, (event) => applyEvent(progress, event, Date.now()));
    finishProgress(progress, Date.now());
    status = "done";
  } catch (err) {
    status = "failed";
    if (err instanceof CommandError) throw new CommandError(withHint(err.message, options.hint), err.stderr);
    throw err;
  } finally {
    display.stop();
  }
}
