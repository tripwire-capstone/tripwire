"""`score restore` on real Postgres: a pg_dump of a test database, restored like a run's dump."""

import subprocess
import uuid

import psycopg
import pytest
from psycopg import sql

from scorer import registry, restore
from scorer.database import libpq_env, maintenance_url, url_for


def test_count_copy_rows_reads_each_copy_block():
    text = ("SET x;\nCOPY public.traces (id, a) FROM stdin;\n1\tx\n2\ty\n\\.\n"
            "COPY public.\"clusters\" (id) FROM stdin;\n\\.\nSELECT 1;\n")
    assert restore.count_copy_rows(text) == {"traces": 2, "clusters": 0}


def test_restore_refuses_the_live_database():
    run = registry.Run("x", "tau", "tuning", None, "tripwire", None, None, "")
    with pytest.raises(restore.RestoreError, match="live database"):
        restore.restore_run(run)


@pytest.fixture
def dumped(run_db, server_url, tmp_path):
    """A custom-format dump of a test database holding one signal and two events."""
    from tests.clustering_data import add_event, add_signal
    from tests.vectors import base_vector

    name, conn = run_db
    _, version_id = add_signal(conn, "Issues")
    add_event(conn, version_id, base_vector(), session_id="c1")
    add_event(conn, version_id, None, session_id="c2")
    dump = tmp_path / "run.dump"
    subprocess.run(["pg_dump", "-Fc", "-f", str(dump), name], env=libpq_env(url_for(name, server_url)), check=True)
    target = f"scorer_test_restore_{uuid.uuid4().hex[:8]}"
    yield registry.Run("t", "tau", "tuning", dump, target, None, None, ""), server_url
    with psycopg.connect(maintenance_url(server_url), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(target)))


def test_restore_then_skip_then_fail_loudly_on_changed_counts(dumped):
    run, server = dumped
    assert restore.restore_run(run, server, log=lambda message: None) == "restored"
    with psycopg.connect(url_for(run.db, server), autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM signal_events").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM event_embeddings").fetchone()[0] == 1
        # the schema is the working tree's migrations (newest projects columns exist)
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'projects'")}
        assert "slack_verified_at" in columns
    assert restore.restore_run(run, server, log=lambda message: None) == "skipped"

    with psycopg.connect(url_for(run.db, server), autocommit=True) as conn:
        conn.execute("DELETE FROM event_embeddings")
    with pytest.raises(restore.RestoreError, match="row counts differ"):
        restore.restore_run(run, server, log=lambda message: None)
