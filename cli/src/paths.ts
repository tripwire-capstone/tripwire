import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// One deployment per AWS account
export const DEPLOYMENT_NAME = "tripwire";

// The cli/ folder
const cliRoot = fileURLToPath(new URL("..", import.meta.url));

// TRIPWIRE_HOME overrides ~/.tripwire
export function tripwireHome(): string {
  return process.env.TRIPWIRE_HOME ?? join(homedir(), ".tripwire");
}

export function deploymentDir(): string {
  return join(tripwireHome(), DEPLOYMENT_NAME);
}

// Packaged infra, or the repo's infra in dev
export function bundledInfraDir(): string {
  const packaged = join(cliRoot, "infra");
  if (existsSync(packaged)) return packaged;

  const repo = join(cliRoot, "..", "infra");
  if (existsSync(repo)) return repo;

  throw new Error(`Can't find the Terraform code: looked in ${packaged} and ${repo}`);
}