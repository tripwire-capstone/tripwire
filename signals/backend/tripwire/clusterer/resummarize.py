"""Re-summarize one run's events under another prompt version, for the sweep to replay (Nov 17).

    python -m tripwire.clusterer.report ... --sweep --as-version 5

A developer tool, never run by the deployed product. It answers "would prompt vN cluster better
than the summaries we have?" on the SAME events, so the only thing that differs is the wording:

  1. Create a schema `asver_<random>`, apply the migrations and copy the rows the clusterer reads,
     except event_summaries (those are what we're replacing).
  2. Summarize every given event under vN: one LLM call per event, `workers` calls in flight. Only
     the call runs in a thread; loading the event and saving its row stay on this thread, because
     a psycopg connection isn't shared across threads. Events whose call fails get one more try.
  3. Embed the new summaries, then give each embedding the created_at of the event's ORIGINAL
     embedding. The sweep replays passes from those timestamps, so v4 and vN are replayed in the
     same order; otherwise a difference in scores could come from arrival order, not wording.
  4. The caller runs sweep(conn, thresholds, source_schema=<that schema>) and then drops it.

Nothing in the real tables changes. Costs one summarizer call per event (plus a retry for failures)
and one embedding call per EMBED_BATCH summaries.
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from psycopg import sql

from ..llm import LLMError
from .embed import save_embedding
from .summarize import build_messages, decide, load_event, load_row, save
from .sweep import COPIED_TABLES, MIGRATIONS

log = logging.getLogger("clusterer.resummarize")

SCHEMA_PREFIX = "asver_"
EMBED_BATCH = 100


def drop_leftover_schemas(conn):
    """Drop `asver_` schemas left by a crashed run. Returns how many."""
    leftovers = [row[0] for row in conn.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (SCHEMA_PREFIX.replace("_", r"\_") + "%",))]
    for schema in leftovers:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    return len(leftovers)


def drop_schema(conn, schema):
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def build_version_schema(conn, event_ids, llm, embedder, model, prompt, workers=8,
                         now=lambda: datetime.now(timezone.utc)):
    """Steps 1-3 above. Returns (schema name, {event_id: RowUpdate} for every event tried).

    `conn` must be in autocommit mode. Its search_path is put back afterwards. On any error the new
    schema is dropped before the error is raised, so a failed run leaves nothing behind.
    """
    original_path = conn.execute("SHOW search_path").fetchone()[0]
    source_schema = conn.execute("SELECT current_schema()").fetchone()[0]
    schema = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:8]}"
    try:
        _create_schema(conn, schema, source_schema)
        updates = _summarize_all(conn, event_ids, llm, model, prompt, workers, now)
        retry = [event_id for event_id, update in updates.items() if update.status != "done"]
        if retry:
            log.info("retrying %d events whose summary didn't finish", len(retry))
            updates.update(_summarize_all(conn, retry, llm, model, prompt, workers, now))
        _embed_all(conn, embedder, source_schema)
    except BaseException:
        conn.execute("SELECT set_config('search_path', %s, false)", (original_path,))
        drop_schema(conn, schema)
        raise
    conn.execute("SELECT set_config('search_path', %s, false)", (original_path,))
    return schema, updates


def _create_schema(conn, schema, source_schema):
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        conn.execute(migration.read_text())
    for table in COPIED_TABLES:
        if table == "event_summaries":
            continue
        conn.execute(sql.SQL("INSERT INTO {} OVERRIDING SYSTEM VALUE SELECT * FROM {}.{}").format(
            sql.Identifier(table), sql.Identifier(source_schema), sql.Identifier(table)))


def _summarize_all(conn, event_ids, llm, model, prompt, workers, now):
    """Load and build on this thread, call the LLM in `workers` threads, decide and save here."""
    messages = {event_id: build_messages(load_event(conn, event_id), prompt.system) for event_id in event_ids}

    def call(event_id):
        try:
            return llm.complete(model, messages[event_id], prompt.schema)
        except LLMError as error:          # an expected failure becomes the row's outcome
            return error

    updates = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {event_id: pool.submit(call, event_id) for event_id in event_ids}
        for done_count, (event_id, future) in enumerate(futures.items(), start=1):
            # The current row (None on a first try) lets a retry count its attempt and keep the
            # first try's cost. An unexpected exception in a call raises here: a dev tool fails loudly.
            update = decide(future.result(), load_row(conn, event_id), now())
            save(conn, event_id, update, model, prompt.version, now())
            updates[event_id] = update
            if done_count % 50 == 0:
                log.info("summarized %d/%d", done_count, len(event_ids))
    return updates


def _embed_all(conn, embedder, source_schema):
    rows = conn.execute(
        "SELECT event_id, summary FROM event_summaries WHERE status = 'done' ORDER BY event_id").fetchall()
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start:start + EMBED_BATCH]
        vectors = embedder.embed([summary for _, summary in batch])
        for (event_id, _), vector in zip(batch, vectors):
            save_embedding(conn, event_id, vector, embedder.model)
    # Step 3: the original embedding's timestamp, so the replay releases events in the same passes.
    conn.execute(sql.SQL(
        "UPDATE event_embeddings mine SET created_at = original.created_at "
        "FROM {}.event_embeddings original WHERE original.event_id = mine.event_id"
    ).format(sql.Identifier(source_schema)))
