import { applyEvent, finishProgress, startProgress } from "./progressGroups.js";
import { progressBlock, showProgress } from "./progressView.js";
import { CommandError, type Log } from "../core/run.js";
import { tfApply } from "../terraform/terraform.js";
import type { Block, LiveApplyOptions } from "../types/progress.js";
import type { Plan, StackName } from "../types/terraform.js";

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
