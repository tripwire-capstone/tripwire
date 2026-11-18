"""Tests for the seed and the placeholder runner and clusterer (real Postgres)."""

import uuid

from tripwire.clusterer.cluster import cluster_step
from tripwire.placeholder import picked
from tripwire.runner.run import run_step
from tripwire.seed import seed


def seeded(conn):
    """The seeded project plus one active signal (the seed itself makes no signals)."""
    seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh")
    project_id = conn.execute("SELECT id FROM projects WHERE name = 'default'").fetchone()[0]
    add_signal(conn, project_id)
    return project_id


def add_signal(conn, project_id, name="Test signal"):
    """A signal with version 1, the way the API would store one."""
    signal_id = conn.execute("INSERT INTO signals (project_id, name) VALUES (%s, %s) RETURNING id",
                             (project_id, name)).fetchone()[0]
    conn.execute("""INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
                    VALUES (%s, 1, 'Flag frustrated users.', '[]', 'gpt-6-luna')""", (signal_id,))
    return signal_id


def add_traces(conn, project_id, n):
    """n traces, each with a pending run for every active signal's current version (as the poller does)."""
    ids = []
    for i in range(n):
        tid = str(uuid.UUID(int=i + 1))
        conn.execute("""INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
                        VALUES (%s, %s, now() - interval '1 minute', now(), 'success')""",
                     (project_id, tid))
        ids.append(tid)
    conn.execute("""INSERT INTO signal_runs (trace_id, signal_version_id)
                    SELECT t.id, v.id FROM traces t
                    JOIN signals s ON s.project_id = t.project_id AND s.active
                    JOIN LATERAL (SELECT id FROM signal_versions WHERE signal_id = s.id
                                  ORDER BY version_number DESC LIMIT 1) v ON true
                    ON CONFLICT DO NOTHING""")
    return ids


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ seed

def test_seed_creates_only_the_project_once(conn, monkeypatch):
    # No signal means no model to pick, so the seed works without JUDGE_DEFAULT_MODEL
    monkeypatch.delenv("JUDGE_DEFAULT_MODEL")
    assert seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh")
    assert not seed(conn, api_url="https://api.lmnr.ai", ui_url="https://laminar.sh")
    assert [count(conn, t) for t in ("signals", "signal_versions", "clusters")] == [0, 0, 0]
    keys = conn.execute("SELECT laminar_api_key_encrypted, laminar_api_key_hint, openai_api_key_encrypted, "
                        "openai_api_key_hint FROM projects WHERE name = 'default'").fetchone()
    assert keys == (None, None, None, None)   # no saved keys: the workers use their environment


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
    conn.execute("UPDATE signal_runs SET status = 'pending', finished_at = NULL")
    assert run_step(conn, 1.0) == (5, 5)

def test_runner_never_creates_runs(conn):
    add_traces(conn, seeded(conn), 3)
    run_step(conn, 0.5)
    conn.execute("""INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
                    SELECT signal_id, 2, 'v2', '[]', 'placeholder' FROM signal_versions""")
    assert run_step(conn, 0.5) == (0, 0)
    assert count(conn, "signal_runs") == 3


def test_runs_of_an_inactive_signal_stay_pending(conn):
    add_traces(conn, seeded(conn), 3)
    conn.execute("UPDATE signals SET active = false")
    assert run_step(conn, 0.5) == (0, 0)
    assert conn.execute("SELECT count(*) FROM signal_runs WHERE status = 'pending'").fetchone()[0] == 3

# ------------------------------------------------------------------ clusterer

def add_cluster(conn):
    """The seed makes no cluster; placeholder-mode tests add one for the signal."""
    from tripwire.placeholder import FIXED_EMBEDDING, vector_text
    conn.execute("""INSERT INTO clusters (signal_id, name, centroid)
                    SELECT id, 'Test cluster', %s FROM signals""", (vector_text(FIXED_EMBEDDING),))


def test_clusterer_embeds_every_event_and_links_the_picked_ones(conn):
    add_traces(conn, seeded(conn), 30)
    add_cluster(conn)
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
    add_cluster(conn)
    run_step(conn, 1.0)
    cluster_step(conn, 0.5)
    assert cluster_step(conn, 0.5) == (0, 0)


def test_signal_without_a_cluster_leaves_every_event_in_the_pool(conn):
    add_traces(conn, seeded(conn), 5)
    run_step(conn, 1.0)                                    # the seed makes no cluster
    assert cluster_step(conn, 1.0) == (5, 0)
