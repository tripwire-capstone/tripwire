import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from tripwire import migrate
from tripwire.migrate import apply_migrations, ensure_database, pending_migrations


@pytest.fixture
def schema_conn():
    """An autocommit connection whose search_path is a throwaway schema (dropped afterwards)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    schema = f"test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}, public")
        try:
            yield conn
        finally:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


def write_migrations(directory, files):
    directory.mkdir(exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text)
    return directory


def tables(conn):
    return {row[0] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}


def test_migrate_applies_files_in_name_order_and_records_them(schema_conn, tmp_path):
    directory = write_migrations(tmp_path / "m", {
        "002_second.sql": "ALTER TABLE things ADD COLUMN label text;",
        "001_first.sql": "CREATE TABLE things (id int);",
    })
    assert apply_migrations(schema_conn, directory) == ["001_first.sql", "002_second.sql"]
    assert {"things", "schema_migrations"} <= tables(schema_conn)
    recorded = [row[0] for row in schema_conn.execute("SELECT filename FROM schema_migrations ORDER BY 1")]
    assert recorded == ["001_first.sql", "002_second.sql"]


def test_migrate_rerun_applies_nothing(schema_conn, tmp_path):
    directory = write_migrations(tmp_path / "m", {"001_first.sql": "CREATE TABLE things (id int);"})
    apply_migrations(schema_conn, directory)
    assert apply_migrations(schema_conn, directory) == []


def test_migrate_applies_only_new_files(schema_conn, tmp_path):
    directory = write_migrations(tmp_path / "m", {"001_first.sql": "CREATE TABLE things (id int);"})
    apply_migrations(schema_conn, directory)
    write_migrations(directory, {"002_notifications.sql": "CREATE TABLE notifications (id int);"})
    assert apply_migrations(schema_conn, directory) == ["002_notifications.sql"]
    assert "notifications" in tables(schema_conn)


def test_migrate_runs_a_file_with_several_statements(schema_conn, tmp_path):
    directory = write_migrations(tmp_path / "m", {
        "001_two.sql": "CREATE TABLE a (id int);\nCREATE TABLE b (id int);\n"})
    apply_migrations(schema_conn, directory)
    assert {"a", "b"} <= tables(schema_conn)


def test_migrate_failing_file_rolls_back_alone_and_stops(schema_conn, tmp_path):
    directory = write_migrations(tmp_path / "m", {
        "001_ok.sql": "CREATE TABLE ok (id int);",
        "002_bad.sql": "CREATE TABLE half (id int);\nTHIS IS NOT SQL;",
        "003_later.sql": "CREATE TABLE later (id int);",
    })
    with pytest.raises(psycopg.Error):
        apply_migrations(schema_conn, directory)
    names = tables(schema_conn)
    assert "ok" in names
    assert "half" not in names and "later" not in names
    assert [path.name for path in pending_migrations(schema_conn, directory)] == ["002_bad.sql", "003_later.sql"]


def test_migrate_renamed_file_runs_again(schema_conn, tmp_path):
    # Why a deployed migration must never be renamed (infra/docs/TRIPWIRE_ON_AWS_RULES.md)
    directory = write_migrations(tmp_path / "m", {"001_things.sql": "CREATE TABLE things (id int);"})
    apply_migrations(schema_conn, directory)
    (directory / "001_things.sql").rename(directory / "001_renamed.sql")
    with pytest.raises(psycopg.errors.DuplicateTable):
        apply_migrations(schema_conn, directory)


def test_migrate_applies_the_real_migrations(schema_conn):
    applied = apply_migrations(schema_conn)
    assert applied == sorted(path.name for path in migrate.MIGRATIONS_DIR.glob("*.sql"))
    assert {"projects", "signal_events", "event_summaries", "clusters"} <= tables(schema_conn)


def test_migrate_seed_from_env_seeds_once(schema_conn, monkeypatch):
    apply_migrations(schema_conn)
    monkeypatch.setenv("JUDGE_DEFAULT_MODEL", "gpt-6-luna")
    monkeypatch.setenv("LMNR_API_URL", "http://app-server.tripwire.internal:8000")
    monkeypatch.setenv("LMNR_UI_URL", "https://laminar.example.com")
    monkeypatch.setenv("LMNR_PROJECT_API_KEY", "lmnr-key-abcd")
    monkeypatch.setenv("LMNR_PROJECT_ID", "42fc1e36-6c0e-4a69-9850-b0e849bb4656")
    assert migrate.seed_from_env(schema_conn) is True
    assert migrate.seed_from_env(schema_conn) is False
    row = schema_conn.execute("SELECT laminar_api_url, laminar_ui_url, laminar_api_key_hint, "
                              "laminar_project_id::text FROM projects").fetchone()
    assert row == ("http://app-server.tripwire.internal:8000", "https://laminar.example.com", None,
                   "42fc1e36-6c0e-4a69-9850-b0e849bb4656")   # the key itself is never stored by the seed


def test_migrate_ensure_database_creates_role_and_database_once():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    name = f"tw_test_{uuid.uuid4().hex[:8]}"
    try:
        ensure_database(url, name, "first-password")
        ensure_database(url, name, "second-password")  # rerun: no error, password updated
        own = make_conninfo(url, dbname=name, user=name, password="second-password")
        with psycopg.connect(own) as conn:
            assert conn.execute("SELECT current_user, current_database()").fetchone() == (name, name)
            assert conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
            conn.execute("CREATE TABLE owned_here (id int)")  # the role owns its database
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'DROP ROLE IF EXISTS "{name}"')
