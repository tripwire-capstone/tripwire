"""Tests for the threshold sweep (tripwire/clusterer/sweep.py): replaying the real clusterer in a
throwaway schema, on real Postgres + pgvector."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tests.clustering_data import add_cluster, add_event, add_member, add_signal
from tests.vectors import base_vector, vectors_all_apart, vectors_with_distances
from tripwire.clusterer import loop
from tripwire.clusterer.pool import PoolEvent, qualifying_groups
from tripwire.clusterer.sweep import (
    SweepError,
    default_thresholds,
    drop_leftover_schemas,
    passes_from_timestamps,
    replay,
    sweep,
)

START = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def at(seconds):
    return START + timedelta(seconds=seconds)


def events_in_conversations(conn, version_id, vectors):
    """One event per vector, each from its own conversation. Returns the event IDs."""
    return [add_event(conn, version_id, vector, session_id=f"conversation-{index}")
            for index, vector in enumerate(vectors)]


def sweep_schemas(conn):
    return [row[0] for row in conn.execute(r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 'calib\_%'")]


# ------------------------------------------------------------------ settings and timing

def test_default_thresholds_run_from_0_20_to_0_40_by_0_02():
    thresholds = default_thresholds()
    assert thresholds[0] == 0.20 and thresholds[-1] == 0.40 and len(thresholds) == 11
    assert 0.30 in thresholds


def test_passes_from_timestamps_groups_bursts_into_passes():
    embeddings = [(1, at(0)), (2, at(0.2)), (3, at(10)), (4, at(10.1)), (5, at(21))]
    assert passes_from_timestamps(embeddings, 10) == [(0, [1, 2]), (0, [3, 4]), (0, [5])]


def test_passes_from_timestamps_counts_quiet_passes_in_a_long_gap():
    # 40 s later with a 10 s loop: the live loop ran about 3 passes with nothing new in between.
    assert passes_from_timestamps([(1, at(0)), (2, at(40))], 10) == [(0, [1]), (3, [2])]


def test_passes_from_timestamps_with_no_embeddings_is_empty():
    assert passes_from_timestamps([], 10) == []


# ------------------------------------------------------------------ the replay

def test_replay_forms_a_cluster_only_when_the_threshold_allows_it(conn):
    _, version_id = add_signal(conn)
    event_ids = events_in_conversations(conn, version_id, vectors_all_apart(3, 0.25))

    strict = replay(conn, 0.20, environ={})
    loose = replay(conn, 0.30, environ={})
    assert all(strict[event_id] is None for event_id in event_ids)               # all stay in the pool
    assert len({loose[event_id] for event_id in event_ids}) == 1 and loose[event_ids[0]] is not None


def test_replay_lets_a_pool_event_join_once_the_new_cluster_has_a_center(conn):
    # A, B, C are 0.28 apart; D is 0.35 from each. Grouping the pool once (average linkage) leaves
    # D out, but D is within 0.30 of the new cluster's center, so the live loop's next pass joins it.
    # Only a replay of the real passes sees that.
    distances = [[0.0, 0.28, 0.28, 0.35],
                 [0.28, 0.0, 0.28, 0.35],
                 [0.28, 0.28, 0.0, 0.35],
                 [0.35, 0.35, 0.35, 0.0]]
    vectors = vectors_with_distances(distances)
    _, version_id = add_signal(conn)
    first, second, third, late = events_in_conversations(conn, version_id, vectors)

    from_scratch = qualifying_groups(
        [PoolEvent(index, 1, ("session", index), np.array(vector)) for index, vector in enumerate(vectors)],
        0.30, 3, 2)
    assert [sorted(event.event_id for event in group) for group in from_scratch] == [[0, 1, 2]]

    clusters = replay(conn, 0.30, environ={})
    assert clusters[late] is not None and clusters[late] == clusters[first] == clusters[second] == clusters[third]


def test_replay_uses_the_clusterers_settings(conn):
    _, version_id = add_signal(conn)
    event_ids = events_in_conversations(conn, version_id, vectors_all_apart(3, 0.10))
    clusters = replay(conn, 0.30, environ={"CLUSTER_MIN_EVENTS": "4"})
    assert all(clusters[event_id] is None for event_id in event_ids)             # 3 events aren't enough


def test_replay_leaves_the_real_tables_and_search_path_alone(conn):
    signal_id, version_id = add_signal(conn)
    existing = add_cluster(conn, signal_id, base_vector())
    member = add_event(conn, version_id, base_vector(), session_id="conversation-x")
    add_member(conn, member, existing)
    events_in_conversations(conn, version_id, vectors_all_apart(3, 0.10))
    path_before = conn.execute("SHOW search_path").fetchone()[0]

    clusters = replay(conn, 0.30, environ={})
    assert len(clusters) == 4                                                     # every embedded event
    assert conn.execute("SHOW search_path").fetchone()[0] == path_before
    assert conn.execute("SELECT count(*) FROM clusters").fetchone()[0] == 1      # only the real one
    assert conn.execute("SELECT count(*) FROM cluster_members").fetchone()[0] == 1
    assert sweep_schemas(conn) == []


def test_replay_drops_its_schema_and_raises_when_a_step_fails(conn, monkeypatch):
    _, version_id = add_signal(conn)
    events_in_conversations(conn, version_id, vectors_all_apart(3, 0.10))

    def broken_assign(pass_conn, threshold):
        raise RuntimeError("assign is broken")
    monkeypatch.setattr(loop, "assign_pool", broken_assign)

    with pytest.raises(SweepError, match="assign"):
        replay(conn, 0.30, environ={})
    assert sweep_schemas(conn) == []


def test_replay_refuses_to_copy_a_sweep_schema(conn):
    with pytest.raises(SweepError, match="sweep schema"):
        replay(conn, 0.30, source_schema="calib_12345678", environ={})


def test_drop_leftover_schemas_removes_only_sweep_schemas(conn):
    conn.execute("CREATE SCHEMA calib_deadbeef")
    current = conn.execute("SELECT current_schema()").fetchone()[0]
    assert drop_leftover_schemas(conn) == 1
    assert sweep_schemas(conn) == []
    assert conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (current,)).fetchone()[0] == 1


def test_sweep_replays_each_threshold_smallest_first(conn):
    _, version_id = add_signal(conn)
    event_ids = events_in_conversations(conn, version_id, vectors_all_apart(3, 0.25))
    conn.execute("CREATE SCHEMA calib_cafef00d")                                  # left by a crash

    results = sweep(conn, [0.30, 0.20], environ={})
    assert [threshold for threshold, _ in results] == [0.20, 0.30]
    assert results[0][1][event_ids[0]] is None and results[1][1][event_ids[0]] is not None
    assert sweep_schemas(conn) == []
