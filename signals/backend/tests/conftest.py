"""Shared test setup.

Loads .env so tests can see TEST_DATABASE_URL. The `conn` fixture gives each test a
throwaway schema with migration 001 applied and one project row, dropped afterwards,
so real tables are never touched. Without TEST_DATABASE_URL, database tests are skipped.
"""

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def conn(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    monkeypatch.setenv("JUDGE_DEFAULT_MODEL", "gpt-6-luna")
    import psycopg
    schema = f"test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as c:
        # The extension lives in public, shared by every test schema; the migration's
        # own CREATE EXTENSION IF NOT EXISTS is then a no-op.
        c.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path TO {schema}, public")
        for f in sorted(MIGRATIONS.glob("*.sql")):
            c.execute(f.read_text())
        c.execute("""INSERT INTO projects (name, laminar_api_url, laminar_ui_url,
                         laminar_api_key_encrypted, laminar_api_key_hint)
                     VALUES ('test', 'http://laminar.test:8000', 'http://laminar.test:5667',
                             'placeholder', 'test')""")
        try:
            yield c
        finally:
            c.execute(f"DROP SCHEMA {schema} CASCADE")
