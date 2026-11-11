"""Tests for one pass of the real clusterer (steps 1-5 together) on real Postgres + pgvector."""

import logging
import os

import psycopg
import pytest

from tests.clustering_data import (
    add_cluster,
    add_event,
    add_signal,
    add_summary,
    cluster_of,
    clusters_of_signal,
    name_of,
)
from tests.vectors import base_vector, vector_at
from tripwire.clusterer import loop
from tripwire.clusterer.loop import open_connection, prepare_stand_in, run_pass
from tripwire.clusterer.settings import load_settings
from tripwire.placeholder import vector_text

TOLERANCE = 1e-5


def real_settings(**overrides):
    environ = {"DATABASE_URL": "postgresql://unused", "CLUSTERER_MODE": "real"}
    environ.update({name: str(value) for name, value in overrides.items()})
    return load_settings(environ)


def summarized_event(conn, version_id, embedding, session_id, summary):
    event_id = add_event(conn, version_id, embedding, session_id=session_id)
    add_summary(conn, event_id, summary)
    return event_id


def center_offset(conn, cluster_id, vector):
    return conn.execute("SELECT centroid <=> %s::vector FROM clusters WHERE id = %s",
                        (vector_text(vector), cluster_id)).fetchone()[0]


# ------------------------------------------------------------------ a full pass

def test_run_pass_forms_and_names_a_new_cluster_from_the_pool(conn):
    signal_id, version_id = add_signal(conn)
    summarized_event(conn, version_id, vector_at(0.03, axis=1), "conversation-1", "Refund tool timed out")
    summarized_event(conn, version_id, vector_at(0.05, axis=2), "conversation-2", "Refund tool timed out again")
    summarized_event(conn, version_id, vector_at(0.10, axis=3), "conversation-3", "Refund tool was slow")

    result = run_pass(conn, real_settings())
    assert (result.created, result.named, result.failed_steps) == (1, 1, [])
    cluster_id = next(iter(clusters_of_signal(conn, signal_id)))
    assert name_of(conn, cluster_id) == "Refund tool timed out"    # the most typical member


def test_run_pass_joins_existing_cluster_and_moves_its_center(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    newcomer = add_event(conn, version_id, vector_at(0.2, axis=1))

    result = run_pass(conn, real_settings())
    assert (result.joined, result.centers_updated) == (1, 1)
    assert cluster_of(conn, newcomer) == cluster_id
    # Its only member is the newcomer, so the center moved onto it.
    assert center_offset(conn, cluster_id, vector_at(0.2, axis=1)) == pytest.approx(0, abs=TOLERANCE)


def test_run_pass_with_nothing_to_do_does_nothing(conn):
    result = run_pass(conn, real_settings())
    assert not result.anything_happened()


def test_run_pass_calls_prepare_with_the_batch_size(conn):
    calls = []
    run_pass(conn, real_settings(CLUSTER_PREPARE_BATCH=7),
             prepare=lambda connection, limit: calls.append(limit) or {"summarized": 0})
    assert calls == [7]


def test_prepare_stand_in_does_nothing(conn):
    assert prepare_stand_in(conn, 50) == {}


def test_run_pass_gives_assign_and_grouping_the_same_threshold(conn, monkeypatch):
    # Anti-drift: joining and grouping must use one threshold (Q8).
    seen = {}
    monkeypatch.setattr(loop, "assign_pool",
                        lambda connection, threshold: seen.setdefault("assign", threshold) and [])
    monkeypatch.setattr(loop, "group_pool",
                        lambda connection, threshold, *rest: seen.setdefault("group", threshold) and 0)
    run_pass(conn, real_settings(CLUSTER_ASSIGN_THRESHOLD=0.27))
    assert seen == {"assign": 0.27, "group": 0.27}


# ------------------------------------------------------------------ failures stay contained

def test_run_pass_carries_on_after_a_failing_step(conn, monkeypatch, caplog):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    newcomer = add_event(conn, version_id, vector_at(0.2, axis=1))

    def broken_naming(connection):
        raise RuntimeError("column does not exist")
    monkeypatch.setattr(loop, "name_new_clusters", broken_naming)

    with caplog.at_level(logging.ERROR, logger="clusterer"):
        result = run_pass(conn, real_settings())
    assert result.failed_steps == ["name"]
    assert cluster_of(conn, newcomer) == cluster_id              # assign still ran
    assert result.centers_updated == 1                           # and so did step 5
    assert center_offset(conn, cluster_id, vector_at(0.2, axis=1)) == pytest.approx(0, abs=TOLERANCE)
    assert "clusterer step 'name' failed" in caplog.text


def test_run_pass_skips_centers_cleanly_when_assign_fails(conn, monkeypatch):
    def broken_assign(connection, threshold):
        raise RuntimeError("database blip")
    monkeypatch.setattr(loop, "assign_pool", broken_assign)

    result = run_pass(conn, real_settings())
    assert result.failed_steps == ["assign"]                     # centers didn't fail: nothing to do
    assert result.centers_updated == 0


def test_run_pass_carries_on_when_prepare_fails(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    waiting = add_event(conn, version_id, vector_at(0.1, axis=1))   # embedded on an earlier pass

    def broken_prepare(connection, limit):
        raise RuntimeError("OpenAI outage")

    result = run_pass(conn, real_settings(), prepare=broken_prepare)
    assert result.failed_steps == ["prepare"]
    assert cluster_of(conn, waiting) == cluster_id


# ------------------------------------------------------------------ each save sticks (autocommit)

def test_open_connection_is_in_autocommit_mode():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    with open_connection(url) as connection:
        assert connection.autocommit


def test_saves_before_a_failing_step_are_visible_to_other_connections(conn, monkeypatch):
    # With autocommit, assign's joins are committed when assign finishes, even though a later
    # step fails in the same pass. A second connection (like the API) can already see them.
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    newcomer = add_event(conn, version_id, vector_at(0.2, axis=1))
    monkeypatch.setattr(loop, "name_new_clusters", lambda connection: 1 / 0)

    run_pass(conn, real_settings())
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as other:
        other.execute(f"SET search_path TO {schema}, public")
        seen = other.execute("SELECT cluster_id FROM cluster_members WHERE event_id = %s",
                             (newcomer,)).fetchone()
    assert seen == (cluster_id,)
