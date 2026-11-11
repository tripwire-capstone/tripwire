"""Poll Laminar for every project and write new traces to the `traces` table.

    python -m tripwire.poller            # poll every 10 s until stopped
    python -m tripwire.poller --once     # one poll of each project, then exit

Settings come from env vars (or .env): DATABASE_URL, LMNR_PROJECT_API_KEY.
Each project's Laminar address comes from its row in `projects`.

TODO (encrypted keys): every project uses LMNR_PROJECT_API_KEY for now. Next step:
decrypt projects.laminar_api_key_encrypted with TRIPWIRE_SECRET_KEY instead.
"""

import argparse
import logging
import os
import signal
import time
from datetime import timedelta

import psycopg
from dotenv import load_dotenv

from .laminar import LaminarError, LaminarSQL
from .poll import poll_once

log = logging.getLogger("poller")


def _stop(signum, frame):
    # docker stop / ECS sends SIGTERM: stop like Ctrl-C. A poll that's cut off wrote
    # nothing (its insert is one transaction), so nothing is lost.
    raise KeyboardInterrupt


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--once", action="store_true", help="poll each project once and exit")
    p.add_argument("--interval", type=float, default=float(os.environ.get("POLL_INTERVAL_SECONDS", 10)),
                   help="seconds between polls (default 10)")
    p.add_argument("--settle-seconds", type=float,
                   default=float(os.environ.get("POLL_SETTLE_SECONDS", 15)),
                   help="only take traces that finished at least this long ago (default 15)")
    p.add_argument("--lookback-hours", type=float,
                   default=float(os.environ.get("POLL_FIRST_RUN_LOOKBACK_HOURS", 24)),
                   help="how far back a project's first poll looks (default 24)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)

    database_url = os.environ["DATABASE_URL"]
    api_key = os.environ["LMNR_PROJECT_API_KEY"]
    lookback = timedelta(hours=args.lookback_hours)
    settle = timedelta(seconds=args.settle_seconds)

    try:
        while True:
            try:
                # A fresh connection per poll: nothing goes stale between polls.
                with psycopg.connect(database_url) as conn:
                    projects = conn.execute(
                        "SELECT id, name, laminar_api_url FROM projects ORDER BY id").fetchall()
                    if not projects:
                        log.warning("no rows in projects: run the seed first")
                    for project_id, name, api_url in projects:
                        poll_project(conn, project_id, name, api_url, api_key, lookback, settle)
            except psycopg.OperationalError as e:
                log.error("can't reach Postgres, will retry: %s", e)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopped")


def poll_project(conn, project_id, name, api_url, api_key, lookback, settle):
    """Poll one project. A failure here is logged and doesn't stop the other projects."""
    laminar = LaminarSQL(api_key=api_key, api_url=api_url)
    try:
        r = poll_once(laminar, conn, project_id, first_run_lookback=lookback, settle=settle)
        log.info("project %s: since %s, %d root spans, %d already stored, %d written, %d runs created",
        name, r.since.isoformat(timespec="seconds"), r.seen, r.already_stored, r.written,
        r.runs_created)
    except LaminarError as e:
        # Laminar unreachable or the key was refused: nothing was written, the marker
        # didn't move, and the next poll tries again.
        log.error("project %s: poll failed, will retry: %s", name, e)
    finally:
        laminar.close()


if __name__ == "__main__":
    main()
