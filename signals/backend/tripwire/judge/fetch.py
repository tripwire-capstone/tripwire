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
    spans = laminar.query(SPANS_SQL, _span_parameters(trace_id, started_at))
    return _require_spans(trace_id, spans)


async def fetch_spans_async(
    laminar, trace_id: str, started_at: datetime, *, deadline: float
) -> list[dict]:
    """Fetch the current turn's spans within the caller's deadline."""
    spans = await laminar.query(
        SPANS_SQL,
        _span_parameters(trace_id, started_at),
        deadline=deadline,
    )
    return _require_spans(trace_id, spans)


def fetch_earlier_turns(
    laminar, session_id: str | None, started_at: datetime
) -> list[dict]:
    """Return up to five earlier root inputs and outputs, oldest first."""
    if not session_id:
        return []

    traces = laminar.query(
        EARLIER_TRACES_SQL, _earlier_trace_parameters(session_id, started_at)
    )
    if not traces:
        return []

    roots = laminar.query(EARLIER_ROOTS_SQL, _earlier_root_parameters(traces))
    return _assemble_earlier_turns(traces, roots)


async def fetch_earlier_turns_async(
    laminar, session_id: str | None, started_at: datetime, *, deadline: float
) -> list[dict]:
    """Fetch earlier turns using one deadline for the trace and root queries."""
    if not session_id:
        return []

    traces = await laminar.query(
        EARLIER_TRACES_SQL,
        _earlier_trace_parameters(session_id, started_at),
        deadline=deadline,
    )
    if not traces:
        return []

    roots = await laminar.query(
        EARLIER_ROOTS_SQL, _earlier_root_parameters(traces), deadline=deadline
    )
    return _assemble_earlier_turns(traces, roots)


def _utc_start(started_at: datetime) -> datetime:
    if started_at.utcoffset() is None:
        raise ValueError("Trace start time must include a timezone")
    return started_at.astimezone(timezone.utc)


def _span_parameters(trace_id: str, started_at: datetime) -> dict:
    lower_bound = _utc_start(started_at) - timedelta(hours=1)
    return {
        "trace_id": trace_id,
        "since": lower_bound.strftime("%Y-%m-%d %H:%M:%S.%f"),
    }


def _require_spans(trace_id: str, spans: list[dict]) -> list[dict]:
    if not spans:
        raise LookupError(f"No spans found for Laminar trace {trace_id}")
    return spans


def _earlier_trace_parameters(session_id: str, started_at: datetime) -> dict:
    before = _utc_start(started_at).strftime("%Y-%m-%d %H:%M:%S.%f")
    return {"session_id": session_id, "before": before}


def _earlier_root_parameters(traces: list[dict]) -> dict:
    lower_bound = min(parse_laminar_time(trace["started_utc"]) for trace in traces)
    lower_bound -= timedelta(hours=1)
    return {
        "trace_ids": [trace["trace_id"] for trace in traces],
        "root_parent_id": ROOT_PARENT_ID,
        "since": lower_bound.strftime("%Y-%m-%d %H:%M:%S.%f"),
    }


def _assemble_earlier_turns(traces: list[dict], roots: list[dict]) -> list[dict]:
    roots_by_id = {root["trace_id"]: root for root in roots}
    return [
        {"input": root["input"], "output": root["output"]}
        for trace in reversed(traces)
        if (root := roots_by_id.get(trace["trace_id"])) is not None
    ]
