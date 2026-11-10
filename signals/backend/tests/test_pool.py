"""Tests for step 3, forming new clusters from the pool (real Postgres + pgvector + SciPy).

Vectors come from tests/vectors.py at exact pairwise distances. Database tests stay clear of the
exact threshold (pgvector stores 32-bit floats); the exact-0.30 case is tested on group_vectors()
directly, in 64-bit floats.
"""

import logging

import pytest

from tests.clustering_data import (
    add_event,
    add_signal,
    add_version,
    cluster_of,
    clusters_of_signal,
    cosine_distance,
)
from tests.vectors import base_vector, vector_at, vectors_all_apart, vectors_with_distances
from tripwire.clusterer.assign import assign_pool
from tripwire.clusterer.pool import group_pool, group_vectors
from tripwire.placeholder import vector_text

THRESHOLD = 0.30
MIN_EVENTS = 3
MIN_CONVERSATIONS = 2
WINDOW_DAYS = 14
TOLERANCE = 1e-5


def run_pool(conn, min_events=MIN_EVENTS, min_conversations=MIN_CONVERSATIONS):
    return group_pool(conn, THRESHOLD, min_events, min_conversations, WINDOW_DAYS)


# ------------------------------------------------------------------ group_vectors (no database)

def test_group_vectors_merges_exactly_at_threshold():
    assert group_vectors(vectors_all_apart(3, 0.30), THRESHOLD) == [[0, 1, 2]]


def test_group_vectors_keeps_vectors_just_past_threshold_apart():
    assert group_vectors(vectors_all_apart(3, 0.31), THRESHOLD) == [[0], [1], [2]]


def test_group_vectors_handles_zero_and_one_vector():
    assert group_vectors([], THRESHOLD) == []
    assert group_vectors([base_vector()], THRESHOLD) == [[0]]


def test_group_vectors_uses_average_linkage_not_chaining():
    # A-B 0.20 merge first. C is 0.28 from B but 0.40 from A: its average distance to {A, B} is
    # 0.34, past the threshold, so it stays out. (Chaining would have pulled C in through B.)
    vectors = vectors_with_distances([
        [0.00, 0.20, 0.40],
        [0.20, 0.00, 0.28],
        [0.40, 0.28, 0.00],
    ])
    assert group_vectors(vectors, THRESHOLD) == [[0, 1], [2]]


def test_group_vectors_rejects_a_vector_with_no_direction():
    with pytest.raises(ValueError):
        group_vectors([base_vector(), [0.0] * len(base_vector()), vector_at(0.1, axis=1)], THRESHOLD)


# ------------------------------------------------------------------ group_pool: forming clusters

def test_group_pool_forms_cluster_from_three_close_events_in_two_conversations(conn):
    signal_id, version_id = add_signal(conn)
    vectors = vectors_all_apart(3, 0.29)
    events = [
        add_event(conn, version_id, vectors[0], session_id="conversation-1"),
        add_event(conn, version_id, vectors[1], session_id="conversation-1"),
        add_event(conn, version_id, vectors[2], session_id="conversation-2"),
    ]

    assert run_pool(conn) == 1
    assert list(clusters_of_signal(conn, signal_id).values()) == [sorted(events)]
    level, name = conn.execute("SELECT level, name FROM clusters WHERE signal_id = %s",
                               (signal_id,)).fetchone()
    assert (level, name) == (0, None)   # a leaf; named later (PR 3)


def test_group_pool_leaves_events_just_past_threshold_in_pool(conn):
    _, version_id = add_signal(conn)
    events = [
        add_event(conn, version_id, vector, session_id=f"conversation-{index}")
        for index, vector in enumerate(vectors_all_apart(3, 0.31))
    ]

    assert run_pool(conn) == 0
    assert [cluster_of(conn, event_id) for event_id in events] == [None, None, None]


def test_group_pool_needs_minimum_events(conn):
    signal_id, version_id = add_signal(conn)
    add_event(conn, version_id, base_vector(), session_id="conversation-1")
    add_event(conn, version_id, vector_at(0.05, axis=1), session_id="conversation-2")

    assert run_pool(conn) == 0          # 2 events: not enough

    add_event(conn, version_id, vector_at(0.05, axis=2), session_id="conversation-3")
    assert run_pool(conn) == 1          # the third arrives: the waiting two are grouped with it
    assert len(clusters_of_signal(conn, signal_id)) == 1


def test_group_pool_needs_minimum_conversations(conn):
    _, version_id = add_signal(conn)
    for axis in (1, 2, 3):
        add_event(conn, version_id, vector_at(0.05, axis), session_id="same-conversation")

    assert run_pool(conn) == 0          # one user repeating themselves isn't a pattern


def test_group_pool_counts_each_trace_without_session_as_its_own_conversation(conn):
    _, version_id = add_signal(conn)
    for axis in (1, 2, 3):
        add_event(conn, version_id, vector_at(0.05, axis), session_id=None)

    assert run_pool(conn) == 1


def test_group_pool_counts_session_and_sessionless_traces_together(conn):
    _, version_id = add_signal(conn)
    add_event(conn, version_id, vector_at(0.05, axis=1), session_id="conversation-1")
    add_event(conn, version_id, vector_at(0.05, axis=2), session_id="conversation-1")
    add_event(conn, version_id, vector_at(0.05, axis=3), session_id=None)

    assert run_pool(conn) == 1          # conversation-1 + one sessionless trace = 2 conversations


def test_group_pool_ignores_events_older_than_the_window(conn):
    _, version_id = add_signal(conn)
    add_event(conn, version_id, vector_at(0.05, axis=1), session_id="conversation-1")
    add_event(conn, version_id, vector_at(0.05, axis=2), session_id="conversation-2")
    old_event = add_event(conn, version_id, vector_at(0.05, axis=3), session_id="conversation-3",
                          created_days_ago=15)

    assert run_pool(conn) == 0
    assert cluster_of(conn, old_event) is None


def test_group_pool_counts_events_inside_the_window(conn):
    _, version_id = add_signal(conn)
    add_event(conn, version_id, vector_at(0.05, axis=1), session_id="conversation-1")
    add_event(conn, version_id, vector_at(0.05, axis=2), session_id="conversation-2")
    add_event(conn, version_id, vector_at(0.05, axis=3), session_id="conversation-3",
              created_days_ago=13)

    assert run_pool(conn) == 1


def test_group_pool_uses_average_linkage_on_real_events(conn):
    signal_id, version_id = add_signal(conn)
    vectors = vectors_with_distances([
        [0.00, 0.20, 0.40],
        [0.20, 0.00, 0.28],
        [0.40, 0.28, 0.00],
    ])
    first = add_event(conn, version_id, vectors[0], session_id="conversation-1")
    second = add_event(conn, version_id, vectors[1], session_id="conversation-2")
    outsider = add_event(conn, version_id, vectors[2], session_id="conversation-3")

    assert run_pool(conn, min_events=2) == 1
    assert list(clusters_of_signal(conn, signal_id).values()) == [sorted([first, second])]
    assert cluster_of(conn, outsider) is None


def test_group_pool_forms_two_clusters_from_two_separate_groups(conn):
    signal_id, version_id = add_signal(conn)
    near_base = [add_event(conn, version_id, vector_at(0.05, axis), session_id=f"base-{axis}")
                 for axis in (1, 2, 3)]
    # A second group around axis 10, far (~0.95+) from the first.
    far_center = vector_at(0.95, axis=10)
    far_group = [add_event(conn, version_id, far_center, session_id=f"far-{index}")
                 for index in range(3)]

    assert run_pool(conn) == 2
    assert sorted(clusters_of_signal(conn, signal_id).values()) == sorted(
        [sorted(near_base), sorted(far_group)])


# ------------------------------------------------------------------ group_pool: scope

def test_group_pool_never_groups_events_of_different_signals(conn):
    _, version_a = add_signal(conn, "Signal A")
    _, version_b = add_signal(conn, "Signal B")
    add_event(conn, version_a, base_vector(), session_id="conversation-1")
    add_event(conn, version_a, base_vector(), session_id="conversation-2")
    add_event(conn, version_b, base_vector(), session_id="conversation-3")

    assert run_pool(conn) == 0          # 3 identical events, but only 2 per Signal


def test_group_pool_groups_events_across_versions_of_one_signal(conn):
    signal_id, version_1 = add_signal(conn)
    version_2 = add_version(conn, signal_id, 2)
    add_event(conn, version_1, vector_at(0.05, axis=1), session_id="conversation-1")
    add_event(conn, version_2, vector_at(0.05, axis=2), session_id="conversation-2")
    add_event(conn, version_2, vector_at(0.05, axis=3), session_id="conversation-3")

    assert run_pool(conn) == 1          # clusters belong to the Signal, not a version (Q6)


def test_group_pool_second_pass_does_nothing(conn):
    _, version_id = add_signal(conn)
    for axis in (1, 2, 3):
        add_event(conn, version_id, vector_at(0.05, axis), session_id=f"conversation-{axis}")

    assert run_pool(conn) == 1
    assert run_pool(conn) == 0          # members have left the pool


# ------------------------------------------------------------------ new clusters' centers

def test_group_pool_sets_center_to_members_average_and_records_distances(conn):
    signal_id, version_id = add_signal(conn)
    vectors = [vector_at(0.10, axis) for axis in (1, 2, 3)]
    events = [add_event(conn, version_id, vector, session_id=f"conversation-{index}")
              for index, vector in enumerate(vectors)]

    run_pool(conn)
    cluster_id = next(iter(clusters_of_signal(conn, signal_id)))
    average = [sum(values) / 3 for values in zip(*vectors)]
    center_offset = conn.execute(
        "SELECT centroid <=> %s::vector FROM clusters WHERE id = %s",
        (vector_text(average), cluster_id),
    ).fetchone()[0]
    assert center_offset == pytest.approx(0, abs=TOLERANCE)

    for event_id, vector in zip(events, vectors):
        recorded = conn.execute("SELECT distance FROM cluster_members WHERE event_id = %s",
                                (event_id,)).fetchone()[0]
        assert recorded == pytest.approx(cosine_distance(conn, vector, average), abs=TOLERANCE)


def test_new_cluster_takes_members_through_assign_on_the_next_pass(conn):
    signal_id, version_id = add_signal(conn)
    for axis in (1, 2, 3):
        add_event(conn, version_id, vector_at(0.05, axis), session_id=f"conversation-{axis}")
    run_pool(conn)
    cluster_id = next(iter(clusters_of_signal(conn, signal_id)))

    newcomer = add_event(conn, version_id, vector_at(0.05, axis=4), session_id="conversation-4")
    assert assign_pool(conn, THRESHOLD) == 1
    assert cluster_of(conn, newcomer) == cluster_id


# ------------------------------------------------------------------ failures

def test_group_pool_error_in_one_signal_does_not_stop_the_others(conn, caplog):
    broken_signal, broken_version = add_signal(conn, "Signal A")   # processed first (lower ID)
    healthy_signal, healthy_version = add_signal(conn, "Signal B")
    zero_vector = [0.0] * len(base_vector())                       # cosine distance undefined
    add_event(conn, broken_version, zero_vector, session_id="conversation-1")
    for axis in (1, 2):
        add_event(conn, broken_version, vector_at(0.05, axis), session_id=f"conversation-{axis + 1}")
    for axis in (1, 2, 3):
        add_event(conn, healthy_version, vector_at(0.05, axis), session_id=f"conversation-{axis}")

    with caplog.at_level(logging.ERROR, logger="clusterer"):
        assert run_pool(conn) == 1
    assert clusters_of_signal(conn, broken_signal) == {}
    assert len(clusters_of_signal(conn, healthy_signal)) == 1
    assert f"pool grouping failed for signal {broken_signal}" in caplog.text
