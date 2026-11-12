"""Threshold sweep: replay the real clusterer at other thresholds, in a throwaway schema (PR 6).

Decided Nov 12 (Decision 2, option C; reasons in the work log): the live clusterer works in passes
(join by center, then group the pool by average linkage, then move centers), so what clusters
form depends on the order events arrive. To score a threshold for *that* clusterer, the sweep
runs the real `run_pass()` on a copy of the data instead of imitating it. Per threshold:

  1. Create a schema `calib_<random>` and apply the migrations (the tests do the same).
  2. Copy the rows the clusterer reads (COPIED_TABLES) with `SELECT *`, so new columns come along.
     Embeddings, clusters and memberships are not copied: the replay rebuilds them.
  3. Release the embeddings in the order they were really created, one pass at a time, through
     `run_pass(..., prepare=<release this pass's embeddings>)`. Passes are recovered from the
     timestamps: the prepare step saves a pass's embeddings within moments of each other, so a gap
     of at least half the loop interval starts a new pass, and a longer gap stands for the quiet
     passes the live loop ran meanwhile (each of which can still join pool events to moved centers).
     After the last embedding, passes run until nothing changes.
  4. Read which cluster each event ended up in, then drop the schema.

Nothing in the real tables changes, and no OpenAI calls are made (embeddings are already stored;
step 1 of the pass is replaced by the release). Schemas left over by a crash are dropped at the
next start. Upkeep (also in DESIGN.md Q14): a new table the clusterer reads goes in COPIED_TABLES;
once assign asks an LLM about the middle band (post-MVP), the replay needs a switch to skip or fake
that call, or every threshold pays for it.

Limitation: the pool window (Q9) is measured from now, so events older than
CLUSTER_POOL_WINDOW_DAYS can join clusters in the replay but not form them; replay fresh runs.
"""

import logging
import os
import uuid
from dataclasses import replace
from pathlib import Path

from psycopg import sql

from .loop import run_pass
from .settings import load_settings

log = logging.getLogger("clusterer.sweep")

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
SCHEMA_PREFIX = "calib_"
# Everything the clusterer reads, parents before children (foreign keys).
COPIED_TABLES = ("projects", "traces", "signals", "signal_versions", "signal_runs", "signal_events",
                 "event_summaries")
MAX_SETTLING_PASSES = 50


class SweepError(RuntimeError):
    """The replay couldn't run faithfully (e.g. a clusterer step failed), so its scores would mislead."""


def default_thresholds():
    """0.20 to 0.40 in steps of 0.02 (DESIGN.md Q14: "e.g. 0.20-0.40")."""
    return [round(0.20 + step * 0.02, 2) for step in range(11)]


def replay_settings(threshold, environ=None):
    """The clusterer's own settings (env vars, same defaults), with the threshold under test.
    Placeholder mode only so no OpenAI key is required: the replay never calls the prepare half."""
    environ = dict(os.environ if environ is None else environ)
    environ.setdefault("DATABASE_URL", "postgresql://replay")
    environ["CLUSTERER_MODE"] = "placeholder"
    return replace(load_settings(environ), assign_threshold=threshold)


def passes_from_timestamps(embeddings, interval_seconds):
    """Split embeddings (sorted by creation time) into the passes that released them.

    `embeddings`: [(event_id, created_at)]. Returns [(quiet_passes_before, [event_id, ...])]: a gap
    of at least half the interval starts a new pass, and every further full interval in the gap
    is one quiet pass (no new embeddings) the live loop ran in between.
    """
    passes = []
    previous_time = None
    for event_id, created_at in embeddings:
        if previous_time is None:
            passes.append((0, [event_id]))
        else:
            gap = (created_at - previous_time).total_seconds()
            if gap < interval_seconds / 2:
                passes[-1][1].append(event_id)
            else:
                passes.append((max(int(gap // interval_seconds) - 1, 0), [event_id]))
        previous_time = created_at
    return passes


def drop_leftover_schemas(conn):
    """Drop `calib_` schemas left by a crashed sweep. Returns how many."""
    leftovers = [row[0] for row in conn.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (SCHEMA_PREFIX.replace("_", r"\_") + "%",))]
    for schema in leftovers:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    if leftovers:
        log.info("dropped %d leftover sweep schema(s)", len(leftovers))
    return len(leftovers)


def _create_copy(conn, source_schema):
    schema = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:8]}"
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        conn.execute(migration.read_text())
    for table in COPIED_TABLES:
        conn.execute(sql.SQL("INSERT INTO {} OVERRIDING SYSTEM VALUE SELECT * FROM {}.{}").format(
            sql.Identifier(table), sql.Identifier(source_schema), sql.Identifier(table)))
    return schema


def replay(conn, threshold, source_schema=None, environ=None):
    """Replay the clusterer on a copy of `source_schema` (default: the connection's current schema)
    at `threshold`. Returns {event_id: cluster_id or None (pool)} for every embedded event.

    `conn` must be in autocommit mode (each step commits on its own, as in the live loop). Its
    search_path is put back afterwards, and the copy is always dropped.
    """
    original_path = conn.execute("SHOW search_path").fetchone()[0]
    source_schema = source_schema or conn.execute("SELECT current_schema()").fetchone()[0]
    if source_schema.startswith(SCHEMA_PREFIX):
        raise SweepError(f"refusing to replay a sweep schema ({source_schema})")
    settings = replay_settings(threshold, environ)
    embeddings = conn.execute(sql.SQL(
        "SELECT event_id, created_at FROM {}.event_embeddings ORDER BY created_at, event_id"
    ).format(sql.Identifier(source_schema))).fetchall()
    release_sql = sql.SQL(
        "INSERT INTO event_embeddings SELECT * FROM {}.event_embeddings WHERE event_id = ANY(%s)"
    ).format(sql.Identifier(source_schema))

    schema = None
    try:
        schema = _create_copy(conn, source_schema)

        def run(released):
            def release(pass_conn, limit):
                if released:
                    pass_conn.execute(release_sql, (released,))
                return {"released": len(released)} if released else {}
            result = run_pass(conn, settings, prepare=release)
            if result.failed_steps:
                raise SweepError(f"clusterer step(s) {result.failed_steps} failed during the replay "
                                 f"at threshold {threshold}; see the log above")
            return result

        def settle(limit):
            for _ in range(limit):
                if not run([]).anything_happened():
                    return

        for quiet_passes, event_ids in passes_from_timestamps(embeddings, settings.interval_seconds):
            settle(quiet_passes)
            run(event_ids)
        settle(MAX_SETTLING_PASSES)

        membership = dict(conn.execute(
            "SELECT embedded.event_id, member.cluster_id FROM event_embeddings embedded "
            "LEFT JOIN cluster_members member ON member.event_id = embedded.event_id"))
    finally:
        conn.execute("SELECT set_config('search_path', %s, false)", (original_path,))
        if schema is not None:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    return membership


def sweep(conn, thresholds, source_schema=None, environ=None):
    """[(threshold, {event_id: cluster_id or None})] for each threshold, smallest first."""
    drop_leftover_schemas(conn)
    results = []
    for threshold in sorted(thresholds):
        log.info("replaying at threshold %.2f", threshold)
        results.append((threshold, replay(conn, threshold, source_schema, environ)))
    return results
