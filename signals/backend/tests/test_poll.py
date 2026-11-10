"""Tests for tripwire/poller/poll.py: a fake Laminar and a real Postgres.

The Postgres tests need TEST_DATABASE_URL (a database with the pgvector extension
available, e.g. the compose `db` service). Each test runs in its own throwaway schema,
dropped afterwards, so real tables are never touched. Without TEST_DATABASE_URL
they're skipped.

Adapted from the prototype's sync tests: same polling rules (marker, overlap,
settling delay), without the conversation rebuilding.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tripwire.poller.poll import NEW_TRACES_SQL, ROOT_PARENT_ID, poll_once

NOW = datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc)

# Three traces of one conversation (times from the prototype's smoke run), as Laminar
# returns them: root span rows only.
TRACES = [
    {"trace_id": "25452952-65fa-dee1-0659-1516e32c2387", "start": "2026-10-26 18:32:46.549806073",
     "end": "2026-10-26 18:32:49.312142973"},
    {"trace_id": "364f7d71-4ae8-5abf-9f9c-39cb8b95134a", "start": "2026-10-26 18:32:50.942769640",
     "end": "2026-10-26 18:32:52.060158448"},
    {"trace_id": "bf482284-34d2-c490-4973-66d011fce2ca", "start": "2026-10-26 18:32:53.628183409",
     "end": "2026-10-26 18:32:54.688809751"},
]


def root(t, session_id="convo-1", status="success"):
    return {"trace_id": t["trace_id"], "session_id": session_id, "started_utc": t["start"],
            "ended_utc": t["end"], "status": status, "parent_span_id": ROOT_PARENT_ID}


# ------------------------------------------------------------------ fake Laminar

class FakeLaminar:
    """Answers the poll query from a list of root-span rows, honoring since/until."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        assert sql == NEW_TRACES_SQL, f"unexpected query: {sql[:60]}"
        since = _t(parameters["since"])
        until = _t(parameters["until"])
        return [{k: v for k, v in r.items() if k != "parent_span_id"}
                for r in self.rows if since < _t(r["ended_utc"]) <= until]


def _t(text):
    return datetime.fromisoformat(text[:26]).replace(tzinfo=timezone.utc)


PROJECT = 1


def stored(conn):
    return conn.execute("SELECT laminar_trace_id::text, session_id, status FROM traces "
                        "ORDER BY ended_at").fetchall()


# ------------------------------------------------------------------ polling

def test_first_poll_writes_every_finished_trace(conn):
    laminar = FakeLaminar([root(t) for t in TRACES])
    result = poll_once(laminar, conn, PROJECT, now=NOW)

    assert result.written == 3
    assert result.since == NOW - timedelta(hours=24) - timedelta(seconds=60)
    assert [r[0] for r in stored(conn)] == [t["trace_id"] for t in TRACES]
    assert len(laminar.calls) == 1


def test_times_are_stored_in_utc_with_microseconds(conn):
    poll_once(FakeLaminar([root(TRACES[0])]), conn, PROJECT, now=NOW)
    started, ended = conn.execute("SELECT started_at, ended_at FROM traces").fetchone()
    assert started == datetime(2026, 10, 26, 18, 32, 46, 549806, tzinfo=timezone.utc)
    assert ended == datetime(2026, 10, 26, 18, 32, 49, 312142, tzinfo=timezone.utc)


def test_second_poll_writes_nothing(conn):
    laminar = FakeLaminar([root(t) for t in TRACES])
    poll_once(laminar, conn, PROJECT, now=NOW)
    result = poll_once(laminar, conn, PROJECT, now=NOW)
    assert (result.written, result.already_stored) == (0, 3)
    assert len(stored(conn)) == 3


def test_since_is_marker_minus_overlap(conn):
    laminar = FakeLaminar([root(t) for t in TRACES])
    poll_once(laminar, conn, PROJECT, now=NOW)
    result = poll_once(laminar, conn, PROJECT, now=NOW)
    # The newest trace ended at 18:32:54.688809; marker - 60 s = 18:31:54.688809.
    assert result.since == datetime(2026, 10, 26, 18, 31, 54, 688809, tzinfo=timezone.utc)
    assert laminar.calls[-1][1] == {"since": "2026-10-26 18:31:54.688809",
                                    "until": "2026-10-26 18:59:45.000000"}


def test_trace_arriving_late_inside_the_overlap_is_still_written(conn):
    # It ended at 18:32:50, before the marker (18:32:54.7), but only shows up in
    # Laminar after the first poll.
    late = root({"trace_id": "cccccccc-0000-0000-0000-000000000001",
                 "start": "2026-10-26 18:32:48.000000000", "end": "2026-10-26 18:32:50.000000000"},
                session_id="convo-2")
    laminar = FakeLaminar([root(t) for t in TRACES])
    poll_once(laminar, conn, PROJECT, now=NOW)
    laminar.rows.append(late)
    assert poll_once(laminar, conn, PROJECT, now=NOW).written == 1
    assert len(stored(conn)) == 4


def test_trace_arriving_later_than_the_overlap_is_missed(conn):
    # The documented limit: arriving more than 60 s behind the marker.
    late = root({"trace_id": "cccccccc-0000-0000-0000-000000000002",
                 "start": "2026-10-26 18:31:00.000000000", "end": "2026-10-26 18:31:30.000000000"},
                session_id="convo-2")
    laminar = FakeLaminar([root(t) for t in TRACES])
    poll_once(laminar, conn, PROJECT, now=NOW)
    laminar.rows.append(late)
    assert poll_once(laminar, conn, PROJECT, now=NOW).written == 0


def test_trace_waits_for_the_settling_delay(conn):
    # Traces ended at 18:32:49.3, 52.1 and 54.7. At 18:33:08 (settled up to 18:32:53)
    # the first two have settled; at 18:33:10 all three have.
    laminar = FakeLaminar([root(t) for t in TRACES])
    early = poll_once(laminar, conn, PROJECT, now=datetime(2026, 10, 26, 18, 33, 8, tzinfo=timezone.utc))
    assert early.written == 2
    later = poll_once(laminar, conn, PROJECT, now=datetime(2026, 10, 26, 18, 33, 10, tzinfo=timezone.utc))
    assert later.written == 1


def test_errored_trace_is_stored_with_status_error(conn):
    poll_once(FakeLaminar([root(TRACES[0], status="error")]), conn, PROJECT, now=NOW)
    assert stored(conn)[0][2] == "error"


def test_trace_without_session_id_is_stored_with_null(conn):
    poll_once(FakeLaminar([root(TRACES[0], session_id="")]), conn, PROJECT, now=NOW)
    assert stored(conn)[0][1] is None


def test_successful_poll_sets_last_polled_at_even_with_nothing_new(conn):
    assert conn.execute("SELECT last_polled_at FROM projects").fetchone()[0] is None
    poll_once(FakeLaminar([]), conn, PROJECT, now=NOW)
    assert conn.execute("SELECT last_polled_at FROM projects").fetchone()[0] is not None


def test_failed_poll_leaves_marker_and_last_polled_at_alone(conn):
    class DownLaminar:
        def query(self, sql, parameters=None):
            raise RuntimeError("Laminar is down")

    with pytest.raises(RuntimeError):
        poll_once(DownLaminar(), conn, PROJECT, now=NOW)
    assert conn.execute("SELECT last_polled_at FROM projects").fetchone()[0] is None
    assert stored(conn) == []
