"""Scorer test setup.

* No paid call can leave the machine: the real OpenAI clients raise if constructed.
* `run_db` gives a throwaway DATABASE (not a schema: the scorer connects to a run's database by
  name) on the TEST_DATABASE_URL server, with every migration and a 'test' project, dropped after.
  Database tests are skipped without TEST_DATABASE_URL, like the backend suite.
"""

import os
import sys
import uuid
from pathlib import Path

import pytest

LOOP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOOP_DIR))

import scorer  # noqa: E402,F401  (puts signals/backend on sys.path)


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    from tripwire.clusterer import embed, llm_client

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to build a real OpenAI client")
    monkeypatch.setattr(llm_client.OpenAIClient, "__init__", refuse)
    monkeypatch.setattr(embed.OpenAIEmbedder, "__init__", refuse)


@pytest.fixture
def server_url():
    from scorer.database import server_url as configured
    try:
        return configured()
    except Exception:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")


@pytest.fixture
def run_db(server_url):
    """(database name, autocommit connection) of a fresh, migrated database."""
    import psycopg
    from psycopg import sql
    from tripwire.clusterer.sweep import MIGRATIONS

    from scorer.database import maintenance_url, url_for

    name = f"scorer_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(maintenance_url(server_url), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    conn = psycopg.connect(url_for(name, server_url), autocommit=True)
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute("""INSERT INTO projects (name, laminar_api_url, laminar_ui_url)
                        VALUES ('test', 'http://laminar.test:8000', 'http://laminar.test:5667')""")
        yield name, conn
    finally:
        conn.close()
        with psycopg.connect(maintenance_url(server_url), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("CLUSTER"):
            monkeypatch.delenv(key)
