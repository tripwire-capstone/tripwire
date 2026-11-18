"""`score restore`: pg_restore each run's dump into its own database, idempotently.

How: create the database, apply the backend's migrations/ (the schema the working-tree code
expects), then load only the dump's DATA with `pg_restore --data-only`, whose COPY lists name each
column. The dumps predate migrations that add columns in the middle of `projects` (the Slack
settings), and sweep.replay copies rows with `SELECT *` (by position), so restoring the dump's own
schema made every replay fail; loading by column name leaves newer columns at their defaults.

A database that already exists is kept if every table's row count equals the dump's (counted from
the dump's own COPY data, so no state file is needed); if the counts differ it fails loudly rather
than overwrite: drop it yourself (`dropdb NAME`) and restore again. The dumps are never modified.
"""

import subprocess

import psycopg
from psycopg import sql

from tripwire.clusterer.sweep import MIGRATIONS

from .database import database_exists, libpq_env, maintenance_url, url_for
from .registry import LIVE_DB


class RestoreError(RuntimeError):
    pass


def dump_row_counts(dump_path):
    """{table: rows} from the dump's data section (`COPY schema.table (...) FROM stdin;` blocks)."""
    text = subprocess.run(["pg_restore", "--data-only", "-f", "-", str(dump_path)],
                          check=True, capture_output=True, text=True).stdout
    return count_copy_rows(text)


def count_copy_rows(text):
    counts, table = {}, None
    for line in text.splitlines():
        if table is None:
            if line.startswith("COPY ") and line.endswith("FROM stdin;"):
                table = line.split()[1].split(".")[-1].strip('"')
                counts[table] = 0
        elif line == "\\.":
            table = None
        else:
            counts[table] += 1
    return counts


def database_row_counts(conn, tables):
    return {table: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            for table in tables}


def restore_run(run, base=None, log=print):
    """Restore one run. Returns "restored" or "skipped" (already there with matching counts)."""
    if run.db == LIVE_DB:
        raise RestoreError(f"refusing to restore into the live database {LIVE_DB!r}")
    expected = dump_row_counts(run.dump)
    if database_exists(run.db, base):
        with psycopg.connect(url_for(run.db, base), autocommit=True) as conn:
            try:
                actual = database_row_counts(conn, expected)
            except psycopg.Error as error:
                raise RestoreError(f"{run.id}: database {run.db} exists but can't be checked ({error}); "
                                   f"drop it (dropdb {run.db}) and restore again") from error
        if actual != expected:
            differing = {table: (actual[table], expected[table]) for table in expected if actual[table] != expected[table]}
            raise RestoreError(f"{run.id}: database {run.db} exists but its row counts differ from the dump "
                               f"(table: (db, dump)) {differing}; drop it (dropdb {run.db}) and restore again")
        log(f"{run.id}: {run.db} already restored ({sum(expected.values())} rows match the dump), skipped")
        return "skipped"

    with psycopg.connect(maintenance_url(base), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(run.db)))
    try:
        target = url_for(run.db, base)
        with psycopg.connect(target, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(migration.read_text())
        result = subprocess.run(["pg_restore", "--data-only", "--no-owner", "--no-privileges",
                                 "--disable-triggers", "--exit-on-error", "-d", run.db, str(run.dump)],
                                env=libpq_env(target), capture_output=True, text=True)
        if result.returncode != 0:
            raise RestoreError(f"{run.id}: pg_restore failed:\n{result.stderr.strip()}")
        with psycopg.connect(target, autocommit=True) as conn:
            actual = database_row_counts(conn, expected)
        if actual != expected:
            raise RestoreError(f"{run.id}: restored row counts {actual} differ from the dump's {expected}")
    except BaseException:
        # Only a database this call created is dropped.
        with psycopg.connect(maintenance_url(base), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(run.db)))
        raise
    log(f"{run.id}: restored {run.dump} into {run.db} ({sum(expected.values())} rows)")
    return "restored"
