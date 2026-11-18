"""Migration 20261117130000_project_keys: the seed's placeholder becomes "no saved key"."""

import os
import uuid
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
THIS = "20261117130000_project_keys.sql"


@pytest.fixture
def before_this_migration():
    """A schema with every migration before this one, and a project as the old seed stored it."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    import psycopg
    schema = f"test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}, public")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name >= THIS:
                break
            conn.execute(path.read_text())
        conn.execute("""INSERT INTO projects (name, laminar_api_url, laminar_ui_url,
                                              laminar_api_key_encrypted, laminar_api_key_hint)
                        VALUES ('default', 'http://x', 'http://x', 'not-encrypted-yet', 'abcd')""")
        try:
            yield conn
        finally:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


def apply_this(conn):
    conn.execute((MIGRATIONS / THIS).read_text())


def test_project_keys_migration_turns_the_seed_placeholder_into_no_saved_key(before_this_migration):
    apply_this(before_this_migration)
    row = before_this_migration.execute(
        """SELECT laminar_api_key_encrypted, laminar_api_key_hint, openai_api_key_encrypted,
                  openai_api_key_hint FROM projects""").fetchone()
    assert row == (None, None, None, None)


def test_project_keys_migration_keeps_a_real_saved_key(before_this_migration):
    before_this_migration.execute("UPDATE projects SET laminar_api_key_encrypted = 'ciphertext'")
    apply_this(before_this_migration)
    key, hint = before_this_migration.execute(
        "SELECT laminar_api_key_encrypted, laminar_api_key_hint FROM projects").fetchone()
    assert (bytes(key), hint) == (b"ciphertext", "abcd")
