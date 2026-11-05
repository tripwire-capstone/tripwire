"""Poll Laminar and write new turns to the `turns` table.

From the repo root:
    python -m sync.run            # poll every 10 s until Ctrl-C
    python -m sync.run --once     # one poll, then exit

Settings come from .env in the repo root (see .env.example).
"""

import argparse
import logging
import os
import time
from datetime import timedelta

import psycopg
from dotenv import load_dotenv

from .laminar import LaminarError, LaminarSQL
from .poll import poll_once

log = logging.getLogger("sync")


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--once", action="store_true", help="run one poll and exit")
    p.add_argument("--interval", type=float, default=float(os.environ.get("SYNC_INTERVAL_SECONDS", 10)),
                   help="seconds between polls (default 10)")
    p.add_argument("--settle-seconds", type=float,
                   default=float(os.environ.get("SYNC_SETTLE_SECONDS", 15)),
                   help="only take turns that finished at least this long ago (default 15)")
    p.add_argument("--lookback-hours", type=float,
                   default=float(os.environ.get("SYNC_FIRST_RUN_LOOKBACK_HOURS", 24)),
                   help="how far back the first poll looks when `turns` has no live rows")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    laminar = LaminarSQL(
        api_key=os.environ["LMNR_PROJECT_API_KEY"],
        base_url=os.environ.get("LMNR_BASE_URL", "http://localhost"),
        port=int(os.environ.get("LMNR_HTTP_PORT", "8000")),
    )
    # Empty means: take everything from PGUSER, PGPASSWORD, PGHOST, PGPORT, PGDATABASE.
    database_url = os.environ.get("DATABASE_URL", "")
    lookback = timedelta(hours=args.lookback_hours)

    try:
        while True:
            try:
                # A fresh connection per poll: nothing goes stale between polls.
                with psycopg.connect(database_url) as conn:
                    r = poll_once(laminar, conn, first_run_lookback=lookback,
                                  settle=timedelta(seconds=args.settle_seconds))
                log.info("poll since %s: %d root spans, %d already stored, %d crashed, "
                         "%d failed, %d written", r.since.isoformat(timespec="seconds"),
                         r.seen, r.already_stored, r.crashed, r.failed, r.written)
            except (LaminarError, psycopg.OperationalError) as e:
                # Laminar or Postgres unreachable: try again next poll. Nothing was
                # written, so the marker didn't move and nothing is lost.
                log.error("poll failed, will retry: %s", e)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        laminar.close()


if __name__ == "__main__":
    main()
