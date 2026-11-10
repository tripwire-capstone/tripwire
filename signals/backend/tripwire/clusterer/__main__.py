"""PLACEHOLDER clusterer: python -m tripwire.clusterer

Settings (env or .env): DATABASE_URL; CLUSTERER_INTERVAL_SECONDS (default 10);
STUB_CLUSTER_RATE (share of events linked to the existing cluster, default 0.5).
"""

import logging
import os

import psycopg
from dotenv import load_dotenv

from ..placeholder import run_forever
from .cluster import cluster_step

log = logging.getLogger("clusterer")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database_url = os.environ["DATABASE_URL"]
    rate = float(os.environ.get("STUB_CLUSTER_RATE", 0.5))

    def step():
        with psycopg.connect(database_url) as conn:
            embedded, linked = cluster_step(conn, rate)
        if embedded:
            log.info("placeholder clusterer: %d embedded, %d linked to a cluster, %d left in the pool",
                     embedded, linked, embedded - linked)

    run_forever(step, float(os.environ.get("CLUSTERER_INTERVAL_SECONDS", 10)), "clusterer")


if __name__ == "__main__":
    main()
