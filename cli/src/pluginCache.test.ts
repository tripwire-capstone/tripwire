import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { deploymentDir } from "./paths.js";
import { pluginCacheDir, preparePluginCache, userHasOwnCache } from "./pluginCache.js";

// Each test gets its own Tripwire home
function freshHome(): void {
  process.env.TRIPWIRE_HOME = mkdtempSync(join(tmpdir(), "tripwire-cache-test-"));
}

// A deployment folder with linked providers and a backend config, as after terraform init
function projectFolder(name: string): { providers: string; backend: string } {
  const dotTerraform = join(deploymentDir(), name, ".terraform");
  const providers = join(dotTerraform, "providers");
  mkdirSync(join(providers, "registry.terraform.io"), { recursive: true });
  const backend = join(dotTerraform, "terraform.tfstate");
  writeFileSync(backend, "{}");
  return { providers, backend };
}

test("userHasOwnCache sees the env var or a plugin_cache_dir setting", () => {
  assert.equal(userHasOwnCache({ TF_PLUGIN_CACHE_DIR: "/my/cache" }, ""), true);
  assert.equal(userHasOwnCache({}, 'plugin_cache_dir = "$HOME/.terraform.d/plugin-cache"\n'), true);
  assert.equal(userHasOwnCache({}, '  plugin_cache_dir="/x"'), true);
  assert.equal(userHasOwnCache({}, ""), false);
  assert.equal(userHasOwnCache({}, '# plugin_cache_dir = "/x"\n// plugin_cache_dir = "/y"'), false);
});

test("preparePluginCache creates the cache under the Tripwire home and points Terraform at it", () => {
  freshHome();
  const env = preparePluginCache({}, "");
  assert.deepEqual(env, { TF_PLUGIN_CACHE_DIR: pluginCacheDir() });
  assert.equal(pluginCacheDir(), join(process.env.TRIPWIRE_HOME ?? "", "plugin-cache"));
  assert.equal(existsSync(pluginCacheDir()), true);
});

test("preparePluginCache leaves a user's own cache alone and creates nothing", () => {
  freshHome();
  assert.deepEqual(preparePluginCache({ TF_PLUGIN_CACHE_DIR: "/my/cache" }, ""), {});
  assert.deepEqual(preparePluginCache({}, 'plugin_cache_dir = "/x"'), {});
  assert.equal(existsSync(pluginCacheDir()), false);
});

test("preparePluginCache removes linked providers when the cache folder is missing, keeping the backend", () => {
  freshHome();
  const stack = projectFolder("stack");
  const persistent = projectFolder("persistent");
  preparePluginCache({}, "");
  assert.equal(existsSync(stack.providers), false);
  assert.equal(existsSync(persistent.providers), false);
  assert.equal(existsSync(stack.backend), true);
});

test("preparePluginCache keeps linked providers when the cache folder exists", () => {
  freshHome();
  mkdirSync(pluginCacheDir(), { recursive: true });
  const stack = projectFolder("stack");
  preparePluginCache({}, "");
  assert.equal(existsSync(stack.providers), true);
});

test("preparePluginCache copes with no deployment folder yet", () => {
  freshHome();
  rmSync(deploymentDir(), { recursive: true, force: true });
  assert.deepEqual(preparePluginCache({}, ""), { TF_PLUGIN_CACHE_DIR: pluginCacheDir() });
});
