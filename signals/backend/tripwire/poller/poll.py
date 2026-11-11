"""One poll of one project: find traces that finished in Laminar, store them, and create
their signal runs.

    marker = newest ended_at already in `traces` for this project
    query  = root spans that ended after (marker - overlap)
             and at least `settle` seconds ago            -> one row per trace
    insert = one transaction: new traces, a pending run for every active signal whose
             filters match each new trace, and the marker moves too

Values reach the SQL as Laminar `parameters` ({name:Type} placeholders), never by
pasting them into the query text.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..filters import FilterError, matches

log = logging.getLogger("poller")

# Every span of a trace hangs under one root span, whose parent ID is all zeros.
ROOT_PARENT_ID = "00000000-0000-0000-0000-000000000000"
SESSION_ID = "JSONExtractString(attributes, 'lmnr.association.properties.session_id')"

# New traces: root spans that ended between `since` (marker - overlap) and `until`
# (now - settling delay). The settling delay gives a trace's child spans time to
# reach Laminar: they can be exported in a later batch than the root, and the judge
# should see the whole trace. The loose start_time bound lets ClickHouse use the
# start_time-ordered copy of spans and skip old data; a trace that takes over an hour
# would be missed (fine for chat turns; revisit for long agent runs). Times are read
# as UTC strings explicitly; aliases differ from the column names so WHERE still sees
# the real columns.
NEW_TRACES_SQL = f"""
SELECT
    trace_id,
    {SESSION_ID} AS session_id,
    toString(start_time, 'UTC') AS started_utc,
    toString(end_time, 'UTC') AS ended_utc,
    status
FROM spans
WHERE start_time > toDateTime64({{since:String}}, 6, 'UTC') - INTERVAL 1 HOUR
  AND end_time > toDateTime64({{since:String}}, 6, 'UTC')
  AND end_time <= toDateTime64({{until:String}}, 6, 'UTC')
  AND toString(parent_span_id) = '{ROOT_PARENT_ID}'
ORDER BY end_time
"""

# The current (highest-numbered) version of every active signal in the project.
ACTIVE_VERSIONS_SQL = """
SELECT DISTINCT ON (v.signal_id) v.signal_id, v.id, v.filters
FROM signal_versions v
JOIN signals s ON s.id = v.signal_id
WHERE s.project_id = %s AND s.active
ORDER BY v.signal_id, v.version_number DESC
"""


@dataclass
class PollResult:
    since: datetime
    until: datetime
    seen: int = 0             # root spans the query returned
    already_stored: int = 0   # re-read inside the overlap; skipped
    written: int = 0
    runs_created: int = 0


def poll_once(laminar, conn, project_id, *, overlap=timedelta(seconds=60),
              settle=timedelta(seconds=15), first_run_lookback=timedelta(hours=24), now=None):
    now = now or datetime.now(timezone.utc)
    marker = read_marker(conn, project_id)
    since = (marker if marker is not None else now - first_run_lookback) - overlap
    until = now - settle
    result = PollResult(since=since, until=until)

    roots = laminar.query(NEW_TRACES_SQL, {"since": _ch_time(since), "until": _ch_time(until)})
    result.seen = len(roots)
    result.written, result.runs_created = insert_traces(conn, project_id, roots)
    result.already_stored = result.seen - result.written
    return result


def read_marker(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT max(ended_at) FROM traces WHERE project_id = %s", (project_id,))
        return cur.fetchone()[0]


def insert_traces(conn, project_id, roots):
    """Store new traces and create their runs, in one transaction.

    Returns (traces written, runs created). Also records the successful poll in
    projects.last_polled_at, so it only moves when the poll really finished.
    A trace that was already stored is skipped entirely: its runs were created
    when it was first stored.
    """
    written = runs = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(ACTIVE_VERSIONS_SQL, (project_id,))
        versions = cur.fetchall()
        for root in roots:
            status = "error" if root["status"] == "error" else "success"
            cur.execute(
                """INSERT INTO traces
                       (project_id, laminar_trace_id, session_id, started_at, ended_at, status)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING
                   RETURNING id""",
                (project_id, root["trace_id"], root["session_id"] or None,
                 parse_laminar_time(root["started_utc"]), parse_laminar_time(root["ended_utc"]),
                 status),
            )
            row = cur.fetchone()
            if row is None:
                continue
            written += 1
            runs += create_runs(cur, row[0], {"status": status}, versions)
        cur.execute("UPDATE projects SET last_polled_at = now() WHERE id = %s", (project_id,))
    return written, runs


def create_runs(cur, trace_id, trace, versions):
    """One pending run per signal version whose filters match the trace. Returns how many."""
    created = 0
    for signal_id, version_id, filters in versions:
        try:
            if not matches(filters, trace):
                continue
        except FilterError as e:
            # One broken signal mustn't stop every poll: skip it and say so.
            log.error("signal %s: %s; it gets no run for this trace", signal_id, e)
            continue
        cur.execute(
            """INSERT INTO signal_runs (trace_id, signal_version_id)
               VALUES (%s, %s)
               ON CONFLICT (trace_id, signal_version_id) DO NOTHING""",
            (trace_id, version_id),
        )
        created += cur.rowcount
    return created


def parse_laminar_time(text):
    """'2026-10-26 18:32:49.312142973' (UTC, nanoseconds) -> aware datetime (microseconds)."""
    return datetime.fromisoformat(text[:26]).replace(tzinfo=timezone.utc)


def _ch_time(moment):
    """Aware datetime -> ClickHouse parameter text, in UTC."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
