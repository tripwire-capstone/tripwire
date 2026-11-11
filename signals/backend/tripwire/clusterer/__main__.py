"""The clusterer: python -m tripwire.clusterer

Two modes, chosen by CLUSTERER_MODE (decided Nov 11):
  * placeholder (default): the skeleton's stand-in. Gives every new event a fixed embedding and
    links some to the seeded cluster, so data keeps flowing end to end.
  * real: steps 1-5 (prepare, assign, group, name, centers) every CLUSTERER_INTERVAL_SECONDS.
    Prepare is a do-nothing stand-in until the prepare half's prepare_events() lands, so until
    then real mode only clusters events that already have embeddings.

Every setting is read and checked once at startup (settings.py); a bad value stops the clusterer
with a message saying what's wrong. Stops cleanly on Ctrl-C or SIGTERM (docker stop / ECS).
"""

import logging
import sys

import psycopg
from dotenv import load_dotenv

from ..placeholder import run_forever
from .cluster import cluster_step
from .loop import open_connection, run_pass
from .settings import SettingsError, load_settings

log = logging.getLogger("clusterer")


def placeholder_pass(settings):
    with psycopg.connect(settings.database_url) as conn:
        embedded, linked = cluster_step(conn, settings.stub_cluster_rate)
    if embedded:
        log.info("placeholder clusterer: %d embedded, %d linked to a cluster, %d left in the pool",
                 embedded, linked, embedded - linked)


def real_pass(settings):
    # A fresh connection each pass, so a dropped connection heals on the next pass.
    with open_connection(settings.database_url) as conn:
        result = run_pass(conn, settings)
    if result.anything_happened():
        log.info("clusterer pass: %d joined, %d new clusters, %d named, %d centers updated%s",
                 result.joined, result.created, result.named, result.centers_updated,
                 f"; failed steps: {', '.join(result.failed_steps)}" if result.failed_steps else "")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings()
    except SettingsError as error:
        log.error("%s", error)
        sys.exit(1)

    log.info("clusterer starting in %s mode (threshold %.2f, every %gs)",
             settings.mode, settings.assign_threshold, settings.interval_seconds)
    one_pass = real_pass if settings.mode == "real" else placeholder_pass
    run_forever(lambda: one_pass(settings), settings.interval_seconds, "clusterer")


if __name__ == "__main__":
    main()
