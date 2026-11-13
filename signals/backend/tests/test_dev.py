"""GET /api/dev/status: the developer view's counts and errors.

The first test is the case that started it (Nov 12): a revoked OpenAI key leaves runs pending with
their attempts given back, and nothing on screen said so.
"""

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tripwire.api.app import app
from tripwire.api.db import get_conn
from tripwire.api.dev import LATEST_DECISIONS, LATEST_SUMMARIES, MAX_ERROR_GROUPS

KEY_REVOKED = "OpenAI service error: Error code: 401 - Incorrect API key provided"


@pytest.fixture
def client(conn):
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ building rows

def add_signal(conn, *, active=True):
    signal_id = conn.execute(
        "INSERT INTO signals (project_id, name, active) VALUES (1, %s, %s) RETURNING id",
        (f"signal {uuid.uuid4().hex[:6]}", active)).fetchone()[0]
    return conn.execute(
        """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
           VALUES (%s, 1, 'Flag things.', '[]', 'gpt-test') RETURNING id""", (signal_id,)).fetchone()[0]


def signal_name(conn, version_id):
    return conn.execute("""SELECT s.name FROM signals s JOIN signal_versions v ON v.signal_id = s.id
                           WHERE v.id = %s""", (version_id,)).fetchone()[0]


def add_trace(conn, *, synced_minutes_ago=60):
    return conn.execute(
        """INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status, synced_at)
           VALUES (1, %s, now() - interval '2 hours', now() - interval '2 hours', 'success',
                   now() - make_interval(mins => %s))
           RETURNING id""", (uuid.uuid4(), synced_minutes_ago)).fetchone()[0]


def add_run(conn, version_id, *, status="pending", minutes_ago=60, finished_minutes_ago=None,
            last_error=None, attempts=0, not_before_minutes=None, lease_minutes=None, analysis=None,
            dropped=0):
    """A run created `minutes_ago`. Finished runs finish `finished_minutes_ago` (default: when created).
    `lease_minutes` is from now: negative = an expired lease."""
    finished = status in ("done", "failed")
    if finished and finished_minutes_ago is None:
        finished_minutes_ago = minutes_ago
    if status == "running" and lease_minutes is None:
        lease_minutes = 5
    return conn.execute(
        """INSERT INTO signal_runs (trace_id, signal_version_id, status, attempts, last_error, analysis,
                                    dropped_findings, created_at, finished_at, not_before, lease_until)
           VALUES (%(trace)s, %(version)s, %(status)s, %(attempts)s, %(error)s, %(analysis)s, %(dropped)s,
                   now() - make_interval(mins => %(age)s),
                   now() - make_interval(mins => %(finished)s),
                   now() + make_interval(mins => %(not_before)s),
                   now() + make_interval(mins => %(lease)s))
           RETURNING id""",
        {"trace": add_trace(conn), "version": version_id, "status": status, "attempts": attempts,
         "error": last_error, "age": minutes_ago, "finished": finished_minutes_ago,
         "not_before": not_before_minutes, "lease": lease_minutes, "analysis": analysis,
         "dropped": dropped}).fetchone()[0]


def add_event(conn, version_id, *, minutes_ago=60):
    run_id = add_run(conn, version_id, status="done", minutes_ago=minutes_ago)
    trace_id = conn.execute("SELECT trace_id FROM signal_runs WHERE id = %s", (run_id,)).fetchone()[0]
    return conn.execute(
        """INSERT INTO signal_events (run_id, trace_id, signal_version_id, severity, payload, judge_input,
                                      created_at)
           VALUES (%s, %s, %s, 'warning', '{}', 'trace text', now() - make_interval(mins => %s))
           RETURNING id""", (run_id, trace_id, version_id, minutes_ago)).fetchone()[0]


def add_finding(conn, run_id, *, index=0, severity="warning"):
    """One more finding on an existing run (add_event makes its own run)."""
    trace_id, version_id = conn.execute("SELECT trace_id, signal_version_id FROM signal_runs WHERE id = %s",
                                        (run_id,)).fetchone()
    conn.execute("""INSERT INTO signal_events (run_id, trace_id, signal_version_id, finding_index, severity,
                                                payload, judge_input)
                    VALUES (%s, %s, %s, %s, %s, '{}', 'trace text')""",
                 (run_id, trace_id, version_id, index, severity))


def add_summary(conn, event_id, *, status="done", error_kind=None, last_error=None, finished_minutes_ago=30,
                text="A summary.", cause=None, prompt_version=1):
    finished = status in ("done", "failed")
    conn.execute(
        """INSERT INTO event_summaries (event_id, status, summary, cause, model, prompt_version, attempts,
                                        error_kind, last_error, finished_at)
           VALUES (%s, %s, %s, %s, 'gpt-test', %s, %s, %s, %s, now() - make_interval(mins => %s))""",
        (event_id, status, text if status == "done" else None, cause, prompt_version, 1 if finished else 0,
         error_kind, last_error, finished_minutes_ago if finished else None))


def add_embedding(conn, event_id, *, minutes_ago=30):
    conn.execute(
        """INSERT INTO event_embeddings (event_id, model, embedding, created_at)
           VALUES (%s, 'text-embedding-3-small', array_fill(0.1, ARRAY[1536])::vector,
                   now() - make_interval(mins => %s))""", (event_id, minutes_ago))


def add_member(conn, version_id, event_id, *, minutes_ago=30, cluster_name=None):
    """Puts the event in a new cluster (named `cluster_name`, or unnamed). Returns the cluster's id."""
    signal_id = conn.execute("SELECT signal_id FROM signal_versions WHERE id = %s", (version_id,)).fetchone()[0]
    cluster_id = conn.execute("INSERT INTO clusters (signal_id, name) VALUES (%s, %s) RETURNING id",
                              (signal_id, cluster_name)).fetchone()[0]
    conn.execute(
        """INSERT INTO cluster_members (event_id, cluster_id, assigned_at)
           VALUES (%s, %s, now() - make_interval(mins => %s))""", (event_id, cluster_id, minutes_ago))
    return cluster_id


# ------------------------------------------------------------------ reading the answer

def status(client):
    response = client.get("/api/dev/status")
    assert response.status_code == 200
    return response.json()


def stage(body, name):
    found = next(s for s in body["pipeline"]["stages"] if s["stage"] == name)
    return {**found, "counts": {c["state"]: c["count"] for c in found["counts"]}}


# ------------------------------------------------------------------ runs


def test_revoked_key_shows_as_retrying_runs_with_one_grouped_error(client, conn):
    version = add_signal(conn)
    for minutes_ago in (90, 60, 30):
        add_run(conn, version, minutes_ago=minutes_ago, last_error=KEY_REVOKED, not_before_minutes=2)

    body = status(client)
    runs = stage(body, "runs")
    assert runs["counts"]["retrying"] == 3
    assert runs["counts"]["pending"] == 3   # retrying is a part of pending
    assert runs["done_recently"] == 0
    assert runs["oldest_waiting_at"] is not None
    assert body["errors"]["groups"] == [{"stage": "runs", "message": KEY_REVOKED, "pending_or_running": 3, "failed": 0,
                               "done": 0, "signals": [signal_name(conn, version)],
                                         "last_seen_at": None,
                                         "next_retry_at": body["errors"]["groups"][0]["next_retry_at"]}]
    assert body["errors"]["groups"][0]["next_retry_at"] is not None
    assert runs["stuck"] is True


def test_oldest_waiting_is_the_oldest_unfinished_run(client, conn):
    version = add_signal(conn)
    oldest = add_run(conn, version, minutes_ago=120)
    add_run(conn, version, minutes_ago=5)
    add_run(conn, version, status="done", minutes_ago=500)   # finished: not waiting

    expected = conn.execute("SELECT created_at FROM signal_runs WHERE id = %s", (oldest,)).fetchone()[0]
    assert datetime.fromisoformat(stage(status(client), "runs")["oldest_waiting_at"]) == expected


def test_runs_of_an_inactive_signal_are_their_own_state_and_never_look_stalled(client, conn):
    inactive = add_signal(conn, active=False)
    add_run(conn, inactive, minutes_ago=600)

    runs = stage(status(client), "runs")
    assert runs["counts"]["inactive_signal"] == 1
    assert runs["counts"]["pending"] == 0
    assert runs["oldest_waiting_at"] is None


def test_running_and_expired_leases_are_told_apart(client, conn):
    version = add_signal(conn)
    add_run(conn, version, status="running", attempts=1, lease_minutes=5)
    add_run(conn, version, status="running", attempts=1, lease_minutes=-5)

    counts = stage(status(client), "runs")["counts"]
    assert (counts["running"], counts["lease_expired"]) == (1, 1)


def test_done_recently_counts_only_the_last_10_minutes_and_only_successes(client, conn):
    version = add_signal(conn)
    add_run(conn, version, status="done", minutes_ago=20, finished_minutes_ago=5)
    add_run(conn, version, status="done", minutes_ago=30, finished_minutes_ago=15)
    add_run(conn, version, status="failed", minutes_ago=10, finished_minutes_ago=2, last_error="bad output")

    runs = stage(status(client), "runs")
    assert runs["done_recently"] == 1
    assert (runs["counts"]["done"], runs["counts"]["failed"]) == (2, 1)


def test_a_quiet_system_that_just_got_one_item_is_not_stuck(client, conn):
    version = add_signal(conn)
    add_run(conn, version, minutes_ago=0)   # just arrived, nothing else in 10 min

    runs = stage(status(client), "runs")
    assert runs["oldest_waiting_at"] is not None and runs["done_recently"] == 0
    assert runs["stuck"] is False


def test_stuck_needs_a_long_wait_and_nothing_getting_through(client, conn):
    version = add_signal(conn)
    add_run(conn, version, minutes_ago=5)
    assert stage(status(client), "runs")["stuck"] is True

    add_run(conn, version, status="done", minutes_ago=6, finished_minutes_ago=1)   # a backlog that drains
    assert stage(status(client), "runs")["stuck"] is False


def test_nothing_waiting_is_never_stuck(client, conn):
    assert not any(s["stuck"] for s in status(client)["pipeline"]["stages"])


# ------------------------------------------------------------------ errors


def test_errors_split_into_pending_or_running_failed_and_done(client, conn):
    version = add_signal(conn)
    add_run(conn, version, last_error="timeout", not_before_minutes=1)
    add_run(conn, version, status="failed", last_error="timeout", finished_minutes_ago=60)
    add_run(conn, version, status="done", last_error="timeout", finished_minutes_ago=60)

    group = status(client)["errors"]["groups"][0]
    assert {k: group[k] for k in ("stage", "message", "pending_or_running", "failed", "done", "signals")} == {
        "stage": "runs", "message": "timeout", "pending_or_running": 1, "failed": 1, "done": 1,
        "signals": [signal_name(conn, version)]}


def test_a_stuck_run_is_listed_however_old_but_finished_errors_age_out_after_24_hours(client, conn):
    version = add_signal(conn)
    add_run(conn, version, minutes_ago=3 * 24 * 60, last_error="stuck for days")
    add_run(conn, version, status="failed", minutes_ago=3 * 24 * 60, last_error="old failure")

    messages = [e["message"] for e in status(client)["errors"]["groups"]]
    assert messages == ["stuck for days"]


def test_summary_errors_are_listed_separately_from_run_errors(client, conn):
    version = add_signal(conn)
    add_run(conn, version, last_error=KEY_REVOKED)
    add_summary(conn, add_event(conn, version), status="pending", error_kind="service", last_error=KEY_REVOKED)

    errors = status(client)["errors"]["groups"]
    assert sorted(e["stage"] for e in errors) == ["runs", "summaries"]
    assert all(e["message"] == KEY_REVOKED and e["pending_or_running"] == 1 for e in errors)


def test_an_error_lists_each_signal_it_hit_once(client, conn):
    first, second = add_signal(conn), add_signal(conn)
    add_run(conn, first, last_error=KEY_REVOKED)
    add_run(conn, first, last_error=KEY_REVOKED)
    add_run(conn, second, last_error=KEY_REVOKED)
    add_run(conn, second, status="failed", last_error="model gpt-nope not found")

    groups = {g["message"]: g["signals"] for g in status(client)["errors"]["groups"]}
    assert groups[KEY_REVOKED] == sorted([signal_name(conn, first), signal_name(conn, second)])
    assert groups["model gpt-nope not found"] == [signal_name(conn, second)]


def test_summary_errors_name_their_signal(client, conn):
    version = add_signal(conn)
    add_summary(conn, add_event(conn, version), status="failed", error_kind="crashed", last_error="KeyError")

    assert status(client)["errors"]["groups"][0]["signals"] == [signal_name(conn, version)]


def test_finished_errors_are_listed_newest_first_below_the_ones_still_happening(client, conn):
    version = add_signal(conn)
    add_run(conn, version, status="failed", last_error="an hour ago", finished_minutes_ago=60)
    add_run(conn, version, status="failed", last_error="two minutes ago", finished_minutes_ago=2)
    add_run(conn, version, status="done", last_error="twenty minutes ago", finished_minutes_ago=20)
    add_run(conn, version, last_error="still retrying")

    assert [g["message"] for g in status(client)["errors"]["groups"]] == [
        "still retrying", "two minutes ago", "twenty minutes ago", "an hour ago"]


def test_errors_most_pending_or_running_first(client, conn):
    version = add_signal(conn)
    add_run(conn, version, status="failed", last_error="many failures")
    add_run(conn, version, status="failed", last_error="many failures")
    add_run(conn, version, last_error="still happening")

    assert [e["message"] for e in status(client)["errors"]["groups"]] == ["still happening", "many failures"]


def test_error_groups_are_capped(client, conn):
    version = add_signal(conn)
    for i in range(MAX_ERROR_GROUPS + 1):
        add_run(conn, version, last_error=f"error {i}")

    body = status(client)
    assert len(body["errors"]["groups"]) == MAX_ERROR_GROUPS
    assert body["errors"]["more"] is True


# ------------------------------------------------------------------ summaries, embeddings, clusters


def test_summary_states_and_failure_kinds(client, conn):
    version = add_signal(conn)
    add_event(conn, version)                                              # not tried
    add_summary(conn, add_event(conn, version), status="pending")         # pending
    add_summary(conn, add_event(conn, version), status="pending", error_kind="service",
                last_error="rate limited")                                # pending, retrying
    add_summary(conn, add_event(conn, version), finished_minutes_ago=3)   # done, recently
    add_summary(conn, add_event(conn, version), status="failed", error_kind="crashed",
                last_error="KeyError")

    summaries = stage(status(client), "summaries")
    assert summaries["counts"] == {"not_tried": 1, "pending": 2, "retrying": 1, "done": 1, "failed": 1,
                                   "failed: crashed": 1}
    assert summaries["done_recently"] == 1
    assert summaries["oldest_waiting_at"] is not None


def test_an_event_the_placeholder_clusterer_embedded_is_not_waiting_for_a_summary(client, conn):
    version = add_signal(conn)
    add_embedding(conn, add_event(conn, version))   # embedded, never summarized

    summaries = stage(status(client), "summaries")
    assert summaries["counts"]["not_tried"] == 0
    assert summaries["oldest_waiting_at"] is None


def test_embeddings_wait_for_done_summaries_only(client, conn):
    version = add_signal(conn)
    add_summary(conn, add_event(conn, version))                   # summarized, not embedded
    embedded = add_event(conn, version)
    add_summary(conn, embedded)
    add_embedding(conn, embedded, minutes_ago=2)

    embeddings = stage(status(client), "embeddings")
    assert embeddings["counts"] == {"waiting": 1, "embedded": 1}
    assert embeddings["done_recently"] == 1


def test_pool_and_clustered_with_no_oldest_waiting(client, conn):
    version = add_signal(conn)
    in_pool = add_event(conn, version)
    add_embedding(conn, in_pool, minutes_ago=600)
    clustered = add_event(conn, version)
    add_embedding(conn, clustered)
    add_member(conn, version, clustered, minutes_ago=1)

    clusters = stage(status(client), "clusters")
    assert clusters["counts"] == {"in_pool": 1, "clustered": 1}
    assert clusters["done_recently"] == 1
    assert clusters["oldest_waiting_at"] is None   # the pool isn't a queue


# ------------------------------------------------------------------ traces and polling


def test_empty_database(client, conn):
    body = status(client)
    assert body["pipeline"]["last_polled_at"] is None
    assert body["pipeline"]["newest_trace_ended_at"] is None
    assert body["errors"] == {"groups": [], "more": False}
    assert body["latest_summaries"] == []
    assert body["latest_decisions"] == []
    stages = body["pipeline"]["stages"]
    assert all(c["count"] == 0 for s in stages for c in s["counts"])
    assert [s["stage"] for s in stages] == ["traces", "runs", "events", "summaries", "embeddings", "clusters"]


def test_traces_stored_recently_and_last_poll(client, conn):
    add_trace(conn, synced_minutes_ago=3)
    add_trace(conn, synced_minutes_ago=30)
    conn.execute("UPDATE projects SET last_polled_at = now() - interval '1 minute'")

    body = status(client)
    traces = stage(body, "traces")
    assert traces["counts"] == {"stored": 2}
    assert traces["done_recently"] == 1
    assert body["pipeline"]["last_polled_at"] is not None and body["pipeline"]["newest_trace_ended_at"] is not None


def test_a_project_never_polled_makes_last_poll_empty(client, conn):
    conn.execute("UPDATE projects SET last_polled_at = now()")
    conn.execute("""INSERT INTO projects (name, laminar_api_url, laminar_ui_url, laminar_api_key_encrypted,
                                          laminar_api_key_hint)
                    VALUES ('second', 'http://x', 'http://x', 'k', 'k')""")

    assert status(client)["pipeline"]["last_polled_at"] is None


# ------------------------------------------------------------------ latest summaries


def test_latest_summaries_newest_first_with_where_each_event_landed(client, conn):
    version = add_signal(conn)
    clustered = add_event(conn, version)
    add_summary(conn, clustered, text="Sent the visitor to the wrong park.", cause="Wrong tool argument",
                finished_minutes_ago=3)
    add_embedding(conn, clustered)
    cluster_id = add_member(conn, version, clustered, cluster_name="Wrong park")
    in_pool = add_event(conn, version)
    add_summary(conn, in_pool, text="Quoted a closed campsite.", finished_minutes_ago=2)
    add_embedding(conn, in_pool)
    not_embedded = add_event(conn, version)
    add_summary(conn, not_embedded, text="Ignored the date.", finished_minutes_ago=1)

    rows = status(client)["latest_summaries"]
    assert [(r["event_id"], r["landed"]) for r in rows] == [
        (not_embedded, "not_embedded"), (in_pool, "pool"), (clustered, "cluster")]
    assert rows[2]["cluster_id"] == cluster_id and rows[2]["cluster_name"] == "Wrong park"
    assert rows[2]["summary"] == "Sent the visitor to the wrong park."
    assert rows[2]["cause"] == "Wrong tool argument"
    assert rows[0]["cluster_id"] is None and rows[0]["cluster_name"] is None


def test_an_unnamed_cluster_has_an_id_but_no_name(client, conn):
    version = add_signal(conn)
    event = add_event(conn, version)
    add_summary(conn, event)
    add_embedding(conn, event)
    add_member(conn, version, event)

    row = status(client)["latest_summaries"][0]
    assert row["landed"] == "cluster" and row["cluster_id"] is not None and row["cluster_name"] is None


def test_latest_summaries_skip_unfinished_and_failed_and_stop_at_the_limit(client, conn):
    version = add_signal(conn)
    add_summary(conn, add_event(conn, version), status="pending")
    add_summary(conn, add_event(conn, version), status="failed", error_kind="refused", last_error="refused")
    for minutes_ago in range(LATEST_SUMMARIES + 2):
        add_summary(conn, add_event(conn, version), finished_minutes_ago=minutes_ago)

    rows = status(client)["latest_summaries"]
    assert len(rows) == LATEST_SUMMARIES
    finished = [datetime.fromisoformat(r["finished_at"]) for r in rows]
    assert finished == sorted(finished, reverse=True)


# ------------------------------------------------------------------ latest judge decisions


def test_decisions_show_findings_in_order_found_nothing_and_failures(client, conn):
    version = add_signal(conn)
    found = add_run(conn, version, status="done", finished_minutes_ago=3, analysis="Two problems.", dropped=1)
    add_finding(conn, found, index=1, severity="warning")
    add_finding(conn, found, index=0, severity="critical")
    nothing = add_run(conn, version, status="done", finished_minutes_ago=2,
                      analysis="The agent answered from the tool result; nothing to flag.")
    failed = add_run(conn, version, status="failed", finished_minutes_ago=1, last_error="unusable output")

    rows = status(client)["latest_decisions"]
    assert [r["run_id"] for r in rows] == [failed, nothing, found]
    assert rows[2]["findings"] == ["critical", "warning"]
    assert rows[2]["dropped_findings"] == 1 and rows[2]["analysis"] == "Two problems."
    assert rows[1]["findings"] == [] and rows[1]["analysis"].startswith("The agent answered")
    assert rows[0]["status"] == "failed" and rows[0]["error"] == "unusable output"
    assert rows[0]["signal_name"] == signal_name(conn, version)


def test_a_done_run_does_not_show_the_error_it_recovered_from(client, conn):
    version = add_signal(conn)
    add_run(conn, version, status="done", finished_minutes_ago=1, last_error="timeout", analysis="Fine.")

    assert status(client)["latest_decisions"][0]["error"] is None


def test_decisions_skip_unfinished_runs_and_stop_at_the_limit(client, conn):
    version = add_signal(conn)
    add_run(conn, version)                                    # pending
    add_run(conn, version, status="running", attempts=1)      # running
    for minutes_ago in range(LATEST_DECISIONS + 2):
        add_run(conn, version, status="done", finished_minutes_ago=minutes_ago)

    rows = status(client)["latest_decisions"]
    assert len(rows) == LATEST_DECISIONS
    assert all(r["status"] == "done" for r in rows)
    finished = [datetime.fromisoformat(r["finished_at"]) for r in rows]
    assert finished == sorted(finished, reverse=True)


def test_last_seen_is_the_latest_failure_or_recovery_and_retrying_runs_show_the_next_try(client, conn):
    version = add_signal(conn)
    add_run(conn, version, status="failed", last_error="timeout", finished_minutes_ago=30)
    recovered = add_run(conn, version, status="done", last_error="timeout", finished_minutes_ago=5)
    add_run(conn, version, last_error="timeout", not_before_minutes=3)
    add_run(conn, version, last_error="timeout", not_before_minutes=1)

    group = status(client)["errors"]["groups"][0]
    expected_seen = conn.execute("SELECT finished_at FROM signal_runs WHERE id = %s", (recovered,)).fetchone()[0]
    expected_retry = conn.execute("SELECT min(not_before) FROM signal_runs WHERE status = 'pending'").fetchone()[0]
    assert datetime.fromisoformat(group["last_seen_at"]) == expected_seen
    assert datetime.fromisoformat(group["next_retry_at"]) == expected_retry


def test_a_summary_error_was_last_seen_at_its_latest_write(client, conn):
    version = add_signal(conn)
    event = add_event(conn, version)
    add_summary(conn, event, status="pending", error_kind="service", last_error="rate limited")
    conn.execute("""UPDATE event_summaries SET updated_at = now() - interval '7 minutes',
                    not_before = now() + interval '1 minute' WHERE event_id = %s""", (event,))

    group = status(client)["errors"]["groups"][0]
    written = conn.execute("SELECT updated_at FROM event_summaries WHERE event_id = %s", (event,)).fetchone()[0]
    assert datetime.fromisoformat(group["last_seen_at"]) == written
    assert group["next_retry_at"] is not None
