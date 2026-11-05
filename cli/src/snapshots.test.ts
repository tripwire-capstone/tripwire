import { test } from "node:test";
import assert from "node:assert/strict";
import { latestFinal, planRotation, type Snapshots } from "./snapshots.js";

const DLM = [{ Key: "aws:dlm:lifecycle-policy-id", Value: "policy-1" }];

const before: Snapshots = {
  db: [{ id: "tripwire-db-final-aaa", created: "2026-10-01T10:00:00Z" }],
  volumes: [
    { id: "snap-old", created: "2026-10-01T10:00:00Z", tags: [] },
    { id: "snap-hourly", created: "2026-11-05T09:00:00Z", tags: DLM },
  ],
};

const after: Snapshots = {
  db: [...before.db, { id: "tripwire-db-final-bbb", created: "2026-11-05T10:00:00Z" }],
  volumes: [...before.volumes, { id: "snap-new", created: "2026-11-05T10:00:00Z", tags: [] }],
};

test("keeps the new set, deletes the old set and scheduled snapshots", () => {
  const rotation = planRotation(before, after);
  assert.equal(rotation.keep.db.id, "tripwire-db-final-bbb");
  assert.equal(rotation.keep.volume.id, "snap-new");
  assert.deepEqual(rotation.deleteDb, ["tripwire-db-final-aaa"]);
  assert.deepEqual(rotation.deleteVolumes, ["snap-old", "snap-hourly"]);
});

test("cleans up several old sets left by an earlier failure", () => {
  const extra = { id: "tripwire-db-final-zzz", created: "2026-09-01T10:00:00Z" };
  const rotation = planRotation(
    { ...before, db: [...before.db, extra] },
    { ...after, db: [...after.db, extra] },
  );
  assert.deepEqual(rotation.deleteDb.sort(), ["tripwire-db-final-aaa", "tripwire-db-final-zzz"]);
});

test("a first destroy has nothing old to delete", () => {
  const rotation = planRotation(
    { db: [], volumes: [] },
    {
      db: [{ id: "tripwire-db-final-bbb", created: "2026-11-05T10:00:00Z" }],
      volumes: [{ id: "snap-new", created: "2026-11-05T10:00:00Z", tags: [] }],
    },
  );
  assert.deepEqual(rotation.deleteDb, []);
  assert.deepEqual(rotation.deleteVolumes, []);
});

test("deletes nothing if a new final snapshot is missing", () => {
  const noVolume = { ...after, volumes: before.volumes };
  assert.throws(() => planRotation(before, noVolume), /found 1 and 0/);
});

test("deletes nothing if there are two new ones of a kind", () => {
  const twoDb = {
    ...after,
    db: [...after.db, { id: "tripwire-db-final-ccc", created: "2026-11-05T10:01:00Z" }],
  };
  assert.throws(() => planRotation(before, twoDb), /found 2 and 1/);
});

test("a new scheduled snapshot is not mistaken for the final one", () => {
  const hourly = { id: "snap-hourly-2", created: "2026-11-05T10:00:00Z", tags: DLM };
  const rotation = planRotation(before, { ...after, volumes: [...after.volumes, hourly] });
  assert.equal(rotation.keep.volume.id, "snap-new");
  assert.ok(rotation.deleteVolumes.includes("snap-hourly-2"));
});

test("restore picks the newest final snapshots, never a scheduled one", () => {
  const latest = latestFinal(after);
  assert.equal(latest.db?.id, "tripwire-db-final-bbb");
  assert.equal(latest.volume?.id, "snap-new");

  const onlyScheduled = latestFinal({ db: [], volumes: [before.volumes[1]] });
  assert.equal(onlyScheduled.volume, undefined);
});