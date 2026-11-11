"""Read trace spans from Laminar for the judge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tripwire.poller.poll import ROOT_PARENT_ID, parse_laminar_time

EARLIER_TRACES_SQL = """
SELECT id AS trace_id, toString(start_time, 'UTC') AS started_utc
FROM traces
WHERE session_id = {session_id:String}
  AND start_time < toDateTime64({before:String}, 6, 'UTC')
  AND end_time < toDateTime64({before:String}, 6, 'UTC')
ORDER BY end_time DESC, id DESC
LIMIT 5
"""

EARLIER_ROOTS_SQL = """
SELECT trace_id, input, output
FROM spans
WHERE trace_id IN {trace_ids:Array(UUID)}
  AND parent_span_id = {root_parent_id:UUID}
  AND start_time >= toDateTime64({since:String}, 6, 'UTC')
"""

SPANS_SQL = """
SELECT
    span_id, parent_span_id, name, span_type,
    toString(start_time, 'UTC') AS started_utc,
    toString(end_time, 'UTC') AS ended_utc,
    input, output, status, events, model
FROM spans
WHERE trace_id = {trace_id:UUID}
  AND start_time >= toDateTime64({since:String}, 6, 'UTC')
ORDER BY start_time, span_id
"""


def fetch_spans(laminar, trace_id: str, started_at: datetime) -> list[dict]:
    """Return the current turn's raw spans in start-time order."""
    if started_at.utcoffset() is None:
        raise ValueError("Trace start time must include a timezone")

    lower_bound = started_at.astimezone(timezone.utc) - timedelta(hours=1)
    spans = laminar.query(
        SPANS_SQL,
        {
            "trace_id": trace_id,
            "since": lower_bound.strftime("%Y-%m-%d %H:%M:%S.%f"),
        },
    )
    if not spans:
        raise LookupError(f"No spans found for Laminar trace {trace_id}")

    return spans


def fetch_earlier_turns(
    laminar, session_id: str | None, started_at: datetime
) -> list[dict]:
    """Return up to five earlier root inputs and outputs, oldest first."""
    if not session_id:
        return []
    if started_at.utcoffset() is None:
        raise ValueError("Trace start time must include a timezone")

    before = started_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    traces = laminar.query(EARLIER_TRACES_SQL, {"session_id": session_id, "before": before})
    if not traces:
        return []

    lower_bound = min(parse_laminar_time(trace["started_utc"]) for trace in traces)
    lower_bound -= timedelta(hours=1)
    roots = laminar.query(
        EARLIER_ROOTS_SQL,
        {
            "trace_ids": [trace["trace_id"] for trace in traces],
            "root_parent_id": ROOT_PARENT_ID,
            "since": lower_bound.strftime("%Y-%m-%d %H:%M:%S.%f"),
        },
    )
    roots_by_id = {root["trace_id"]: root for root in roots}
    return [
        {"input": root["input"], "output": root["output"]}
        for trace in reversed(traces)
        if (root := roots_by_id.get(trace["trace_id"])) is not None
    ]
