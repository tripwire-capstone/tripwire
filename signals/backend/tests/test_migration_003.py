"""Migration 003: several findings per run, and the new run and version columns."""

import psycopg
import pytest


def make_run(conn, status="done"):
    """One trace, one signal + version, one run. Returns (run_id, trace_id, version_id)."""
    trace_id = conn.execute(
        """INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
           SELECT id, gen_random_uuid(), now(), now(), 'success' FROM projects RETURNING id"""
    ).fetchone()[0]
    signal_id = conn.execute(
        "INSERT INTO signals (project_id, name) SELECT id, 'S-' || gen_random_uuid() FROM projects RETURNING id"
    ).fetchone()[0]
    version_id = conn.execute(
        """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
           VALUES (%s, 1, 'x', '[]', 'm') RETURNING id""", (signal_id,)
    ).fetchone()[0]
    run_id = conn.execute(
        """INSERT INTO signal_runs (trace_id, signal_version_id, status, finished_at, last_error)
           VALUES (%s, %s, %s, now(), 'err') RETURNING id""", (trace_id, version_id, status)
    ).fetchone()[0]
    return run_id, trace_id, version_id


def add_event(conn, run, index, description="d"):
    run_id, trace_id, version_id = run
    conn.execute(
        """INSERT INTO signal_events (run_id, trace_id, signal_version_id, finding_index,
                                      severity, description, judge_input)
           VALUES (%s, %s, %s, %s, 'warning', %s, 'input')""",
        (run_id, trace_id, version_id, index, description),
    )


def test_one_run_can_have_five_events(conn):
    run = make_run(conn)
    for i in range(5):
        add_event(conn, run, i)
    assert conn.execute("SELECT count(*) FROM signal_events").fetchone()[0] == 5


def test_same_finding_index_twice_is_rejected(conn):
    run = make_run(conn)
    add_event(conn, run, 0)
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_event(conn, run, 0)


def test_sixth_finding_is_rejected(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        add_event(conn, make_run(conn), 5)


def test_description_may_be_null_but_not_blank(conn):
    run = make_run(conn)
    add_event(conn, run, 0, description=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        add_event(conn, run, 1, description="  ")


def test_skipped_is_no_longer_a_status(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        make_run(conn, status="skipped")


def test_rendered_trace_only_on_failed_runs(conn):
    run_id, _, _ = make_run(conn, status="failed")
    conn.execute("UPDATE signal_runs SET rendered_trace = 'trace' WHERE id = %s", (run_id,))
    done_id, _, _ = make_run(conn, status="done")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE signal_runs SET rendered_trace = 'trace' WHERE id = %s", (done_id,))


def test_dropped_findings_defaults_to_zero_and_cannot_go_negative(conn):
    run_id, _, _ = make_run(conn)
    assert conn.execute("SELECT dropped_findings FROM signal_runs WHERE id = %s",
                        (run_id,)).fetchone()[0] == 0
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE signal_runs SET dropped_findings = -1 WHERE id = %s", (run_id,))


def test_blank_critical_guidance_is_rejected(conn):
    _, _, version_id = make_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE signal_versions SET critical_guidance = '' WHERE id = %s",
                     (version_id,))