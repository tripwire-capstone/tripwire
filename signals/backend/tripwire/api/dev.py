"""Developer view: is data moving through Tripwire, and if not, where is it stuck and why?

    GET /api/dev/status      (the page: mock-ui/src/pages/Dev.jsx, at /dev)

For the team, not end users (decided Nov 12, after a revoked OpenAI key left hundreds of runs
pending with nothing on screen to say so). Read-only; every number is counted in SQL. One request
in one transaction, so every section uses the same "now".

Sections (one function each, one field of DevStatus each):
  * pipeline: one row per step, with a count per state, when the oldest item still waiting entered
    the step, and how many got through it in the last 10 minutes. Stuck = something has waited more
    than 2 minutes AND nothing got through recently; a big backlog that is draining has an old
    oldest item too, and a quiet system that just got one new item hasn't had time to move it.
  * errors: the errors behind runs and summaries, grouped by exact message, split into still
    pending or running / failed / done, with the signals each one hit. Unfinished items are listed however old they are (a stuck run
    must never age out of the list); finished ones only for 24 hours.
  * latest_decisions: the judge's newest finished runs, with what it found and why, including
    "found nothing". Nowhere else in the UI shows the judge's reasoning for a run that found nothing,
    and "why didn't it flag this?" is the question tuning a signal keeps asking.
  * latest_summaries: the newest summaries and where each event landed, to check that the output
    makes sense. Counts can't catch a broken prompt that summarizes everything as "an issue".

Adding a section:
  1. Here: SQL as a constant, a response model, a function `def my_section(cur)` that returns it,
     and a field on DevStatus filled in `status()`. Read-only queries; count in SQL, not Python.
  2. tests/test_dev.py: build rows with the helpers there (add_signal, add_run, add_event,
     add_summary, add_embedding, add_member), call status(client), check your field.
  3. mock-ui/src/api/dev.js: map the field in devStatusFromApi (snake_case -> camelCase).
  4. mock-ui/src/pages/Dev.jsx: a component for it in <Dev>, and a few lines in statusAsText() so
     "Copy as text" includes it.
  Keep the page fast: every section runs on every refresh.

To remove the developer view: delete this file, tests/test_dev.py, mock-ui/src/api/dev.js and
mock-ui/src/pages/Dev.jsx, and the lines marked "dev view" in app.py, App.jsx, api/index.js,
links.js, AppLayout.jsx, README.md and compose.yaml.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from pydantic import BaseModel

from .db import get_conn

router = APIRouter(prefix="/api/dev")

# "Got through recently" and "a finished error still worth listing".
RECENT = "interval '10 minutes'"
ERROR_WINDOW = "interval '24 hours'"
# Every worker runs every 5-10 s, so an item waiting this long with nothing getting through is never
# normal. Shorter, and a quiet system that just got one new item would flash "stuck".
STUCK_AFTER = timedelta(minutes=2)
MAX_ERROR_GROUPS = 50
LATEST_SUMMARIES = 10
LATEST_DECISIONS = 10


# ------------------------------------------------------------------ pipeline

class StateCount(BaseModel):
    state: str
    count: int


class Stage(BaseModel):
    stage: str
    counts: list[StateCount]           # in pipeline order; the page shows them as they come
    oldest_waiting_at: datetime | None  # None = nothing waiting (or waiting isn't meaningful here)
    done_recently: int                 # got through this step in the last 10 minutes
    stuck: bool                        # waited longer than STUCK_AFTER and nothing got through recently


class Pipeline(BaseModel):
    last_polled_at: datetime | None    # the stalest project's last successful poll; None = never
    newest_trace_ended_at: datetime | None
    stages: list[Stage]


POLL_SQL = """
SELECT (SELECT CASE WHEN bool_or(last_polled_at IS NULL) THEN NULL ELSE min(last_polled_at) END
        FROM projects) AS last_polled_at,
       (SELECT max(ended_at) FROM traces) AS newest_trace_ended_at
"""

TRACES_SQL = f"""
SELECT count(*) AS stored,
       count(*) FILTER (WHERE synced_at >= now() - {RECENT}) AS done_recently
FROM traces
"""

# State names are the table's own (status, signals.active), except two the table has no word for:
#   retrying       = pending with last_error set, a part of `pending` (the page shows it in brackets).
#                    A service error gives its attempt back, so this is how a revoked key shows up.
#   inactive_signal = pending or running on a signal with active = false. The runner never claims
#                    these, so they get their own state and are left out of "oldest waiting":
#                    otherwise turning a signal off would look like a stall forever.
RUNS_SQL = f"""
SELECT count(*) FILTER (WHERE s.active AND r.status = 'pending') AS pending,
       count(*) FILTER (WHERE s.active AND r.status = 'pending' AND r.last_error IS NOT NULL) AS retrying,
       count(*) FILTER (WHERE s.active AND r.status = 'running' AND r.lease_until >= now()) AS running,
       count(*) FILTER (WHERE s.active AND r.status = 'running' AND r.lease_until < now()) AS lease_expired,
       count(*) FILTER (WHERE NOT s.active AND r.status IN ('pending', 'running')) AS inactive_signal,
       count(*) FILTER (WHERE r.status = 'done') AS done,
       count(*) FILTER (WHERE r.status = 'failed') AS failed,
       min(r.created_at) FILTER (WHERE s.active AND r.status IN ('pending', 'running')) AS oldest_waiting_at,
       count(*) FILTER (WHERE r.status = 'done' AND r.finished_at >= now() - {RECENT}) AS done_recently
FROM signal_runs r
JOIN signal_versions v ON v.id = r.signal_version_id
JOIN signals s ON s.id = v.signal_id
"""

EVENTS_SQL = f"""
SELECT count(*) AS found,
       count(*) FILTER (WHERE created_at >= now() - {RECENT}) AS done_recently
FROM signal_events
"""

# "Not tried" leaves out events that are already embedded: the placeholder clusterer embeds events
# without summarizing them, and those aren't waiting for anything.
# Waiting is measured from when the event was found, the same clock the clusterer's health check uses.
SUMMARIES_SQL = f"""
SELECT count(*) FILTER (WHERE summary.event_id IS NULL AND embedded.event_id IS NULL) AS not_tried,
       count(*) FILTER (WHERE summary.status = 'pending') AS pending,
       count(*) FILTER (WHERE summary.status = 'pending' AND summary.last_error IS NOT NULL) AS retrying,
       count(*) FILTER (WHERE summary.status = 'done') AS done,
       count(*) FILTER (WHERE summary.status = 'failed') AS failed,
       min(event.created_at) FILTER (WHERE (summary.event_id IS NULL AND embedded.event_id IS NULL)
                                        OR summary.status = 'pending') AS oldest_waiting_at,
       count(*) FILTER (WHERE summary.status = 'done'
                          AND summary.finished_at >= now() - {RECENT}) AS done_recently
FROM signal_events event
LEFT JOIN event_summaries summary ON summary.event_id = event.id
LEFT JOIN event_embeddings embedded ON embedded.event_id = event.id
"""

# Why summaries failed for good: bad_output / too_long / refused / crashed.
SUMMARY_FAILURES_SQL = """
SELECT error_kind, count(*) FROM event_summaries
WHERE status = 'failed'
GROUP BY error_kind
ORDER BY count(*) DESC, error_kind
"""

# Summarized but not embedded yet. Prepare embeds right after summarizing, so this is normally 0.
EMBEDDINGS_SQL = f"""
SELECT count(*) FILTER (WHERE summary.status = 'done' AND embedded.event_id IS NULL) AS waiting,
       count(embedded.event_id) AS embedded,
       min(summary.finished_at) FILTER (WHERE summary.status = 'done'
                                          AND embedded.event_id IS NULL) AS oldest_waiting_at,
       count(*) FILTER (WHERE embedded.created_at >= now() - {RECENT}) AS done_recently
FROM signal_events event
LEFT JOIN event_summaries summary ON summary.event_id = event.id
LEFT JOIN event_embeddings embedded ON embedded.event_id = event.id
"""

# The pool is not a queue: an event with nothing similar stays there until similar events arrive,
# so its age says nothing about a stall. No "oldest waiting" here on purpose.
CLUSTERS_SQL = f"""
SELECT count(*) FILTER (WHERE member.event_id IS NULL) AS in_pool,
       count(member.event_id) AS clustered,
       count(*) FILTER (WHERE member.assigned_at >= now() - {RECENT}) AS done_recently
FROM event_embeddings embedded
LEFT JOIN cluster_members member ON member.event_id = embedded.event_id
"""


def stage(name, row, states, now):
    """A Stage from one query's row: `states` are its count columns, in the order to show them."""
    oldest = row.get("oldest_waiting_at")
    return Stage(stage=name, counts=[StateCount(state=s, count=row[s]) for s in states],
                 oldest_waiting_at=oldest, done_recently=row["done_recently"],
                 stuck=oldest is not None and now - oldest > STUCK_AFTER and row["done_recently"] == 0)


def pipeline(cur, now):
    summaries = one(cur, SUMMARIES_SQL)
    failures = cur.execute(SUMMARY_FAILURES_SQL).fetchall()
    # One state per failure kind ("failed: crashed"), so the cause shows without opening anything.
    summaries.update({f"failed: {f['error_kind']}": f["count"] for f in failures})
    return Pipeline(**one(cur, POLL_SQL), stages=[
        stage("traces", one(cur, TRACES_SQL), ["stored"], now),
        stage("runs", one(cur, RUNS_SQL),
              ["pending", "retrying", "running", "lease_expired", "inactive_signal", "done", "failed"], now),
        stage("events", one(cur, EVENTS_SQL), ["found"], now),
        stage("summaries", summaries,
              ["not_tried", "pending", "retrying", "done", "failed",
               *(f"failed: {f['error_kind']}" for f in failures)], now),
        stage("embeddings", one(cur, EMBEDDINGS_SQL), ["waiting", "embedded"], now),
        stage("clusters", one(cur, CLUSTERS_SQL), ["in_pool", "clustered"], now),
    ])


# ------------------------------------------------------------------ errors

class ErrorGroup(BaseModel):
    stage: str                         # 'runs' or 'summaries'
    message: str
    pending_or_running: int            # not finished yet: will be retried (or its signal is inactive)
    failed: int                        # status failed, last 24 h
    done: int                          # status done after this error (recovered), last 24 h
    signals: list[str]                 # the signals it hit, by name: one signal = likely its definition
    last_seen_at: datetime | None      # latest known time it happened; None = only still-retrying runs
    next_retry_at: datetime | None     # soonest retry of an item still waiting (may be past = due now)


class Errors(BaseModel):
    groups: list[ErrorGroup]
    more: bool                         # more than MAX_ERROR_GROUPS groups; only the first are listed


# Exact message text: a revoked key gives the same message on every run. If messages with retry
# times or request IDs split one problem into many rows, blank the numbers here.
# `signals` tells a setup problem (every signal) from one signal's definition (a bad model name).
# When it happened: a summary's updated_at is written on every summarizer call, so it is the error's
# time (or the recovery's, for a done summary). signal_runs records no time for an error: a failed
# run's finished_at is its final error, a done run's is its recovery, and a run still retrying has
# only not_before (its next try). So runs that are only still retrying have no last_seen_at; the page
# says "retrying" with the next try instead of guessing. (A last_error_at column would fix this; it's
# the Signals pair's table.)
ERRORS_SQL = f"""
SELECT stage, message, pending_or_running, failed, done, signals, last_seen_at, next_retry_at FROM (
    SELECT 'runs' AS stage, r.last_error AS message,
           count(*) FILTER (WHERE r.status IN ('pending', 'running')) AS pending_or_running,
           count(*) FILTER (WHERE r.status = 'failed') AS failed,
           count(*) FILTER (WHERE r.status = 'done') AS done,
           array_agg(DISTINCT s.name ORDER BY s.name) AS signals,
           max(r.finished_at) AS last_seen_at,
           min(r.not_before) FILTER (WHERE r.status = 'pending') AS next_retry_at
    FROM signal_runs r
    JOIN signal_versions v ON v.id = r.signal_version_id
    JOIN signals s ON s.id = v.signal_id
    WHERE r.last_error IS NOT NULL
      AND (r.status IN ('pending', 'running') OR r.finished_at >= now() - {ERROR_WINDOW})
    GROUP BY r.last_error
  UNION ALL
    SELECT 'summaries', summary.last_error,
           count(*) FILTER (WHERE summary.status = 'pending'),
           count(*) FILTER (WHERE summary.status = 'failed'),
           count(*) FILTER (WHERE summary.status = 'done'),
           array_agg(DISTINCT s.name ORDER BY s.name),
           max(summary.updated_at),
           min(summary.not_before) FILTER (WHERE summary.status = 'pending')
    FROM event_summaries summary
    JOIN signal_events event ON event.id = summary.event_id
    JOIN signal_versions v ON v.id = event.signal_version_id
    JOIN signals s ON s.id = v.signal_id
    WHERE summary.last_error IS NOT NULL
      AND (summary.status = 'pending' OR summary.finished_at >= now() - {ERROR_WINDOW})
    GROUP BY summary.last_error
) grouped
-- Happening now first (items still pending or running, most first), then the rest newest first.
ORDER BY (pending_or_running > 0) DESC, pending_or_running DESC, last_seen_at DESC NULLS LAST, stage, message
LIMIT %s
"""


def errors(cur):
    rows = cur.execute(ERRORS_SQL, (MAX_ERROR_GROUPS + 1,)).fetchall()
    return Errors(groups=[ErrorGroup(**r) for r in rows[:MAX_ERROR_GROUPS]], more=len(rows) > MAX_ERROR_GROUPS)


# ------------------------------------------------------------------ latest judge decisions

class LatestDecision(BaseModel):
    run_id: int
    signal_id: int
    signal_name: str
    status: str                        # 'done' or 'failed'
    finished_at: datetime
    findings: list[str]                # severities, in finding order; [] = found nothing (or failed)
    dropped_findings: int              # removed because their evidence didn't check out
    analysis: str | None               # the judge's reasoning (done runs)
    error: str | None                  # why it failed (failed runs only; a done run's earlier error isn't shown)


# The newest finished runs are picked first, then each one's findings are looked up, so the cost
# stays at 10 runs however many runs exist.
LATEST_DECISIONS_SQL = """
SELECT run.id AS run_id, version.signal_id, signal.name AS signal_name, run.status, run.finished_at,
       coalesce((SELECT array_agg(finding.severity ORDER BY finding.finding_index)
                 FROM signal_events finding WHERE finding.run_id = run.id), '{}') AS findings,
       run.dropped_findings, run.analysis,
       CASE WHEN run.status = 'failed' THEN run.last_error END AS error
FROM (SELECT * FROM signal_runs WHERE status IN ('done', 'failed')
      ORDER BY finished_at DESC, id DESC LIMIT %s) run
JOIN signal_versions version ON version.id = run.signal_version_id
JOIN signals signal ON signal.id = version.signal_id
ORDER BY run.finished_at DESC, run.id DESC
"""


def latest_decisions(cur):
    return [LatestDecision(**row) for row in cur.execute(LATEST_DECISIONS_SQL, (LATEST_DECISIONS,)).fetchall()]


# ------------------------------------------------------------------ latest summaries

class LatestSummary(BaseModel):
    event_id: int
    signal_id: int
    signal_name: str
    finished_at: datetime
    prompt_version: int
    cause: str | None
    summary: str
    landed: str                        # 'cluster', 'pool' (embedded, no cluster yet) or 'not_embedded'
    cluster_id: int | None
    cluster_name: str | None           # None for a cluster that isn't named yet


LATEST_SUMMARIES_SQL = """
SELECT summary.event_id, version.signal_id, signal.name AS signal_name, summary.finished_at,
       summary.prompt_version, summary.cause, summary.summary,
       CASE WHEN member.event_id IS NOT NULL THEN 'cluster'
            WHEN embedded.event_id IS NOT NULL THEN 'pool'
            ELSE 'not_embedded' END AS landed,
       member.cluster_id, cluster.name AS cluster_name
FROM event_summaries summary
JOIN signal_events event ON event.id = summary.event_id
JOIN signal_versions version ON version.id = event.signal_version_id
JOIN signals signal ON signal.id = version.signal_id
LEFT JOIN event_embeddings embedded ON embedded.event_id = summary.event_id
LEFT JOIN cluster_members member ON member.event_id = summary.event_id
LEFT JOIN clusters cluster ON cluster.id = member.cluster_id
WHERE summary.status = 'done'
ORDER BY summary.finished_at DESC, summary.event_id DESC
LIMIT %s
"""


def latest_summaries(cur):
    return [LatestSummary(**row) for row in cur.execute(LATEST_SUMMARIES_SQL, (LATEST_SUMMARIES,)).fetchall()]


# ------------------------------------------------------------------ the endpoint

class DevStatus(BaseModel):
    as_of: datetime                    # the database's clock: measure ages from this, not the browser's
    pipeline: Pipeline
    errors: Errors
    latest_decisions: list[LatestDecision]
    latest_summaries: list[LatestSummary]


def one(cur, sql, params=None):
    return cur.execute(sql, params).fetchone()


@router.get("/status", response_model=DevStatus)
def status(conn=Depends(get_conn)):
    # One transaction, so now() is the same instant in every section's cutoffs.
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        now = one(cur, "SELECT now() AS now")["now"]
        return DevStatus(as_of=now, pipeline=pipeline(cur, now), errors=errors(cur),
                         latest_decisions=latest_decisions(cur), latest_summaries=latest_summaries(cur))
