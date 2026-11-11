"""Tests for step 2, assigning pool events to existing clusters (real Postgres + pgvector).

Vectors come from tests/vectors.py, placed at exact cosine distances. pgvector stores 32-bit
floats, so database distances are only accurate to about 1e-6: the exact-boundary case
(distance == threshold) is tested on decide() directly, and database tests stay clear of it.
"""

import random

import pytest

from tests.clustering_data import add_cluster, add_event, add_signal, cluster_of
from tests.vectors import base_vector, vector_at
from tripwire.clusterer.assign import Decision, assign_pool, decide
from tripwire.placeholder import vector_text

THRESHOLD = 0.30
TOLERANCE = 1e-5

# ------------------------------------------------------------------ decide

def test_decide_joins_below_threshold():
    assert decide(0.29, THRESHOLD) is Decision.JOIN


def test_decide_joins_exactly_at_threshold():
    assert decide(0.30, THRESHOLD) is Decision.JOIN


def test_decide_waits_above_threshold():
    assert decide(0.31, THRESHOLD) is Decision.WAIT


# ------------------------------------------------------------------ assign_pool

def test_assign_pool_joins_event_within_threshold_and_records_distance(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    event_id = add_event(conn, version_id, vector_at(0.29, axis=1))

    assert len(assign_pool(conn, THRESHOLD)) == 1
    assert cluster_of(conn, event_id) == cluster_id
    recorded = conn.execute(
        "SELECT distance FROM cluster_members WHERE event_id = %s", (event_id,)
    ).fetchone()[0]
    assert recorded == pytest.approx(0.29, abs=TOLERANCE)


def test_assign_pool_leaves_event_beyond_threshold_in_pool(conn):
    signal_id, version_id = add_signal(conn)
    add_cluster(conn, signal_id, base_vector())
    event_id = add_event(conn, version_id, vector_at(0.31, axis=1))

    assert len(assign_pool(conn, THRESHOLD)) == 0
    assert cluster_of(conn, event_id) is None


def test_assign_pool_picks_the_nearer_of_two_clusters(conn):
    signal_id, version_id = add_signal(conn)
    near_cluster = add_cluster(conn, signal_id, base_vector())
    add_cluster(conn, signal_id, vector_at(0.2, axis=2))
    # 0.10 from the near cluster, 1 - 0.9 * 0.8 = 0.28 from the other: both within the threshold.
    event_id = add_event(conn, version_id, vector_at(0.1, axis=1))

    assert len(assign_pool(conn, THRESHOLD)) == 1
    assert cluster_of(conn, event_id) == near_cluster


def test_assign_pool_breaks_a_tie_with_the_lower_cluster_id(conn):
    signal_id, version_id = add_signal(conn)
    first_cluster = add_cluster(conn, signal_id, base_vector())
    add_cluster(conn, signal_id, base_vector())
    event_id = add_event(conn, version_id, vector_at(0.1, axis=1))

    assign_pool(conn, THRESHOLD)
    assert cluster_of(conn, event_id) == first_cluster


def test_assign_pool_never_joins_another_signals_cluster(conn):
    own_signal, own_version = add_signal(conn, "Signal A")
    other_signal, _ = add_signal(conn, "Signal B")
    other_cluster = add_cluster(conn, other_signal, vector_at(0.1, axis=1))  # distance 0 to the event
    lonely_event = add_event(conn, own_version, vector_at(0.1, axis=1))

    assert len(assign_pool(conn, THRESHOLD)) == 0
    assert cluster_of(conn, lonely_event) is None

    own_cluster = add_cluster(conn, own_signal, base_vector())  # 0.10 away: farther than the other
    assert len(assign_pool(conn, THRESHOLD)) == 1
    assert cluster_of(conn, lonely_event) == own_cluster
    assert cluster_of(conn, lonely_event) != other_cluster


def test_assign_pool_leaves_event_in_pool_when_signal_has_no_clusters(conn):
    _, version_id = add_signal(conn)
    event_id = add_event(conn, version_id, base_vector())

    assert len(assign_pool(conn, THRESHOLD)) == 0
    assert cluster_of(conn, event_id) is None


def test_assign_pool_ignores_clusters_without_a_center(conn):
    signal_id, version_id = add_signal(conn)
    add_cluster(conn, signal_id, None)
    event_id = add_event(conn, version_id, base_vector())

    assert len(assign_pool(conn, THRESHOLD)) == 0
    assert cluster_of(conn, event_id) is None


def test_assign_pool_ignores_non_leaf_clusters(conn):
    signal_id, version_id = add_signal(conn)
    add_cluster(conn, signal_id, base_vector(), level=1)
    event_id = add_event(conn, version_id, base_vector())

    assert len(assign_pool(conn, THRESHOLD)) == 0
    assert cluster_of(conn, event_id) is None


def test_assign_pool_does_not_reconsider_members(conn):
    signal_id, version_id = add_signal(conn)
    add_cluster(conn, signal_id, base_vector())
    far_cluster = add_cluster(conn, signal_id, vector_at(0.25, axis=2))
    event_id = add_event(conn, version_id, base_vector())
    conn.execute(
        "INSERT INTO cluster_members (event_id, cluster_id, distance) VALUES (%s, %s, 0.25)",
        (event_id, far_cluster),
    )

    assert len(assign_pool(conn, THRESHOLD)) == 0
    assert cluster_of(conn, event_id) == far_cluster


def test_assign_pool_second_pass_does_nothing(conn):
    signal_id, version_id = add_signal(conn)
    add_cluster(conn, signal_id, base_vector())
    add_event(conn, version_id, vector_at(0.1, axis=1))
    add_event(conn, version_id, vector_at(0.2, axis=2))

    assert len(assign_pool(conn, THRESHOLD)) == 2
    assert len(assign_pool(conn, THRESHOLD)) == 0


def test_assign_pool_rechecks_waiting_events_on_later_passes(conn):
    # Decided Nov 10: every pool event is checked each pass, so an event that was too far
    # joins once the center moves closer.
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    event_id = add_event(conn, version_id, vector_at(0.33, axis=1))

    assert len(assign_pool(conn, THRESHOLD)) == 0
    conn.execute(
        "UPDATE clusters SET centroid = %s WHERE id = %s",
        (vector_text(vector_at(0.05, axis=1)), cluster_id),
    )
    assert len(assign_pool(conn, THRESHOLD)) == 1
    assert cluster_of(conn, event_id) == cluster_id


def test_assign_pool_does_not_move_centers(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    add_event(conn, version_id, vector_at(0.2, axis=1))

    assign_pool(conn, THRESHOLD)
    moved_by = conn.execute(
        "SELECT centroid <=> %s::vector FROM clusters WHERE id = %s",
        (vector_text(base_vector()), cluster_id),
    ).fetchone()[0]
    assert moved_by == pytest.approx(0, abs=TOLERANCE)


def test_assign_pool_result_does_not_depend_on_event_order(conn):
    # Shuffle test (Q13): the same events, inserted in two different orders under two
    # signals with identical clusters, end up in the same clusters.
    events = {
        "near_first": vector_at(0.10, axis=1),
        "near_second": vector_at(0.15, axis=2),  # exactly the second cluster's center
        "between": vector_at(0.20, axis=3),
        "too_far": vector_at(0.40, axis=4),
        "near_first_again": vector_at(0.25, axis=5),
    }

    def run_in_order(signal_name, labels):
        signal_id, version_id = add_signal(conn, signal_name)
        first = add_cluster(conn, signal_id, base_vector())
        second = add_cluster(conn, signal_id, vector_at(0.15, axis=2))
        names = {first: "first", second: "second", None: "pool"}
        event_ids = {label: add_event(conn, version_id, events[label]) for label in labels}
        assign_pool(conn, THRESHOLD)
        return {label: names[cluster_of(conn, event_id)] for label, event_id in event_ids.items()}

    labels = list(events)
    shuffled = labels[:]
    random.Random(7).shuffle(shuffled)
    assert shuffled != labels

    in_order = run_in_order("Signal in order", labels)
    in_shuffled_order = run_in_order("Signal shuffled", shuffled)
    assert in_order == in_shuffled_order
    assert in_order["too_far"] == "pool"
    assert in_order["near_second"] == "second"
    assert in_order["near_first"] == "first"
