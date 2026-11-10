"""Tests for the seed and the placeholder runner and clusterer (real Postgres)."""

import uuid

from tripwire.clusterer.cluster import cluster_step
from tripwire.placeholder import picked
from tripwire.runner.run import run_step
from tripwire.seed import seed


def seeded(conn):
    seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh", key_hint="abcd")
    return conn.execute("SELECT id FROM projects WHERE name = 'default'").fetchone()[0]


def add_traces(conn, project_id, n):
    ids = []
    for i in range(n):
        tid = str(uuid.UUID(int=i + 1))
        conn.execute("""INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
                        VALUES (%s, %s, now() - interval '1 minute', now(), 'success')""",
                     (project_id, tid))
        ids.append(tid)
    return ids


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ seed

def test_seed_creates_project_signal_version_and_cluster_once(conn):
    assert seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh", key_hint="abcd")
    assert not seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh", key_hint="abcd")
    assert [count(conn, t) for t in ("signals", "signal_versions", "clusters")] == [1, 1, 1]
    key, hint = conn.execute("SELECT laminar_api_key_encrypted, laminar_api_key_hint FROM projects "
                             "WHERE name = 'default'").fetchone()
    assert bytes(key) == b"not-encrypted-yet" and hint == "abcd"   # never the real key


# ------------------------------------------------------------------ runner

def test_runner_writes_one_run_per_trace_and_events_for_the_picked_ones(conn):
    traces = add_traces(conn, seeded(conn), 20)
    expected_events = sum(picked(f"event:{t}", 0.5) for t in traces)

    assert run_step(conn, 0.5) == (20, expected_events)
    assert 0 < expected_events < 20      # the rule really splits them
    assert conn.execute("SELECT count(*) FROM signal_runs WHERE status = 'done'").fetchone()[0] == 20


def test_runner_second_step_does_nothing(conn):
    add_traces(conn, seeded(conn), 5)
    run_step(conn, 0.5)
    assert run_step(conn, 0.5) == (0, 0)


def test_event_rate_zero_and_one(conn):
    add_traces(conn, seeded(conn), 5)
    assert run_step(conn, 0.0) == (5, 0)
    conn.execute("DELETE FROM signal_runs")
    assert run_step(conn, 1.0) == (5, 5)


def test_new_version_gets_its_own_runs_and_the_old_one_gets_none(conn):
    add_traces(conn, seeded(conn), 3)
    run_step(conn, 0.5)
    conn.execute("""INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
                    SELECT signal_id, 2, 'v2', '{}', 'placeholder' FROM signal_versions""")
    assert run_step(conn, 0.5)[0] == 3
    assert conn.execute("""SELECT v.version_number, count(*) FROM signal_runs r
                           JOIN signal_versions v ON v.id = r.signal_version_id
                           GROUP BY 1 ORDER BY 1""").fetchall() == [(1, 3), (2, 3)]


def test_inactive_signal_gets_no_runs(conn):
    add_traces(conn, seeded(conn), 3)
    conn.execute("UPDATE signals SET active = false")
    assert run_step(conn, 0.5) == (0, 0)


# ------------------------------------------------------------------ clusterer

def test_clusterer_embeds_every_event_and_links_the_picked_ones(conn):
    add_traces(conn, seeded(conn), 30)
    run_step(conn, 1.0)                                    # 30 events
    events = [r[0] for r in conn.execute("SELECT id FROM signal_events").fetchall()]
    expected = sum(picked(f"cluster:{e}", 0.5) for e in events)

    assert cluster_step(conn, 0.5) == (30, expected)
    assert 0 < expected < 30
    assert count(conn, "event_embeddings") == 30
    cluster = conn.execute("SELECT id FROM clusters").fetchone()[0]
    assert conn.execute("SELECT count(*) FROM cluster_members WHERE cluster_id = %s",
                        (cluster,)).fetchone()[0] == expected
    pool = conn.execute("""SELECT count(*) FROM event_embeddings m
                           WHERE NOT EXISTS (SELECT 1 FROM cluster_members c
                                             WHERE c.event_id = m.event_id)""").fetchone()[0]
    assert pool == 30 - expected


def test_clusterer_second_step_does_nothing(conn):
    add_traces(conn, seeded(conn), 5)
    run_step(conn, 1.0)
    cluster_step(conn, 0.5)
    assert cluster_step(conn, 0.5) == (0, 0)


def test_signal_without_a_cluster_leaves_every_event_in_the_pool(conn):
    add_traces(conn, seeded(conn), 5)
    run_step(conn, 1.0)
    conn.execute("DELETE FROM clusters")
    assert cluster_step(conn, 1.0) == (5, 0)
