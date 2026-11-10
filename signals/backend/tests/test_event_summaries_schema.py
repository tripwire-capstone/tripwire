"""Rules the database enforces on event_summaries (migration 002), one test per rule."""

import uuid

import psycopg
import pytest

from tripwire.runner.run import run_step
from tripwire.seed import seed


@pytest.fixture
def event_id(conn):
    """One real signal_events row, made by the seed and the placeholder runner."""
    seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh", key_hint="abcd")
    project = conn.execute("SELECT id FROM projects WHERE name = 'default'").fetchone()[0]
    conn.execute("""INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
                    VALUES (%s, %s, now() - interval '1 minute', now(), 'success')""",
                 (project, str(uuid.uuid4())))
    run_step(conn, 1.0)
    return conn.execute("SELECT id FROM signal_events").fetchone()[0]


DONE = dict(status="done", cause="booking tool", summary="Booking fails for large parties.",
            attempts=1, finished_at="now")
FAILED = dict(status="failed", attempts=3, error_kind="bad_output", last_error="no summary field",
              finished_at="now")


def insert(conn, event_id, **cols):
    cols = {"model": "small-model", "prompt_version": 1, **cols}
    if cols.get("finished_at") == "now":
        cols["finished_at"] = conn.execute("SELECT now()").fetchone()[0]
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    conn.execute(f"INSERT INTO event_summaries (event_id, {names}) VALUES (%s, {marks})",
                 (event_id, *cols.values()))


def rejected(conn, event_id, **cols):
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(conn, event_id, **cols)


# ------------------------------------------------------------------ allowed shapes

def test_done_failed_and_pending_rows_are_accepted(conn, event_id):
    insert(conn, event_id, **DONE)
    conn.execute("DELETE FROM event_summaries")
    insert(conn, event_id, **FAILED)
    conn.execute("DELETE FROM event_summaries")
    insert(conn, event_id, status="pending", attempts=1, error_kind="bad_output",
           last_error="broken JSON", not_before=conn.execute("SELECT now()").fetchone()[0])


def test_done_may_have_no_cause_and_keep_an_earlier_error(conn, event_id):
    insert(conn, event_id, **{**DONE, "cause": None, "attempts": 2,
                              "error_kind": "bad_output", "last_error": "broken JSON"})


def test_pending_after_an_outage_keeps_zero_attempts_and_waits(conn, event_id):
    later = conn.execute("SELECT now() + interval '30 seconds'").fetchone()[0]
    insert(conn, event_id, status="pending", attempts=0, error_kind="service", last_error="timeout",
           not_before=later)


def test_one_row_per_event(conn, event_id):
    insert(conn, event_id, **DONE)
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(conn, event_id, **DONE)


# ------------------------------------------------------------------ rejected shapes

def test_unknown_status_is_rejected(conn, event_id):
    rejected(conn, event_id, **{**DONE, "status": "summarized"})


@pytest.mark.parametrize("summary", [None, "", "   "])
def test_done_needs_a_non_blank_summary(conn, event_id, summary):
    rejected(conn, event_id, **{**DONE, "summary": summary})


def test_done_needs_a_finish_time_and_a_counted_call(conn, event_id):
    rejected(conn, event_id, **{**DONE, "finished_at": None})
    rejected(conn, event_id, **{**DONE, "attempts": 0})


def test_failed_needs_an_error(conn, event_id):
    rejected(conn, event_id, **{**FAILED, "last_error": None})
    rejected(conn, event_id, **{**FAILED, "error_kind": None})


def test_an_outage_can_never_fail_an_event(conn, event_id):
    rejected(conn, event_id, **{**FAILED, "error_kind": "service"})


@pytest.mark.parametrize("kind", ["too_long", "refused"])
def test_too_long_and_refused_fail_after_one_call(conn, event_id, kind):
    insert(conn, event_id, **{**FAILED, "attempts": 1, "error_kind": kind})


def test_unknown_error_kind_is_rejected(conn, event_id):
    rejected(conn, event_id, **{**FAILED, "error_kind": "timeout"})


def test_failed_has_no_summary(conn, event_id):
    rejected(conn, event_id, **{**FAILED, "summary": "Something."})


def test_pending_is_not_finished(conn, event_id):
    rejected(conn, event_id, status="pending", finished_at="now")
    rejected(conn, event_id, status="pending", summary="Something.")


def test_only_pending_rows_are_scheduled(conn, event_id):
    later = conn.execute("SELECT now() + interval '1 minute'").fetchone()[0]
    rejected(conn, event_id, **{**DONE, "not_before": later})
    rejected(conn, event_id, **{**FAILED, "not_before": later})


def test_error_kind_needs_an_error_message(conn, event_id):
    rejected(conn, event_id, status="pending", error_kind="service")


def test_negative_attempts_and_version_zero_are_rejected(conn, event_id):
    rejected(conn, event_id, status="pending", attempts=-1)
    rejected(conn, event_id, status="pending", prompt_version=0)


# ------------------------------------------------------------------ deletes

def test_deleting_the_trace_removes_the_summary(conn, event_id):
    insert(conn, event_id, **DONE)
    conn.execute("DELETE FROM traces")
    assert conn.execute("SELECT count(*) FROM event_summaries").fetchone()[0] == 0
