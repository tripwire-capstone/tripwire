"""Tests for llm_naming.py (SIG-65/66): LLM cluster names, fallback, renames on growth."""

import pytest

from tests.clustering_data import add_cluster, add_event, add_member, add_signal, add_summary
from tests.vectors import base_vector, vector_at
from tripwire.clusterer.llm_naming import (
    NAMED_CLUSTERS_SQL,
    Job,
    NamedCluster,
    find_work,
    rename_is_due,
    renames_due,
)


def member(conn, version_id, cluster_id, distance=0.1, axis=1, summary="a summary"):
    """An event in `cluster_id`, `distance` from the base vector, with a done summary (or none)."""
    event_id = add_event(conn, version_id, vector_at(distance, axis))
    add_member(conn, event_id, cluster_id)
    if summary is not None:
        add_summary(conn, event_id, summary)
    return event_id


def cluster_with(conn, signal_id, version_id, size, name=None, source=None, named_count=None):
    """A leaf cluster with `size` summarized members, optionally already named."""
    cluster_id = add_cluster(conn, signal_id, base_vector())
    for index in range(size):
        member(conn, version_id, cluster_id, axis=index + 1, summary=f"summary {index}")
    if name is not None:
        conn.execute(
            """UPDATE clusters SET name = %s, name_source = %s, named_member_count = %s,
                   named_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END
               WHERE id = %s""",
            (name, source, named_count, source, cluster_id),
        )
    return cluster_id


def renames(jobs):
    return [job.cluster_id for job in jobs if job.kind == "rename"]


# ------------------------------------------------------------------ find_work: new clusters

def test_an_unnamed_cluster_is_new_work_with_its_closest_summary_as_fallback(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_id, cluster_id, distance=0.2, axis=1, summary="farther")
    member(conn, version_id, cluster_id, distance=0.05, axis=2, summary="closest")

    assert find_work(conn, growth=2.0, max_renames=10) == [
        Job(cluster_id, signal_id, "new", fallback="closest")]


def test_an_unnamed_cluster_with_no_summarized_member_is_left_for_later(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_id, cluster_id, summary=None)
    assert find_work(conn, growth=2.0, max_renames=10) == []


def test_new_clusters_come_in_id_order_before_renames(conn):
    signal_id, version_id = add_signal(conn)
    old_step_named = cluster_with(conn, signal_id, version_id, 3, name="Old name")
    first = cluster_with(conn, signal_id, version_id, 3)
    second = cluster_with(conn, signal_id, version_id, 3)

    jobs = find_work(conn, growth=2.0, max_renames=10)
    assert [(job.cluster_id, job.kind) for job in jobs] == [
        (first, "new"), (second, "new"), (old_step_named, "rename")]


# ------------------------------------------------------------------ rename_is_due / renames_due (pure)

def named(cluster_id=1, members=4, named_count=None, summarized=None, source="llm"):
    """A NamedCluster as NAMED_CLUSTERS_SQL returns it. named_count=None = named by the old step."""
    return NamedCluster(cluster_id, signal_id=1, name="Some Name",
                        source=None if named_count is None else source, named_at=None,
                        named_member_count=named_count, members=members,
                        summarized=members if summarized is None else summarized)


@pytest.mark.parametrize("growth, named_count, members, due", [
    (2.0, 4, 7, False),     # one short of double
    (2.0, 4, 8, True),      # exactly double
    (1.5, 5, 7, False),     # 7.5 rounds up to 8
    (1.5, 5, 8, True),
    (1.1, 50, 55, True),    # 1.1 x 50 as a float is 55.00000000000001: must not wait for 56
    (1.1, 50, 54, False),
])
def test_rename_is_due_at_growth_times_the_named_size(growth, named_count, members, due):
    assert rename_is_due(named(members=members, named_count=named_count), growth) is due


def test_a_name_from_the_old_step_is_due_whatever_the_size():
    assert rename_is_due(named(members=3, named_count=None), growth=2.0)


def test_a_fallback_name_is_due_at_the_same_doubling_as_an_llm_name():
    for source in ("llm", "fallback"):
        assert not rename_is_due(named(members=5, named_count=3, source=source), growth=2.0)
        assert rename_is_due(named(members=6, named_count=3, source=source), growth=2.0)


def test_renames_are_capped_most_overdue_first():
    tripled = named(1, members=6, named_count=2)
    doubled = named(2, members=4, named_count=2)
    old_step = named(3, members=3, named_count=None)
    quadrupled = named(4, members=8, named_count=2)
    picked = renames_due([tripled, doubled, old_step, quadrupled], growth=2.0, max_renames=3)
    assert [c.cluster_id for c in picked] == [3, 4, 1]


def test_equally_overdue_renames_go_oldest_cluster_first():
    picked = renames_due([named(7, members=4, named_count=2), named(5, members=6, named_count=3)],
                         growth=2.0, max_renames=1)
    assert [c.cluster_id for c in picked] == [5]


def test_a_cap_of_zero_means_no_renames():
    assert renames_due([named(members=3, named_count=None)], growth=2.0, max_renames=0) == []


def test_a_cluster_with_no_summarized_member_is_never_due():
    assert renames_due([named(members=3, named_count=None, summarized=0)], 2.0, 10) == []


# ------------------------------------------------------------------ find_work: renames (database)

def test_named_clusters_are_read_with_members_and_summarized_counts(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = cluster_with(conn, signal_id, version_id, 2, name="Old name")
    member(conn, version_id, cluster_id, axis=9, summary=None)    # a member with no summary
    [row] = conn.execute(NAMED_CLUSTERS_SQL).fetchall()
    assert NamedCluster(*row) == NamedCluster(cluster_id, signal_id, "Old name", None, None, None,
                                              members=3, summarized=2)


def test_a_name_from_the_old_step_becomes_a_rename_job_with_its_current_name(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = cluster_with(conn, signal_id, version_id, 3, name="Old step's summary")
    assert find_work(conn, growth=2.0, max_renames=10) == [
        Job(cluster_id, signal_id, "rename", current_name="Old step's summary")]


def test_a_grown_llm_name_becomes_a_rename_job_and_an_ungrown_one_does_not(conn):
    signal_id, version_id = add_signal(conn)
    grown = cluster_with(conn, signal_id, version_id, 4, name="A", source="llm", named_count=2)
    cluster_with(conn, signal_id, version_id, 3, name="B", source="llm", named_count=2)
    assert renames(find_work(conn, growth=2.0, max_renames=10)) == [grown]


def test_parent_clusters_and_clusters_without_a_center_are_never_work(conn):
    signal_id, version_id = add_signal(conn)
    parent = add_cluster(conn, signal_id, base_vector(), level=1)
    no_center = add_cluster(conn, signal_id, None)
    for cluster_id in (parent, no_center):
        member(conn, version_id, cluster_id)
    assert find_work(conn, growth=2.0, max_renames=10) == []
    conn.execute("UPDATE clusters SET name = 'Old name'")
    assert find_work(conn, growth=2.0, max_renames=10) == []
