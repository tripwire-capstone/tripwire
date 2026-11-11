import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { deploymentDir } from "./paths.js";
import type { TripwireConfig } from "../types/config.js";

export function configPath(): string {
  return join(deploymentDir(), "tripwire.json");
}

// null if init hasn't run
export async function readConfig(): Promise<TripwireConfig | null> {
  const path = configPath();
  if (!existsSync(path)) return null;
  return JSON.parse(await readFile(path, "utf8")) as TripwireConfig;
}

export async function writeConfig(config: TripwireConfig): Promise<void> {
  const path = configPath();
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(config, null, 2) + "\n");
}