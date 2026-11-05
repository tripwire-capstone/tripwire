// Terraform's shared plugin cache: each provider version is downloaded once and linked into
// every project folder (bootstrap, persistent, stack), instead of downloaded per folder.
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { deploymentDir, tripwireHome } from "./paths.js";

export function pluginCacheDir(): string {
  return join(tripwireHome(), "plugin-cache");
}

// Where Terraform reads its CLI config file from
function cliConfigPath(env: NodeJS.ProcessEnv): string {
  if (env.TF_CLI_CONFIG_FILE) return env.TF_CLI_CONFIG_FILE;
  if (process.platform === "win32" && env.APPDATA) return join(env.APPDATA, "terraform.rc");
  return join(homedir(), ".terraformrc");
}

function readCliConfig(env: NodeJS.ProcessEnv): string {
  const path = cliConfigPath(env);
  return existsSync(path) ? readFileSync(path, "utf8") : "";
}

// Whether the user already set up a cache; ours would override theirs, so we then stay out
export function userHasOwnCache(env: NodeJS.ProcessEnv, configText: string): boolean {
  if (env.TF_PLUGIN_CACHE_DIR) return true;
  // A plugin_cache_dir setting that isn't commented out
  return /^\s*plugin_cache_dir\s*=/m.test(configText);
}

// The env vars Terraform needs to use our cache; {} when the user has their own.
// If the cache folder is missing (first run, or someone deleted it), the providers already
// linked into project folders would point at nothing, so those links are removed first;
// the next terraform init reinstalls them.
export function preparePluginCache(env: NodeJS.ProcessEnv, configText: string): Record<string, string> {
  if (userHasOwnCache(env, configText)) return {};

  const cache = pluginCacheDir();
  if (!existsSync(cache)) {
    const deployment = deploymentDir();
    if (existsSync(deployment)) {
      for (const entry of readdirSync(deployment, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        // Only the providers subfolder: .terraform/terraform.tfstate (the backend) stays
        rmSync(join(deployment, entry.name, ".terraform", "providers"), { recursive: true, force: true });
      }
    }
    mkdirSync(cache, { recursive: true });
  }
  return { TF_PLUGIN_CACHE_DIR: cache };
}

let prepared: Record<string, string> | undefined;

// preparePluginCache for this process, done once
export function terraformEnv(): Record<string, string> {
  prepared ??= preparePluginCache(process.env, readCliConfig(process.env));
  return prepared;
}
