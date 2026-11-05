import { UserError } from "./errors.js";
import { DEPLOYMENT_NAME } from "./paths.js";
import { CommandError, run, type Log } from "./run.js";

export interface DbSnapshot {
  id: string;
  created: string;
}

export interface VolumeSnapshot {
  id: string;
  created: string;
  tags: { Key: string; Value: string }[];
}

export interface Snapshots {
  db: DbSnapshot[];
  volumes: VolumeSnapshot[];
}

export interface SnapshotSet {
  db: DbSnapshot;
  volume: VolumeSnapshot;
}

export interface Rotation {
  keep: SnapshotSet;
  deleteDb: string[];
  deleteVolumes: string[];
}

// Made by the scheduled snapshot policy, not a teardown
export function isScheduled(snapshot: VolumeSnapshot): boolean {
  return snapshot.tags.some((t) => t.Key === "aws:dlm:lifecycle-policy-id");
}

export function newest<T extends { created: string }>(items: T[]): T | undefined {
  return [...items].sort((a, b) => b.created.localeCompare(a.created))[0];
}

// The newest final snapshot of each kind, if any
export function latestFinal(snapshots: Snapshots): Partial<SnapshotSet> {
  return {
    db: newest(snapshots.db),
    volume: newest(snapshots.volumes.filter((s) => !isScheduled(s))),
  };
}

// What to keep and delete after a destroy.
// Throws unless exactly one new final snapshot of each kind appeared.
export function planRotation(before: Snapshots, after: Snapshots): Rotation {
  const oldIds = new Set([...before.db, ...before.volumes].map((s) => s.id));
  const newDb = after.db.filter((s) => !oldIds.has(s.id));
  const newVolumes = after.volumes.filter((s) => !isScheduled(s) && !oldIds.has(s.id));

  if (newDb.length !== 1 || newVolumes.length !== 1) {
    throw new UserError(
      `Expected 1 new database and 1 new data volume snapshot, found ` +
        `${newDb.length} and ${newVolumes.length}. No old snapshots were deleted.`,
    );
  }

  const keep = { db: newDb[0], volume: newVolumes[0] };
  return {
    keep,
    deleteDb: after.db.filter((s) => s.id !== keep.db.id).map((s) => s.id),
    deleteVolumes: after.volumes.filter((s) => s.id !== keep.volume.id).map((s) => s.id),
  };
}

export function formatDate(iso: string): string {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

export async function listSnapshots(log: Log, region: string): Promise<Snapshots> {
  const dbOut = await run(log, "aws", [
    "rds", "describe-db-snapshots", "--snapshot-type", "manual", "--region", region,
    "--query",
    `DBSnapshots[?starts_with(DBSnapshotIdentifier, '${DEPLOYMENT_NAME}-db-final-')]` +
      ".{id: DBSnapshotIdentifier, created: SnapshotCreateTime}",
    "--output", "json",
  ]);
  const volumeOut = await run(log, "aws", [
    "ec2", "describe-snapshots", "--owner-ids", "self", "--region", region,
    "--filters",
    `Name=tag:Project,Values=${DEPLOYMENT_NAME}`,
    `Name=tag:Backup,Values=${DEPLOYMENT_NAME}-data`,
    "--query", "Snapshots[].{id: SnapshotId, created: StartTime, tags: Tags}",
    "--output", "json",
  ]);
  return {
    db: JSON.parse(dbOut) as DbSnapshot[],
    volumes: JSON.parse(volumeOut) as VolumeSnapshot[],
  };
}

// aws wait gives up after a set time; keep going unless it really failed
async function waitLonger(log: Log, args: string[]): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    try {
      await run(log, "aws", args);
      return;
    } catch (err) {
      const timedOut = err instanceof CommandError && err.message.includes("Max attempts exceeded");
      if (!timedOut || attempt === 3) throw err;
    }
  }
}

export async function waitForSet(log: Log, region: string, set: SnapshotSet): Promise<void> {
  await waitLonger(log, [
    "rds", "wait", "db-snapshot-available",
    "--db-snapshot-identifier", set.db.id, "--region", region,
  ]);
  await waitLonger(log, [
    "ec2", "wait", "snapshot-completed", "--snapshot-ids", set.volume.id, "--region", region,
  ]);
}

export async function deleteSnapshots(log: Log, region: string, rotation: Rotation): Promise<void> {
  for (const id of rotation.deleteDb) {
    await run(log, "aws", [
      "rds", "delete-db-snapshot", "--db-snapshot-identifier", id, "--region", region,
    ]);
  }
  for (const id of rotation.deleteVolumes) {
    await run(log, "aws", ["ec2", "delete-snapshot", "--snapshot-id", id, "--region", region]);
  }
}