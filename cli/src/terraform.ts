import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { TripwireConfig } from "./config.js";
import { deploymentDir } from "./paths.js";
import { run, runWithExitCode, type Log } from "./run.js";
import { buildBackendHcl, buildTfvars, type Restore, type StackName } from "./tfvars.js";

export const STACKS: StackName[] = ["bootstrap", "persistent", "stack"];

// Resources whose replacement loses data
export const DATA_RESOURCES = [
  "aws_db_instance.main",
  "aws_ebs_volume.data",
  "aws_instance.data",
];

interface Resource {
  address: string;
  values: Record<string, unknown>;
}

export interface State {
  values?: { root_module: { resources?: Resource[] } };
}

export interface Plan {
  resource_changes?: { address: string; change: { actions: string[] } }[];
}

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

export async function tfInit(log: Log, stack: StackName): Promise<void> {
  const args = ["init", "-input=false", "-no-color"];
  if (stack !== "bootstrap") args.push("-backend-config=backend.hcl");
  await run(log, "terraform", args, { cwd: stackDir(stack) });
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

export async function tfApply(log: Log, stack: StackName): Promise<void> {
  try {
    await run(log, "terraform", ["apply", "-input=false", "-no-color", "tfplan"], {
      cwd: stackDir(stack),
    });
  } finally {
    await discardPlan(stack);
  }
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