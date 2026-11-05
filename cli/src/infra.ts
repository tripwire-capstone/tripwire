import { existsSync } from "node:fs";
import { copyFile, mkdir, readdir, rm } from "node:fs/promises";
import { dirname, join, sep } from "node:path";

// The allowlist for copying /infra files
export function shouldCopy(relPath: string): boolean {
  const parts = relPath.split("/");
  const name = parts[parts.length - 1];

  if (parts.includes(".terraform")) return false;
  if (relPath.startsWith("stack/files/")) return true;

  return (
    name.endsWith(".tf") ||
    name.endsWith(".tfvars.example") ||
    name.endsWith(".hcl.example") ||
    name === ".terraform.lock.hcl"
  );
}

// Paths under dir, with "/" separators
async function listFiles(dir: string): Promise<string[]> {
  if (!existsSync(dir)) return [];
  const paths = await readdir(dir, { recursive: true });
  return paths.map((p) => p.split(sep).join("/"));
}

// Copies allowed files, removes dropped ones
export async function syncInfra(from: string, to: string): Promise<string[]> {
  const wanted = (await listFiles(from)).filter(shouldCopy);

  for (const rel of wanted) {
    const target = join(to, rel);
    await mkdir(dirname(target), { recursive: true });
    await copyFile(join(from, rel), target);
  }

  const stale = (await listFiles(to)).filter((rel) => shouldCopy(rel) && !wanted.includes(rel));
  for (const rel of stale) {
    await rm(join(to, rel));
  }

  return wanted;
}