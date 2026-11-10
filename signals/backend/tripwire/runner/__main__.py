"""PLACEHOLDER signal runner: python -m tripwire.runner

Settings (env or .env): DATABASE_URL; RUNNER_INTERVAL_SECONDS (default 5);
STUB_EVENT_RATE (share of traces that get an event, default 0.5).
"""

import logging
import os

import psycopg
from dotenv import load_dotenv

from ..placeholder import run_forever
from .run import run_step

log = logging.getLogger("runner")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database_url = os.environ["DATABASE_URL"]
    rate = float(os.environ.get("STUB_EVENT_RATE", 0.5))

    def step():
        with psycopg.connect(database_url) as conn:
            runs, events = run_step(conn, rate)
        if runs:
            log.info("placeholder judge: %d runs, %d events", runs, events)

    run_forever(step, float(os.environ.get("RUNNER_INTERVAL_SECONDS", 5)), "runner")


if __name__ == "__main__":
    main()
