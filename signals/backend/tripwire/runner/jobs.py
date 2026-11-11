"""The job table's SQL: claiming a run, loading what the judge needs, finishing it, and
sweeping runs that can never finish.

Every finish checks the run is still `running` with the attempts value it was claimed with.
If the lease expired and another runner claimed it, the check matches nothing and the late
result is thrown away.
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from ..judge.types import JudgeError, JudgeResult, SignalVersion, TraceRow, Usage


@dataclass(frozen=True)
class Claim:
    run_id: int
    attempts: int          # the value this runner claimed with
    created_at: datetime


# Oldest live run first, then backfill. Skips runs another runner is claiming right now,
# runs waiting out a backoff, and runs of turned-off signals. An expired lease is up for grabs.
CLAIM_SQL = """
UPDATE signal_runs
SET status = 'running', attempts = attempts + 1, not_before = NULL,
    lease_until = now() + make_interval(secs => %(lease)s),
    started_at = coalesce(started_at, now())
WHERE id = (
    SELECT r.id
    FROM signal_runs r
    JOIN signal_versions v ON v.id = r.signal_version_id
    JOIN signals s ON s.id = v.signal_id
    WHERE s.active
      AND r.attempts < %(max_attempts)s
      AND (r.status = 'pending' OR (r.status = 'running' AND r.lease_until < now()))
      AND (r.not_before IS NULL OR r.not_before <= now())
    ORDER BY (r.source = 'backfill'), r.created_at, r.id
    LIMIT 1
    FOR UPDATE OF r SKIP LOCKED
)
RETURNING id, attempts, created_at
"""

LOAD_SQL = """
SELECT t.id, t.laminar_trace_id::text, t.session_id, t.started_at, t.ended_at, t.status,
       v.id, v.signal_id, s.name, v.instruction, v.critical_guidance, v.output_schema, v.model,
       p.laminar_api_url
FROM signal_runs r
JOIN traces t ON t.id = r.trace_id
JOIN projects p ON p.id = t.project_id
JOIN signal_versions v ON v.id = r.signal_version_id
JOIN signals s ON s.id = v.signal_id
WHERE r.id = %s
"""

# Adds one attempt's usage to the run's totals. NULL input_tokens = nothing recorded yet, so
# the first cost is simply stored; after that, any unknown cost makes the total unknown.
# SET expressions read the row's old values, so the CASE sees the totals before this update.
ADD_USAGE = """
    input_tokens = coalesce(input_tokens, 0) + %(input_tokens)s,
    cached_input_tokens = coalesce(cached_input_tokens, 0) + %(cached_input_tokens)s,
    output_tokens = coalesce(output_tokens, 0) + %(output_tokens)s,
    cost_usd = CASE WHEN input_tokens IS NULL THEN %(cost_usd)s::numeric
                    WHEN cost_usd IS NULL OR %(cost_usd)s::numeric IS NULL THEN NULL
                    ELSE cost_usd + %(cost_usd)s::numeric END
"""

STILL_OURS = "WHERE id = %(id)s AND status = 'running' AND attempts = %(attempts)s"

DONE_SQL = f"""
UPDATE signal_runs
SET status = 'done', lease_until = NULL, finished_at = now(), model = %(model)s,
    analysis = %(analysis)s, raw_output = %(raw_output)s, dropped_findings = %(dropped)s,
    {ADD_USAGE}
{STILL_OURS}
"""

RETRY_SQL = f"""
UPDATE signal_runs
SET status = 'pending', lease_until = NULL, last_error = %(error)s,
    attempts = attempts - %(give_back)s,
    not_before = now() + make_interval(secs => %(wait)s),
    {ADD_USAGE}
{STILL_OURS}
"""

FAIL_SQL = f"""
UPDATE signal_runs
SET status = 'failed', lease_until = NULL, finished_at = now(), last_error = %(error)s,
    rendered_trace = %(rendered)s,
    {ADD_USAGE}
{STILL_OURS}
"""

INSERT_EVENT_SQL = """
INSERT INTO signal_events (run_id, trace_id, signal_version_id, finding_index, severity,
                           payload, span_ids, evidence_labels, judge_input)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# A runner died holding the run's last attempt.
SWEEP_EXPIRED_SQL = """
UPDATE signal_runs
SET status = 'failed', lease_until = NULL, finished_at = now(),
    last_error = 'lease expired after the final attempt'
WHERE status = 'running' AND lease_until < now() AND attempts >= %(max_attempts)s
"""

# Runs of active signals that still aren't finished long after they were created.
GIVE_UP_SQL = """
UPDATE signal_runs r
SET status = 'failed', lease_until = NULL, finished_at = now(),
    last_error = 'gave up: not finished within ' || %(hours)s::text || ' hours'
                 || coalesce(' (last error: ' || r.last_error || ')', '')
FROM signal_versions v
JOIN signals s ON s.id = v.signal_id
WHERE v.id = r.signal_version_id AND s.active
  AND r.created_at < now() - make_interval(hours => %(hours)s::int)
  AND (r.status = 'pending' OR (r.status = 'running' AND r.lease_until < now()))
"""


def claim(conn, settings):
    row = conn.execute(CLAIM_SQL, {"lease": settings.lease_seconds,
                                   "max_attempts": settings.max_attempts}).fetchone()
    return Claim(*row) if row else None


def load(conn, run_id):
    """The run's trace and signal version, and its project's Laminar API URL."""
    r = conn.execute(LOAD_SQL, (run_id,)).fetchone()
    trace = TraceRow(id=r[0], laminar_trace_id=r[1], session_id=r[2], started_at=r[3],
                     ended_at=r[4], status=r[5])
    version = SignalVersion(id=r[6], signal_id=r[7], name=r[8], instruction=r[9],
                            critical_guidance=r[10], output_schema=r[11], model=r[12])
    return trace, version, r[13]


def _usage(usage: Usage):
    return {"input_tokens": usage.input_tokens, "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens, "cost_usd": usage.cost_usd}


def record_success(conn, claimed: Claim, trace_id, version_id, result: JudgeResult):
    """Run done plus one event per finding, in one transaction. False if the run isn't ours."""
    with conn.transaction():
        cur = conn.execute(DONE_SQL, {
            "id": claimed.run_id, "attempts": claimed.attempts, "model": result.model,
            "analysis": result.analysis, "raw_output": result.raw_output,
            "dropped": result.dropped_findings, **_usage(result.usage)})
        if cur.rowcount == 0:
            raise psycopg.Rollback()
        for index, finding in enumerate(result.findings):
            conn.execute(INSERT_EVENT_SQL, (claimed.run_id, trace_id, version_id, index,
                                            finding.severity, Jsonb(finding.fields), finding.span_ids,
                                            finding.evidence_labels, result.judge_input))
    return cur.rowcount == 1


def record_retry(conn, claimed: Claim, error: JudgeError, wait_seconds):
    """Back to pending until now + wait. A service error gives its attempt back."""
    cur = conn.execute(RETRY_SQL, {
        "id": claimed.run_id, "attempts": claimed.attempts, "error": str(error),
        "give_back": 0 if error.uses_attempt else 1, "wait": wait_seconds, **_usage(error.usage)})
    return cur.rowcount == 1


def record_failure(conn, claimed: Claim, error: JudgeError):
    cur = conn.execute(FAIL_SQL, {
        "id": claimed.run_id, "attempts": claimed.attempts, "error": str(error),
        "rendered": error.rendered_trace, **_usage(error.usage)})
    return cur.rowcount == 1


def sweep(conn, settings):
    """Fail runs that can never finish. Returns (expired on the last attempt, given up)."""
    expired = conn.execute(SWEEP_EXPIRED_SQL, {"max_attempts": settings.max_attempts}).rowcount
    gave_up = conn.execute(GIVE_UP_SQL, {"hours": settings.give_up_hours}).rowcount
    return expired, gave_up