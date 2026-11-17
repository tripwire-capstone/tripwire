"""Migration 008: clusters.name_source / named_at / named_member_count, one test per rule."""

import psycopg
import pytest

from tests.clustering_data import add_cluster, add_signal
from tests.vectors import base_vector


@pytest.fixture
def cluster_id(conn):
    signal_id, _ = add_signal(conn)
    return add_cluster(conn, signal_id, base_vector())


def set_naming(conn, cluster_id, name="Booking Errors", source="llm", named_at="now", count=4):
    conn.execute(
        """UPDATE clusters SET name = %s, name_source = %s,
               named_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END,
               named_member_count = %s
           WHERE id = %s""",
        (name, source, named_at, count, cluster_id),
    )


def naming_of(conn, cluster_id):
    return conn.execute(
        "SELECT name, name_source, named_at IS NOT NULL, named_member_count FROM clusters WHERE id = %s",
        (cluster_id,),
    ).fetchone()


def test_a_new_cluster_has_no_naming_columns_set(conn, cluster_id):
    assert naming_of(conn, cluster_id) == (None, None, False, None)


def test_the_old_naming_step_can_still_set_only_the_name(conn, cluster_id):
    # naming.py (NAMING_MODEL=off) writes name and nothing else.
    conn.execute("UPDATE clusters SET name = 'A summary' WHERE id = %s", (cluster_id,))
    assert naming_of(conn, cluster_id) == ("A summary", None, False, None)


def test_llm_and_fallback_names_are_accepted_with_all_three_columns(conn, cluster_id):
    set_naming(conn, cluster_id, source="llm")
    assert naming_of(conn, cluster_id) == ("Booking Errors", "llm", True, 4)
    set_naming(conn, cluster_id, source="fallback")
    assert naming_of(conn, cluster_id)[1] == "fallback"


def test_an_unknown_name_source_is_rejected(conn, cluster_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        set_naming(conn, cluster_id, source="manual")


def test_a_member_count_below_one_is_rejected(conn, cluster_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        set_naming(conn, cluster_id, count=0)


@pytest.mark.parametrize("missing", ["named_at", "count"])
def test_the_three_columns_must_be_set_together(conn, cluster_id, missing):
    with pytest.raises(psycopg.errors.CheckViolation):
        set_naming(conn, cluster_id, **{missing: None})


def test_a_naming_record_without_a_name_is_rejected(conn, cluster_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        set_naming(conn, cluster_id, name=None)
