"""One poll of one project: find traces that finished in Laminar and store them.

    marker = newest ended_at already in `traces` for this project
    query  = root spans that ended after (marker - overlap)
             and at least `settle` seconds ago            -> one row per trace
    insert = one transaction, ON CONFLICT DO NOTHING      -> the marker moves too

Values reach the SQL as Laminar `parameters` ({name:Type} placeholders), never by
pasting them into the query text.

Adapted from the prototype's sync/poll.py. What changed: no conversation rebuilding
(the judge reads a trace's spans itself later), so there's one Laminar query instead
of two; traces that errored or have no session ID are stored, not skipped.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


@dataclass
class PollResult:
    since: datetime
    until: datetime
    seen: int = 0             # root spans the query returned
    already_stored: int = 0   # re-read inside the overlap; skipped
    written: int = 0


def poll_once(laminar, conn, project_id, *, overlap=timedelta(seconds=60),
              settle=timedelta(seconds=15), first_run_lookback=timedelta(hours=24), now=None):
    now = now or datetime.now(timezone.utc)
    marker = read_marker(conn, project_id)
    since = (marker if marker is not None else now - first_run_lookback) - overlap
    until = now - settle
    result = PollResult(since=since, until=until)

    roots = laminar.query(NEW_TRACES_SQL, {"since": _ch_time(since), "until": _ch_time(until)})
    result.seen = len(roots)
    result.written = insert_traces(conn, project_id, roots)
    result.already_stored = result.seen - result.written
    return result


def read_marker(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT max(ended_at) FROM traces WHERE project_id = %s", (project_id,))
        return cur.fetchone()[0]


def insert_traces(conn, project_id, roots):
    """Insert one row per root span in one transaction; return how many were new.

    Also records the successful poll in projects.last_polled_at, in the same
    transaction, so it only moves when the poll really finished.
    """
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        for root in roots:
            cur.execute(
                """INSERT INTO traces
                       (project_id, laminar_trace_id, session_id, started_at, ended_at, status)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (project_id, root["trace_id"], root["session_id"] or None,
                 parse_laminar_time(root["started_utc"]), parse_laminar_time(root["ended_utc"]),
                 "error" if root["status"] == "error" else "success"),
            )
            written += cur.rowcount
        cur.execute("UPDATE projects SET last_polled_at = now() WHERE id = %s", (project_id,))
    return written


def parse_laminar_time(text):
    """'2026-10-26 18:32:49.312142973' (UTC, nanoseconds) -> aware datetime (microseconds)."""
    return datetime.fromisoformat(text[:26]).replace(tzinfo=timezone.utc)


def _ch_time(moment):
    """Aware datetime -> ClickHouse parameter text, in UTC."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
