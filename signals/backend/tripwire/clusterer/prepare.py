"""Step 1 of the clusterer: prepare new events (summarize, then embed). Design Notes Q21.

One Preparer is built at startup (__main__.py) and handed to the loop as
`run_pass(conn, settings, prepare=preparer.prepare_events)`. It lives as long as the clusterer
because it keeps things between passes: the OpenAI clients and, as a last resort, a skip list.

One pass of prepare_events():
  1. Summarize events that need it (no summary row yet, or `pending` with its wait over),
     live before backfill, then oldest first. One call per event.
     - Stops starting new summaries after TIME_BUDGET (the loop's --alive check restarts the
       clusterer if no pass finishes for 3 minutes).
     - Stops after a service error (rate limit, outage): the shared OpenAI key would only collect
       more errors, and the judge uses the same key.
     - An unexpected error (a bug in our code) is logged with its traceback and saved on the event
       as a `failed` summary with error_kind 'crashed' (migration 004), so it can't block the queue
       or be paid for again, and the health check doesn't warn about it forever. If the health
       check's "last N summaries all failed" rule trips, it's a bug hitting every event.
     - A database error is NOT a crash: it ends the step (the loop logs it and retries next pass),
       so an outage can't mark a whole batch as crashed.
  2. Embed summaries that are `done` but have no vector: one call for the whole batch.
"""

import logging
import time
from datetime import datetime, timezone

import psycopg

from ..llm import LLMServiceError
from .embed import save_embedding
from .summarize import crash_update, load_prompt, load_row, save, summarize_event

log = logging.getLogger("clusterer.prepare")

TIME_BUDGET_SECONDS = 60      # MVP constant (Q21)

# `source = 'backfill'` is false for live runs, and false sorts first: live before backfill.
NEEDS_SUMMARY_SQL = """
SELECT e.id
FROM signal_events e
JOIN signal_runs r ON r.id = e.run_id
LEFT JOIN event_summaries s ON s.event_id = e.id
WHERE (s.event_id IS NULL
       OR (s.status = 'pending' AND (s.not_before IS NULL OR s.not_before <= %(now)s)))
ORDER BY r.source = 'backfill', e.created_at, e.id
LIMIT %(limit)s
"""

NEEDS_EMBEDDING_SQL = """
SELECT s.event_id, s.summary
FROM event_summaries s
JOIN signal_events e ON e.id = s.event_id
JOIN signal_runs r ON r.id = e.run_id
WHERE s.status = 'done'
  AND NOT EXISTS (SELECT 1 FROM event_embeddings x WHERE x.event_id = s.event_id)
ORDER BY r.source = 'backfill', e.created_at, e.id
LIMIT %(limit)s
"""


class Preparer:
    def __init__(self, llm, embedder, model, prompt_version,
                 clock=time.monotonic, now=lambda: datetime.now(timezone.utc)):
        self.llm = llm
        self.embedder = embedder
        self.model = model
        self.prompt = load_prompt(prompt_version)
        self.clock = clock            # for the time budget; tests pass a fake one
        self.now = now                # for waits and timestamps; tests pass a fixed time
        # Last resort only: events whose crash couldn't even be saved. Cleared on restart.
        self.skipped = set()

    def prepare_events(self, conn, limit):
        """Summarize and embed up to `limit` events. Returns the non-zero counts (empty = nothing)."""
        counts = {}
        self._summarize(conn, limit, counts)
        self._embed(conn, limit, counts)
        return {name: value for name, value in counts.items() if value}

    def _summarize(self, conn, limit, counts):
        started = self.clock()
        event_ids = [row[0] for row in conn.execute(
            NEEDS_SUMMARY_SQL, {"now": self.now(), "limit": limit})]

        for position, event_id in enumerate(event_ids):
            if self.clock() - started >= TIME_BUDGET_SECONDS:
                counts["left_for_next_pass"] = len(event_ids) - position
                log.info("prepare: time budget used; %d events left for the next pass",
                         len(event_ids) - position)
                return
            if event_id in self.skipped:
                continue
            try:
                update = summarize_event(conn, self.llm, event_id, self.model, self.prompt, self.now())
            except (psycopg.OperationalError, psycopg.InterfaceError):
                # The database is unreachable, not a bug: end the step; the loop retries next pass.
                # (A bad query or a broken table rule is a bug, so it's handled below as a crash.)
                raise
            except Exception as error:
                log.exception("prepare: event %s hit a bug in our code; saved as a crashed summary",
                              event_id)
                self._record_crash(conn, event_id, error)
                counts["crashed"] = counts.get("crashed", 0) + 1
                continue

            outcome = {"done": "summarized", "failed": "failed"}.get(update.status, "retrying")
            counts[outcome] = counts.get(outcome, 0) + 1
            if update.status == "pending" and update.error_kind == "service":
                left = len(event_ids) - position - 1
                counts["left_for_next_pass"] = left
                log.warning("prepare: OpenAI service problem; stopping summaries for this pass "
                            "(%d events left)", left)
                return

    def _record_crash(self, conn, event_id, error):
        try:
            save(conn, event_id, crash_update(load_row(conn, event_id), error, self.now()),
                 self.model, self.prompt.version, self.now())
        except Exception:
            # Even saving the crash failed (e.g. the bug is in save itself): skip the event until
            # restart so it isn't retried, and possibly paid for, every pass.
            log.exception("prepare: couldn't save the crash for event %s; skipping it until restart",
                          event_id)
            self.skipped.add(event_id)

    def _embed(self, conn, limit, counts):
        rows = conn.execute(NEEDS_EMBEDDING_SQL, {"limit": limit}).fetchall()
        if not rows:
            return
        try:
            vectors = self.embedder.embed([summary for _, summary in rows])
        except LLMServiceError as error:
            log.warning("prepare: embedding call failed (%s); %d summaries wait for the next pass",
                        error, len(rows))
            return
        for (event_id, _), vector in zip(rows, vectors):
            save_embedding(conn, event_id, vector, self.embedder.model)
        counts["embedded"] = len(rows)
