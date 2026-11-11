import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { deploymentDir } from "../core/paths.js";
import { CommandError, run, runWithExitCode, type Log } from "../core/run.js";
import { parseTerraformEvent } from "./tfEvents.js";
import { buildBackendHcl, buildTfvars } from "./tfvars.js";
import type { TripwireConfig } from "../types/config.js";
import type { Plan, Resource, Restore, StackName, State, TerraformEvent } from "../types/terraform.js";

export const STACKS: StackName[] = ["bootstrap", "persistent", "stack"];

// Resources whose replacement loses data
export const DATA_RESOURCES = [
  "aws_db_instance.main",
  "aws_ebs_volume.data",
  "aws_instance.data",
];

export function stackDir(stack: StackName): string {
  return join(deploymentDir(), stack);
}

export async function writeSettings(config: TripwireConfig, restore?: Restore): Promise<void> {
  for (const stack of STACKS) {
    const dir = stackDir(stack);
    await writeFile(join(dir, "terraform.tfvars"), buildTfvars(stack, config, restore));
    if (stack !== "bootstrap") {
      await writeFile(join(dir, "backend.hcl"), buildBackendHcl(config.accountId, config.region));
    }
  }
}

// onDownload is called once if init really downloads a provider (not from the cache)
export async function tfInit(log: Log, stack: StackName, onDownload?: () => void): Promise<void> {
  const args = ["init", "-input=false", "-no-color"];
  if (stack !== "bootstrap") args.push("-backend-config=backend.hcl");
  let downloading = false;
  const onLine = onDownload
    ? (line: string) => {
        if (!downloading && isDownloadLine(line)) {
          downloading = true;
          onDownload();
        }
        return line;
      }
    : undefined;
  await run(log, "terraform", args, { cwd: stackDir(stack), onLine });
}

// "- Installing hashicorp/aws v6.71.0..." means a download; cache hits say "Using … from the shared cache"
export function isDownloadLine(line: string): boolean {
  return line.startsWith("- Installing ");
}

// Saves a plan to tfplan; true if it has changes
export async function tfPlan(log: Log, stack: StackName, extra: string[] = []): Promise<boolean> {
  const { exitCode } = await runWithExitCode(
    log,
    "terraform",
    ["plan", "-input=false", "-no-color", "-detailed-exitcode", "-out=tfplan", ...extra],
    { cwd: stackDir(stack), okExitCodes: [2] },
  );
  return exitCode === 2;
}

// The saved plan as JSON; not logged, it holds secrets
export async function tfPlanJson(log: Log, stack: StackName): Promise<Plan> {
  const out = await run(log, "terraform", ["show", "-json", "tfplan"], {
    cwd: stackDir(stack),
    logStdout: false,
  });
  return JSON.parse(out) as Plan;
}

// Applies the saved plan. With onEvent, Terraform reports progress as JSON events; the log
// still gets Terraform's normal text, and a failure shows Terraform's own error.
export async function tfApply(
  log: Log,
  stack: StackName,
  onEvent?: (event: TerraformEvent) => void,
): Promise<void> {
  const errors: TerraformEvent[] = [];
  const onLine = onEvent
    ? (line: string) => {
        const event = parseTerraformEvent(line);
        if (!event) return line;
        if (event.type === "diagnostic" && event.severity === "error") errors.push(event);
        onEvent(event);
        return event.message;
      }
    : undefined;

  try {
    await run(log, "terraform", ["apply", "-input=false", "-no-color", ...(onEvent ? ["-json"] : []), "tfplan"], {
      cwd: stackDir(stack),
      onLine,
    });
  } catch (err) {
    if (err instanceof CommandError && errors.length > 0) {
      throw new CommandError(`${applyErrorMessage(errors)}\n\nFull log: ${log.path}`, err.stderr);
    }
    throw err;
  } finally {
    await discardPlan(stack);
  }
}

// Terraform's error diagnostics as one message: summary, then detail, per error
export function applyErrorMessage(errors: TerraformEvent[]): string {
  return errors
    .filter((event) => event.type === "diagnostic")
    .map((event) => (event.detail ? `${event.summary}\n${event.detail}` : event.summary))
    .join("\n\n");
}

// Plan files can hold secrets
export async function discardPlan(stack: StackName): Promise<void> {
  await rm(join(stackDir(stack), "tfplan"), { force: true });
}

// The current state as JSON; not logged, it holds secrets
export async function tfState(log: Log, stack: StackName): Promise<State> {
  const out = await run(log, "terraform", ["show", "-json"], {
    cwd: stackDir(stack),
    logStdout: false,
  });
  return JSON.parse(out) as State;
}

export async function tfOutputs(log: Log, stack: StackName): Promise<Record<string, unknown>> {
  const out = await run(log, "terraform", ["output", "-json"], {
    cwd: stackDir(stack),
    logStdout: false,
  });
  const raw = JSON.parse(out) as Record<string, { value: unknown }>;
  return Object.fromEntries(Object.entries(raw).map(([key, o]) => [key, o.value]));
}

export function findResource(state: State, address: string): Resource | undefined {
  return state.values?.root_module.resources?.find((r) => r.address === address);
}

// Data resources the plan deletes or replaces
export function dataLosses(plan: Plan): string[] {
  return (plan.resource_changes ?? [])
    .filter((c) => DATA_RESOURCES.includes(c.address) && c.change.actions.includes("delete"))
    .map((c) => c.address);
}