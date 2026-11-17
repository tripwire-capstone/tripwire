"""Prepare Tripwire's database on AWS: python -m tripwire.migrate

Run once per deploy, by a short ECS task, before the services update (the Tripwire CLI starts it).
Locally you don't need it: docker compose's Postgres runs migrations/ on an empty volume.

Every step is safe to run again:
  1. As the RDS master user: create the tripwire role (or reset its password) and the tripwire
     database owned by it, and enable pgvector there (needs the master user's rights on RDS).
  2. As the tripwire user: apply each file in migrations/ that isn't recorded yet in
     schema_migrations, in name order, each in its own transaction with its record.
  3. Seed the first project and signal (tripwire/seed.py; does nothing if already seeded).

Files are recorded by name: never edit or rename a migration once it has been deployed, add a new
one (infra/docs/TRIPWIRE_ON_AWS_RULES.md). Exits 0 on success, 1 on failure; passwords are never
printed.

Settings (env):
  MASTER_DATABASE_URL   the RDS master user's connection (any database it can log into)
  TRIPWIRE_DB_PASSWORD  the password to give the tripwire user
  TRIPWIRE_DB_NAME      default tripwire (also the user's name)
  PGSSLMODE             require on RDS (read by libpq itself)
  Seed: JUDGE_DEFAULT_MODEL, LMNR_API_URL, LMNR_UI_URL, LMNR_PROJECT_ID (no keys: they're saved
  on the Settings page, or come from each service's environment)
"""

import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from . import seed

log = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def ensure_database(master_conninfo, name, password):
    """Create role and database `name` (the role owns the database) and enable pgvector in it."""
    with psycopg.connect(master_conninfo, autocommit=True) as conn:
        role = sql.Identifier(name)
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone():
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(role, sql.Literal(password)))
            log.info("role %s exists; password updated", name)
        else:
            conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(role, sql.Literal(password)))
            log.info("created role %s", name)
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            # On RDS the master user isn't a superuser: to make another role a database's owner it
            # must be able to act as that role, so it grants itself membership first.
            conn.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(role))
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(role, role))
            log.info("created database %s", name)
    with psycopg.connect(make_conninfo(master_conninfo, dbname=name), autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def pending_migrations(conn, directory):
    """Files in `directory` (name order) not yet recorded in schema_migrations."""
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                        filename   text PRIMARY KEY,
                        applied_at timestamptz NOT NULL DEFAULT now())""")
    done = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
    return [path for path in sorted(Path(directory).glob("*.sql")) if path.name not in done]


def apply_migrations(conn, directory=MIGRATIONS_DIR):
    """Apply each pending migration in its own transaction with its record; returns their names.

    A failing file rolls back alone and raises; files before it stay applied."""
    applied = []
    for path in pending_migrations(conn, directory):
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
        log.info("applied %s", path.name)
        applied.append(path.name)
    return applied


def seed_from_env(conn):
    return seed.seed(conn,
                     api_url=os.environ["LMNR_API_URL"],
                     ui_url=os.environ["LMNR_UI_URL"],
                     laminar_project_id=os.environ.get("LMNR_PROJECT_ID") or None)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    master = os.environ["MASTER_DATABASE_URL"]
    name = os.environ.get("TRIPWIRE_DB_NAME", "tripwire")
    password = os.environ["TRIPWIRE_DB_PASSWORD"]
    try:
        ensure_database(master, name, password)
        tripwire_conninfo = make_conninfo(master, dbname=name, user=name, password=password)
        with psycopg.connect(tripwire_conninfo, autocommit=True) as conn:
            applied = apply_migrations(conn)
            seeded = seed_from_env(conn)
    except Exception:
        log.exception("migration failed")
        return 1
    log.info("done: %d migration(s) applied%s", len(applied), ", seeded" if seeded else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
