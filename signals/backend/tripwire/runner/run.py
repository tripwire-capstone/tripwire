"""PLACEHOLDER signal runner: finishes pending runs without an LLM.

The poller creates a pending run for every new trace and matching signal. This step
takes up to `batch` of them and, for each:
  * marks it `done` (no tokens, no cost)
  * for about STUB_EVENT_RATE of them (decided by the trace ID's hash), also writes a
    canned event

Runs of a turned-off signal are left pending. To be replaced by the real job flow
(claiming with leases, retries, the judge).
"""

import logging

from ..placeholder import picked

log = logging.getLogger("runner")

PENDING_SQL = """
SELECT r.id, r.trace_id, t.laminar_trace_id::text, r.signal_version_id
FROM signal_runs r
JOIN traces t ON t.id = r.trace_id
JOIN signal_versions v ON v.id = r.signal_version_id
JOIN signals s ON s.id = v.signal_id
WHERE r.status = 'pending' AND s.active
ORDER BY r.created_at, r.id
LIMIT %s
FOR UPDATE OF r SKIP LOCKED
"""


def run_step(conn, event_rate, batch=100):
    """Finish up to `batch` pending runs in one transaction. Returns (runs, events)."""
    runs = events = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(PENDING_SQL, (batch,))
        for run_id, trace_id, laminar_trace_id, version_id in cur.fetchall():
            cur.execute(
                """UPDATE signal_runs
                   SET status = 'done', attempts = 1, model = 'placeholder',
                       input_tokens = 0, cached_input_tokens = 0, output_tokens = 0, cost_usd = 0,
                       started_at = now(), finished_at = now()
                   WHERE id = %s""",
                (run_id,),
            )
            runs += 1
            if picked(f"event:{laminar_trace_id}", event_rate):
                cur.execute(
                    """INSERT INTO signal_events
                           (run_id, trace_id, signal_version_id, severity, description, judge_input)
                       VALUES (%s, %s, %s, 'warning', %s, 'placeholder')""",
                    (run_id, trace_id, version_id,
                     f"Placeholder event for trace {laminar_trace_id[:8]}"),
                )
                events += 1
    return runs, events
