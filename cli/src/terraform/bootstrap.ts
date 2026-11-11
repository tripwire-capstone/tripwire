import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { run, type Log } from "../core/run.js";
import { buildTfvars, stateBucketName } from "./tfvars.js";
import type { TripwireConfig } from "../types/config.js";

export const BUCKET_RESOURCES = [
  "aws_s3_bucket.state",
  "aws_s3_bucket_versioning.state",
  "aws_s3_bucket_server_side_encryption_configuration.state",
  "aws_s3_bucket_public_access_block.state",
  "aws_s3_bucket_lifecycle_configuration.state",
  "aws_s3_bucket_policy.state",
];

// Bucket resources not in `terraform state list` output yet
export function missingResources(stateList: string): string[] {
  const have = new Set(stateList.split("\n").map((line) => line.trim()));
  return BUCKET_RESOURCES.filter((address) => !have.has(address));
}

export async function bucketExists(log: Log, bucket: string): Promise<boolean> {
  const out = await run(log, "aws", [
    "s3api", "list-buckets",
    "--query", `Buckets[?Name=='${bucket}'].Name`, "--output", "json",
  ]);
  return (JSON.parse(out) as string[]).length > 0;
}

// Imports an existing state bucket into dir's state.
// Returns what was imported, or null if there's no bucket.
export async function adoptStateBucket(
  log: Log,
  dir: string,
  config: TripwireConfig,
): Promise<string[] | null> {
  const bucket = stateBucketName(config.accountId, config.region);
  if (!(await bucketExists(log, bucket))) return null;

  await writeFile(join(dir, "terraform.tfvars"), buildTfvars("bootstrap", config));
  await run(log, "terraform", ["init", "-input=false", "-no-color"], { cwd: dir });

  const stateList = existsSync(join(dir, "terraform.tfstate"))
    ? await run(log, "terraform", ["state", "list"], { cwd: dir })
    : "";

  const missing = missingResources(stateList);
  for (const address of missing) {
    await run(log, "terraform", ["import", "-input=false", "-no-color", address, bucket], {
      cwd: dir,
    });
  }
  return missing;
}