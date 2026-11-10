"""PLACEHOLDER signal runner: one step of judging new traces, without an LLM.

For every trace that has no run yet for an active signal's current version:
  * write a `done` run (no tokens, no cost)
  * for about STUB_EVENT_RATE of them (decided by the trace ID's hash), also write a
    canned event

Assumes one runner. To be replaced by the real job flow (fan-out, claiming with
leases, retries; SQL in CONTRACT.md) and the real judge(trace, version).
"""

import logging

from ..placeholder import picked

log = logging.getLogger("runner")

# Traces missing a run for an active signal's current (highest) version.
UNJUDGED_SQL = """
SELECT t.id, t.laminar_trace_id::text, v.id
FROM traces t
JOIN signals s ON s.project_id = t.project_id AND s.active
JOIN LATERAL (
    SELECT id FROM signal_versions
    WHERE signal_id = s.id
    ORDER BY version_number DESC
    LIMIT 1
) v ON true
WHERE NOT EXISTS (
    SELECT 1 FROM signal_runs r
    WHERE r.trace_id = t.id AND r.signal_version_id = v.id
)
ORDER BY t.ended_at
LIMIT %s
"""


def run_step(conn, event_rate, batch=100):
    """Judge up to `batch` (trace, version) pairs in one transaction. Returns (runs, events)."""
    runs = events = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(UNJUDGED_SQL, (batch,))
        for trace_id, laminar_trace_id, version_id in cur.fetchall():
            cur.execute(
                """INSERT INTO signal_runs
                       (trace_id, signal_version_id, status, attempts, model,
                        input_tokens, cached_input_tokens, output_tokens, cost_usd,
                        started_at, finished_at)
                   VALUES (%s, %s, 'done', 1, 'placeholder', 0, 0, 0, 0, now(), now())
                   ON CONFLICT (trace_id, signal_version_id) DO NOTHING
                   RETURNING id""",
                (trace_id, version_id),
            )
            row = cur.fetchone()
            if row is None:  # someone else judged it meanwhile
                continue
            runs += 1
            if picked(f"event:{laminar_trace_id}", event_rate):
                cur.execute(
                    """INSERT INTO signal_events
                           (run_id, trace_id, signal_version_id, severity, description, judge_input)
                       VALUES (%s, %s, %s, 'warning', %s, 'placeholder')""",
                    (row[0], trace_id, version_id,
                     f"Placeholder event for trace {laminar_trace_id[:8]}"),
                )
                events += 1
    return runs, events
