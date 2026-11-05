"""Tests for sync/poll.py: a fake Laminar and a real Postgres.

The Postgres tests need TEST_DATABASE_URL (any database you can create schemas in).
Each test runs in its own throwaway schema, which is dropped afterwards, so your
real `turns` table is never touched. Without TEST_DATABASE_URL they're skipped.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sync.items import ROOT_PARENT_ID
from sync.poll import CONVERSATIONS_SQL, NEW_TURNS_SQL, group_by_session, poll_once

REPO = Path(__file__).resolve().parents[2]
CONVO = "convo-7fbf383a-bc4c-4ef2-ae09-429067df8d46"
SMOKE_ROWS = json.loads((Path(__file__).parent / "fixtures" / "smoke_convo.json").read_text())
NOW = datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc)  # half an hour after the smoke run


# ------------------------------------------------------------------ fake Laminar

def as_query2_row(row, session_id):
    """A smoke fixture row in query 2's shape (UTC aliases + session_id)."""
    out = {k: v for k, v in row.items() if k not in ("start_time", "end_time")}
    return {**out, "started_utc": row["start_time"], "ended_utc": row["end_time"],
            "session_id": session_id}


def as_query1_row(row, session_id):
    return {"trace_id": row["trace_id"], "session_id": session_id,
            "ended_utc": row["end_time"], "status": row["status"]}


class FakeLaminar:
    """Answers the two poll queries from a list of query-2-shaped rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        if sql == NEW_TURNS_SQL:
            since = datetime.fromisoformat(parameters["since"]).replace(tzinfo=timezone.utc)
            until = datetime.fromisoformat(parameters["until"]).replace(tzinfo=timezone.utc)
            return [as_query1_row({**r, "end_time": r["ended_utc"]}, r["session_id"])
                    for r in self.rows
                    if r["parent_span_id"] == ROOT_PARENT_ID
                    and since < _t(r["ended_utc"]) <= until]
        if sql == CONVERSATIONS_SQL:
            return [r for r in self.rows if r["session_id"] in parameters["sessions"]]
        raise AssertionError(f"unexpected query: {sql[:60]}")

    def queries(self, sql):
        return [params for s, params in self.calls if s == sql]


def _t(text):
    return datetime.fromisoformat(text[:26]).replace(tzinfo=timezone.utc)


def smoke(session_id=CONVO):
    return [as_query2_row(r, session_id) for r in SMOKE_ROWS]


# ------------------------------------------------------------------ throwaway Postgres schema

@pytest.fixture
def conn():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    import psycopg
    schema = f"sync_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path TO {schema}")
        c.execute((REPO / "schema.sql").read_text())
        try:
            yield c
        finally:
            c.execute(f"DROP SCHEMA {schema} CASCADE")


def live_rows(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT trace_id::text, target_index, item FROM turns "
                    "WHERE source = 'live' ORDER BY turn_ended_at")
        return cur.fetchall()


# ------------------------------------------------------------------ grouping (no database)

def test_group_by_session_renames_time_columns():
    rows = smoke("a") + smoke("b")[:1]
    grouped = group_by_session(rows)
    assert sorted(grouped) == ["a", "b"]
    assert len(grouped["a"]) == 4 and len(grouped["b"]) == 1
    first = grouped["a"][0]
    assert first["start_time"] == "2026-10-26 18:32:46.549806073"
    assert "session_id" not in first and "started_utc" not in first


# ------------------------------------------------------------------ polling (Postgres)

def test_first_poll_writes_every_finished_turn(conn):
    laminar = FakeLaminar(smoke())
    result = poll_once(laminar, conn, now=NOW)

    assert result.written == 3
    assert result.since == NOW - timedelta(hours=24) - timedelta(seconds=60)
    rows = live_rows(conn)
    assert [r[1] for r in rows] == [1, 2, 3]
    assert [len(r[2]["turns"]) for r in rows] == [1, 2, 3]   # Rule 6: no later turns
    assert len(laminar.queries(CONVERSATIONS_SQL)) == 1         # one query for all of them


def test_second_poll_writes_nothing_and_skips_query_2(conn):
    laminar = FakeLaminar(smoke())
    poll_once(laminar, conn, now=NOW)
    result = poll_once(laminar, conn, now=NOW)

    assert result.written == 0
    assert len(laminar.queries(CONVERSATIONS_SQL)) == 1  # not called again


def test_since_is_marker_minus_overlap(conn):
    laminar = FakeLaminar(smoke())
    poll_once(laminar, conn, now=NOW)
    result = poll_once(laminar, conn, now=NOW)
    # Turn 3 ended at 18:32:54.688809; marker - 60 s = 18:31:54.688809.
    assert result.since == datetime(2026, 10, 26, 18, 31, 54, 688809, tzinfo=timezone.utc)
    assert laminar.queries(NEW_TURNS_SQL)[-1] == {"since": "2026-10-26 18:31:54.688809",
                                                  "until": "2026-10-26 18:59:45.000000"}
    assert result.already_stored == 3  # all three re-read inside the overlap


def test_turn_arriving_late_inside_the_overlap_is_still_written(conn):
    # Conversation B's turn ended at 18:32:50, before conversation A's last turn
    # (18:32:54.7, which becomes the marker), but only shows up after the first poll.
    late = as_query2_row({**SMOKE_ROWS[2], "trace_id": "c" * 8 + SMOKE_ROWS[2]["trace_id"][8:],
                          "span_id": "late-root", "start_time": "2026-10-26 18:32:48.000000000",
                          "end_time": "2026-10-26 18:32:50.000000000"}, "convo-B")
    laminar = FakeLaminar(smoke())
    poll_once(laminar, conn, now=NOW)
    laminar.rows = smoke() + [late]
    result = poll_once(laminar, conn, now=NOW)
    assert result.written == 1
    assert len(live_rows(conn)) == 4


def test_turn_arriving_later_than_the_overlap_is_missed(conn):
    # The documented limit: the same turn arriving more than 60 s behind the marker.
    late = as_query2_row({**SMOKE_ROWS[2], "trace_id": "c" * 8 + SMOKE_ROWS[2]["trace_id"][8:],
                          "span_id": "late-root", "start_time": "2026-10-26 18:31:00.000000000",
                          "end_time": "2026-10-26 18:31:30.000000000"}, "convo-B")
    laminar = FakeLaminar(smoke())
    poll_once(laminar, conn, now=NOW)
    laminar.rows = smoke() + [late]
    assert poll_once(laminar, conn, now=NOW).written == 0


def test_turn_waits_for_the_settling_delay(conn):
    # Turns ended at 18:32:49.3, 52.1 and 54.7. At 18:33:08 (settled up to 18:32:53)
    # turns 1 and 2 have settled and turn 3 hasn't; at 18:33:10 all three have.
    laminar = FakeLaminar(smoke())
    early = poll_once(laminar, conn, now=datetime(2026, 10, 26, 18, 33, 8, tzinfo=timezone.utc))
    assert early.written == 2
    assert [r[1] for r in live_rows(conn)] == [1, 2]
    later = poll_once(laminar, conn, now=datetime(2026, 10, 26, 18, 33, 10, tzinfo=timezone.utc))
    assert later.written == 1
    assert [r[1] for r in live_rows(conn)] == [1, 2, 3]


def test_out_of_order_turn_gets_full_history_after_settling(conn):
    # Turn 2 reaches Laminar after turn 3 (different machines). Turn 3 isn't built
    # until it has settled, by which time turn 2 is there, so its history is complete.
    rows = smoke()
    turn_2 = [r for r in rows if r["trace_id"].startswith("364f7d71")]
    laminar = FakeLaminar([r for r in rows if r not in turn_2])
    at = datetime(2026, 10, 26, 18, 33, 0, tzinfo=timezone.utc)   # turn 3 ended 5 s ago
    poll_once(laminar, conn, now=at)                               # only turn 1 is written
    laminar.rows = rows                                            # turn 2 arrives
    poll_once(laminar, conn, now=at + timedelta(seconds=15))
    items = {r[1]: r[2] for r in live_rows(conn)}
    assert sorted(items) == [1, 2, 3]
    assert [t["index"] for t in items[3]["turns"]] == [1, 2, 3]


def test_crashed_turn_is_skipped_without_counting_as_a_failure(conn):
    rows = smoke()
    for r in rows:
        if r["trace_id"] == "bf482284-34d2-c490-4973-66d011fce2ca":
            r["status"], r["output"] = "error", ""
    result = poll_once(FakeLaminar(rows), conn, now=NOW)
    assert (result.written, result.crashed, result.failed) == (2, 1, 0)


def test_bad_conversation_does_not_block_others(conn):
    good, bad = smoke("convo-good"), smoke("convo-bad")
    for r in bad:
        r["trace_id"] = "b" + r["trace_id"][1:]   # distinct trace ids
        if r["parent_span_id"] == ROOT_PARENT_ID:
            r["input"] = "not json"
    result = poll_once(FakeLaminar(good + bad), conn, now=NOW)
    assert result.written == 3
    assert result.failed == 3
    assert all(trace.startswith("b") for trace, _ in result.failures)


def test_root_without_session_id_is_skipped(conn):
    result = poll_once(FakeLaminar(smoke(session_id="")), conn, now=NOW)
    assert (result.written, result.failed) == (0, 3)


def test_nothing_new_makes_one_query(conn):
    laminar = FakeLaminar([])
    result = poll_once(laminar, conn, now=NOW)
    assert result.written == 0
    assert len(laminar.calls) == 1
