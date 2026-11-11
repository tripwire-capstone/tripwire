"""Tests for steps 4 and 5: naming new clusters and moving centers (real Postgres + pgvector)."""

import pytest

from tests.clustering_data import (
    add_cluster,
    add_event,
    add_member,
    add_signal,
    add_summary,
    name_of,
)
from tests.vectors import base_vector, vector_at
from tripwire.clusterer.assign import assign_pool
from tripwire.clusterer.naming import name_new_clusters, unique_name, update_centers
from tripwire.placeholder import vector_text

THRESHOLD = 0.30
TOLERANCE = 1e-5


def clustered_event(conn, version_id, cluster_id, embedding, summary=None):
    """An event that's already a member of `cluster_id`, optionally with a finished summary."""
    event_id = add_event(conn, version_id, embedding)
    add_member(conn, event_id, cluster_id)
    if summary is not None:
        add_summary(conn, event_id, summary)
    return event_id


def center_offset(conn, cluster_id, vector):
    """Cosine distance from the cluster's stored center to `vector`."""
    return conn.execute(
        "SELECT centroid <=> %s::vector FROM clusters WHERE id = %s",
        (vector_text(vector), cluster_id),
    ).fetchone()[0]


def average(vectors):
    return [sum(values) / len(vectors) for values in zip(*vectors)]


# ------------------------------------------------------------------ assign reports what it joined

def test_assign_pool_returns_which_clusters_gained_members(conn):
    signal_id, version_id = add_signal(conn)
    near_cluster = add_cluster(conn, signal_id, base_vector())
    add_cluster(conn, signal_id, vector_at(0.9, axis=9))      # far from everything: gains nothing
    add_event(conn, version_id, vector_at(0.1, axis=1))
    add_event(conn, version_id, vector_at(0.2, axis=2))

    joins = assign_pool(conn, THRESHOLD)
    assert {join.cluster_id for join in joins} == {near_cluster}
    assert len(joins) == 2


# ------------------------------------------------------------------ update_centers (step 5)

def test_update_centers_moves_center_to_average_of_all_members(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    vectors = [vector_at(0.1, axis=1), vector_at(0.1, axis=2), vector_at(0.2, axis=3)]
    for vector in vectors:
        clustered_event(conn, version_id, cluster_id, vector)

    update_centers(conn, [cluster_id])
    assert center_offset(conn, cluster_id, average(vectors)) == pytest.approx(0, abs=TOLERANCE)


def test_update_centers_leaves_other_clusters_alone(conn):
    signal_id, version_id = add_signal(conn)
    touched = add_cluster(conn, signal_id, base_vector())
    untouched = add_cluster(conn, signal_id, base_vector())
    clustered_event(conn, version_id, touched, vector_at(0.2, axis=1))
    clustered_event(conn, version_id, untouched, vector_at(0.2, axis=2))   # center now stale

    update_centers(conn, [touched])
    assert center_offset(conn, untouched, base_vector()) == pytest.approx(0, abs=TOLERANCE)


def test_update_centers_keeps_center_of_a_cluster_with_no_members(conn):
    signal_id, _ = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, vector_at(0.2, axis=1))

    update_centers(conn, [cluster_id])
    assert center_offset(conn, cluster_id, vector_at(0.2, axis=1)) == pytest.approx(0, abs=TOLERANCE)


def test_update_centers_with_no_clusters_does_nothing(conn):
    update_centers(conn, [])   # no error, no query


def test_moved_center_lets_a_waiting_event_join_on_the_next_pass(conn):
    # Steps 2 and 5 together: a waiting event 0.33 away joins once new members pull the center
    # toward it (the reason assign checks the whole pool each pass).
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    waiting = add_event(conn, version_id, vector_at(0.33, axis=1))
    assert assign_pool(conn, THRESHOLD) == []

    for new_member in range(3):
        clustered_event(conn, version_id, cluster_id, vector_at(0.2, axis=1))
    update_centers(conn, [cluster_id])
    assert [join.event_id for join in assign_pool(conn, THRESHOLD)] == [waiting]


# ------------------------------------------------------------------ name_new_clusters (step 4)

def test_name_new_clusters_uses_summary_of_member_closest_to_center(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    clustered_event(conn, version_id, cluster_id, vector_at(0.20, axis=1), "Farther member")
    clustered_event(conn, version_id, cluster_id, vector_at(0.05, axis=2), "Most typical member")
    clustered_event(conn, version_id, cluster_id, vector_at(0.10, axis=3), "Middle member")

    assert name_new_clusters(conn) == 1
    assert name_of(conn, cluster_id) == "Most typical member"


def test_name_new_clusters_skips_closest_member_without_summary(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    clustered_event(conn, version_id, cluster_id, vector_at(0.05, axis=1))            # no summary
    clustered_event(conn, version_id, cluster_id, vector_at(0.10, axis=2), "Next closest")

    name_new_clusters(conn)
    assert name_of(conn, cluster_id) == "Next closest"


def test_name_new_clusters_ignores_unfinished_summaries(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    pending_event = clustered_event(conn, version_id, cluster_id, vector_at(0.05, axis=1))
    conn.execute(
        """INSERT INTO event_summaries (event_id, status, model, prompt_version)
           VALUES (%s, 'pending', 'test-model', 1)""",
        (pending_event,),
    )
    clustered_event(conn, version_id, cluster_id, vector_at(0.10, axis=2), "Finished summary")

    name_new_clusters(conn)
    assert name_of(conn, cluster_id) == "Finished summary"


def test_name_new_clusters_leaves_name_empty_and_retries_when_no_member_has_summary(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    event_id = clustered_event(conn, version_id, cluster_id, vector_at(0.05, axis=1))

    assert name_new_clusters(conn) == 0
    assert name_of(conn, cluster_id) is None

    add_summary(conn, event_id, "Summary arrived later")
    assert name_new_clusters(conn) == 1
    assert name_of(conn, cluster_id) == "Summary arrived later"


def test_name_new_clusters_never_renames_a_named_cluster(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    clustered_event(conn, version_id, cluster_id, vector_at(0.10, axis=1), "First name")
    name_new_clusters(conn)

    clustered_event(conn, version_id, cluster_id, base_vector(), "Now even more typical")
    assert name_new_clusters(conn) == 0
    assert name_of(conn, cluster_id) == "First name"      # named once (refresh is #20, later)


def test_name_new_clusters_breaks_a_tie_with_the_lower_event_id(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    clustered_event(conn, version_id, cluster_id, vector_at(0.1, axis=1), "Older event")
    clustered_event(conn, version_id, cluster_id, vector_at(0.1, axis=2), "Newer event")

    name_new_clusters(conn)
    assert name_of(conn, cluster_id) == "Older event"


def test_name_new_clusters_numbers_a_repeated_name_within_a_signal(conn):
    signal_id, version_id = add_signal(conn)
    first = add_cluster(conn, signal_id, base_vector())
    second = add_cluster(conn, signal_id, vector_at(0.9, axis=8))
    third = add_cluster(conn, signal_id, vector_at(0.9, axis=9))
    clustered_event(conn, version_id, first, base_vector(), "Booking tool returned an error")
    clustered_event(conn, version_id, second, vector_at(0.9, axis=8), "Booking tool returned an error")
    clustered_event(conn, version_id, third, vector_at(0.9, axis=9), "Booking tool returned an error")

    assert name_new_clusters(conn) == 3
    assert [name_of(conn, cluster) for cluster in (first, second, third)] == [
        "Booking tool returned an error",
        "Booking tool returned an error (2)",
        "Booking tool returned an error (3)",
    ]


def test_name_new_clusters_numbers_against_names_given_in_earlier_passes(conn):
    signal_id, version_id = add_signal(conn)
    older = add_cluster(conn, signal_id, base_vector())
    clustered_event(conn, version_id, older, base_vector(), "Booking tool returned an error")
    name_new_clusters(conn)

    newer = add_cluster(conn, signal_id, vector_at(0.9, axis=8))
    clustered_event(conn, version_id, newer, vector_at(0.9, axis=8), "Booking tool returned an error")
    name_new_clusters(conn)
    assert name_of(conn, newer) == "Booking tool returned an error (2)"


def test_name_new_clusters_allows_the_same_name_in_different_signals(conn):
    signal_a, version_a = add_signal(conn, "Signal A")
    signal_b, version_b = add_signal(conn, "Signal B")
    cluster_a = add_cluster(conn, signal_a, base_vector())
    cluster_b = add_cluster(conn, signal_b, base_vector())
    clustered_event(conn, version_a, cluster_a, base_vector(), "Booking tool returned an error")
    clustered_event(conn, version_b, cluster_b, base_vector(), "Booking tool returned an error")

    name_new_clusters(conn)
    assert name_of(conn, cluster_a) == name_of(conn, cluster_b) == "Booking tool returned an error"


def test_name_new_clusters_ignores_non_leaf_clusters(conn):
    signal_id, version_id = add_signal(conn)
    parent = add_cluster(conn, signal_id, base_vector(), level=1)
    clustered_event(conn, version_id, parent, base_vector(), "A summary")

    assert name_new_clusters(conn) == 0
    assert name_of(conn, parent) is None


def test_unique_name_skips_numbers_already_taken():
    taken = {"Timeout", "Timeout (2)"}
    assert unique_name("Timeout", taken) == "Timeout (3)"
    assert unique_name("Refund failed", taken) == "Refund failed"
