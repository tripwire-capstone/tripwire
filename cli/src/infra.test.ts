import { test, type TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { shouldCopy, syncInfra } from "./infra.js";
import { bundledInfraDir } from "./paths.js";

// Temp folder, removed after the test
async function tempDir(t: TestContext): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "tripwire-test-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return dir;
}

test("ships Terraform code, examples, lock files and stack/files", () => {
  for (const path of [
    "stack/ecs.tf",
    "stack/terraform.tfvars.example",
    "persistent/backend.hcl.example",
    "bootstrap/.terraform.lock.hcl",
    "stack/files/docker-compose.yml.tftpl",
  ]) {
    assert.equal(shouldCopy(path), true, path);
  }
});

test("never ships settings, state, plans or provider downloads", () => {
  for (const path of [
    "stack/terraform.tfvars",
    "stack/backend.hcl",
    "bootstrap/terraform.tfstate",
    "bootstrap/terraform.tfstate.backup",
    "stack/tfplan",
    "stack/.terraform/modules/x/main.tf",
    ".tflint.hcl",
  ]) {
    assert.equal(shouldCopy(path), false, path);
  }
});

test("copying the real infra/ leaves every secret behind", async (t) => {
  const to = await tempDir(t);
  await syncInfra(bundledInfraDir(), to);

  const copied = await readdir(to, { recursive: true });
  assert.ok(copied.some((p) => p.endsWith("ecs.tf")), "the stack's code was copied");

  const leaked = copied.filter((p) => /\.tfstate|\.tfvars$|backend\.hcl$|\.terraform$/.test(p));
  assert.deepEqual(leaked, []);
});

test("removes code the bundle dropped, keeps the deployer's own files", async (t) => {
  const from = await tempDir(t);
  const to = await tempDir(t);

  await mkdir(join(from, "stack"));
  await writeFile(join(from, "stack/new.tf"), "");
  await mkdir(join(to, "stack"));
  await writeFile(join(to, "stack/old.tf"), "");
  await writeFile(join(to, "stack/terraform.tfvars"), "");

  await syncInfra(from, to);

  const left = (await readdir(join(to, "stack"))).sort();
  assert.deepEqual(left, ["new.tf", "terraform.tfvars"]);
});