"""One poll: find turns that finished in Laminar, build their items, store them.

    marker  = newest turn_ended_at already in `turns` (live rows)
    query 1 = root spans that ended after (marker - overlap)     -> candidate turns
              and at least `settle` seconds ago
    query 2 = root + tool spans of those turns' conversations    -> build_item input
    insert  = one transaction, ON CONFLICT DO NOTHING            -> the marker moves too

Values reach the SQL as Laminar `parameters` ({name:Type} placeholders), never by
pasting them into the query text.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from .items import ROOT_PARENT_ID, ItemError, build_item, parse_laminar_time

log = logging.getLogger("sync")

SESSION_ID = "JSONExtractString(attributes, 'lmnr.association.properties.session_id')"

# Query 1: new turns, i.e. root spans that ended between `since` (marker - overlap)
# and `until` (now - settling delay). The settling delay gives a conversation's
# earlier turns time to reach Laminar before a later turn's item is built, in case
# they were exported out of order. The loose start_time bound lets ClickHouse use
# the start_time-ordered copy of spans and skip old data; a turn always starts
# before it ends and never takes an hour, so it never excludes a real match.
# Times are read as UTC strings explicitly; aliases differ from the column names
# so WHERE still sees the real columns.
NEW_TURNS_SQL = f"""
SELECT
    trace_id,
    {SESSION_ID} AS session_id,
    toString(end_time, 'UTC') AS ended_utc,
    status
FROM spans
WHERE start_time > toDateTime64({{since:String}}, 6, 'UTC') - INTERVAL 1 HOUR
  AND end_time > toDateTime64({{since:String}}, 6, 'UTC')
  AND end_time <= toDateTime64({{until:String}}, 6, 'UTC')
  AND toString(parent_span_id) = '{ROOT_PARENT_ID}'
ORDER BY end_time
"""

# Query 2: whole conversations (the step 2 query, for many conversations at once).
# traces has an index on session_id; spans is sorted by trace_id, so fetching spans
# by trace_id is the fast path.
CONVERSATIONS_SQL = f"""
SELECT
    trace_id,
    span_id,
    parent_span_id,
    name,
    span_type,
    toString(start_time, 'UTC') AS started_utc,
    toString(end_time, 'UTC') AS ended_utc,
    status,
    input,
    output,
    {SESSION_ID} AS session_id
FROM spans
WHERE trace_id IN (
        SELECT id
        FROM traces
        WHERE session_id IN {{sessions:Array(String)}}
          AND start_time > now() - toIntervalDay({{window_days:UInt32}})
    )
  AND start_time > now() - toIntervalDay({{window_days:UInt32}})
  AND (toString(parent_span_id) = '{ROOT_PARENT_ID}' OR span_type = 'TOOL')
ORDER BY start_time
"""


@dataclass
class PollResult:
    since: datetime
    until: datetime
    seen: int = 0              # root spans query 1 returned
    already_stored: int = 0    # re-read inside the overlap; skipped
    crashed: int = 0           # never targets (contract decision 6); skipped on purpose
    failed: int = 0            # bad data; logged and skipped (not retried)
    written: int = 0
    failures: list = field(default_factory=list)


def poll_once(laminar, conn, *, overlap=timedelta(seconds=60), settle=timedelta(seconds=15),
              first_run_lookback=timedelta(hours=24), window_days=7, now=None):
    now = now or datetime.now(timezone.utc)
    marker = read_marker(conn)
    since = (marker if marker is not None else now - first_run_lookback) - overlap
    until = now - settle
    result = PollResult(since=since, until=until)

    roots = laminar.query(NEW_TURNS_SQL, {"since": _ch_time(since), "until": _ch_time(until)})
    result.seen = len(roots)

    stored = stored_trace_ids(conn, [root["trace_id"] for root in roots])
    candidates = []
    for root in roots:
        if root["trace_id"] in stored:
            result.already_stored += 1
        elif root["status"] == "error":
            result.crashed += 1
        elif not root["session_id"]:
            _fail(result, root["trace_id"], "root span has no session_id attribute")
        else:
            candidates.append(root)

    if candidates:
        sessions = sorted({root["session_id"] for root in candidates})
        rows = laminar.query(CONVERSATIONS_SQL,
                             {"sessions": sessions, "window_days": window_days})
        by_session = group_by_session(rows)

        new_rows = []
        for root in candidates:
            try:
                item = build_item(by_session.get(root["session_id"], []),
                                  root["trace_id"], root["session_id"])
            except ItemError as e:
                _fail(result, root["trace_id"], str(e))
                continue
            new_rows.append((item, parse_laminar_time(root["ended_utc"])))

        result.written = insert_turns(conn, new_rows)

    return result


def group_by_session(rows):
    """Query 2 rows -> {session_id: rows in build_item's shape}."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["session_id"]].append({
            **{k: v for k, v in row.items()
               if k not in ("session_id", "started_utc", "ended_utc")},
            "start_time": row["started_utc"],
            "end_time": row["ended_utc"],
        })
    return dict(grouped)


def read_marker(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT max(turn_ended_at) FROM turns WHERE source = 'live'")
        return cur.fetchone()[0]


def stored_trace_ids(conn, trace_ids):
    if not trace_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT trace_id::text FROM turns WHERE trace_id = ANY(%s::uuid[])",
                    (trace_ids,))
        return {row[0] for row in cur.fetchall()}


def insert_turns(conn, new_rows):
    """Insert (item, ended_at) pairs in one transaction; return how many were new."""
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        for item, ended_at in new_rows:
            cur.execute(
                "INSERT INTO turns (source, turn_ended_at, item) VALUES ('live', %s, %s) "
                "ON CONFLICT DO NOTHING",
                (ended_at, Jsonb(item)),
            )
            written += cur.rowcount
    return written


def _fail(result, trace_id, reason):
    result.failed += 1
    result.failures.append((trace_id, reason))
    log.warning("skipping turn %s: %s", trace_id, reason)


def _ch_time(moment):
    """Aware datetime -> ClickHouse parameter text, in UTC."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
