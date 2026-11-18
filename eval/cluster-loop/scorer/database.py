"""Connections to the per-run databases, in the same Postgres that docker compose runs.

The server comes from TEST_DATABASE_URL (signals/backend/.env, as the backend tests use); only the
database name is swapped for the run's `db`. The live `tripwire` database is refused everywhere.
"""

import os

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from . import BACKEND_DIR
from .registry import LIVE_DB


class DatabaseError(RuntimeError):
    pass


def server_url():
    load_dotenv(BACKEND_DIR / ".env")
    url = os.environ.get("SCORE_DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise DatabaseError("set TEST_DATABASE_URL (signals/backend/.env) to the docker compose Postgres")
    return url


def url_for(db, base=None):
    if db == LIVE_DB:
        raise DatabaseError(f"refusing to use the live database {LIVE_DB!r}")
    return make_conninfo(base or server_url(), dbname=db)


def maintenance_url(base=None):
    """A connection for CREATE/DROP DATABASE: the `postgres` database of the same server."""
    return make_conninfo(base or server_url(), dbname="postgres")


def connect(db, base=None):
    return psycopg.connect(url_for(db, base), autocommit=True)


def database_exists(db, base=None):
    with psycopg.connect(maintenance_url(base), autocommit=True) as conn:
        return conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone() is not None


def libpq_env(url):
    """Environment for pg_restore: connection fields as PG* variables, so no password is on argv."""
    fields = conninfo_to_dict(url)
    mapping = {"host": "PGHOST", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD",
               "dbname": "PGDATABASE"}
    env = dict(os.environ)
    env.update({mapping[key]: str(value) for key, value in fields.items() if key in mapping and value})
    return env
