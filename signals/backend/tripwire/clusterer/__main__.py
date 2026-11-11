"""The clusterer: python -m tripwire.clusterer

Two modes, chosen by CLUSTERER_MODE (decided Nov 11):
  * placeholder (default): the skeleton's stand-in. Gives every new event a fixed embedding and
    links some to the seeded cluster, so data keeps flowing end to end.
  * real: steps 1-5 (prepare, assign, group, name, centers) every CLUSTERER_INTERVAL_SECONDS.
    Prepare is a do-nothing stand-in until the prepare half's prepare_events() lands, so until
    then real mode only clusters events that already have embeddings.

Every setting is read and checked once at startup (settings.py); a bad value stops the clusterer
with a message saying what's wrong. Stops cleanly on Ctrl-C or SIGTERM (docker stop / ECS).

Health (health.py): every pass records its time for `--alive` (ECS), and Q12's rules are checked
after each pass and logged as warnings for people (once when they start, every few minutes while they
last, once when they clear).
"""

import logging
import sys

import psycopg
from dotenv import load_dotenv

from ..placeholder import run_forever
from .cluster import cluster_step
from .health import UnhealthyReporter, check_clustering, record_pass
from .loop import open_connection, run_pass
from .settings import SettingsError, load_settings

log = logging.getLogger("clusterer")


def report_health(conn, settings, reporter):
    """Check Q12's rules after a pass and log them for people. Never breaks the pass."""
    try:
        reporter.report(check_clustering(conn, settings))
    except Exception:
        log.exception("health check failed; the clusterer carries on")


def placeholder_pass(settings, reporter):
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        embedded, linked = cluster_step(conn, settings.stub_cluster_rate)
        report_health(conn, settings, reporter)
    if embedded:
        log.info("placeholder clusterer: %d embedded, %d linked to a cluster, %d left in the pool",
                 embedded, linked, embedded - linked)


def real_pass(settings, reporter):
    # A fresh connection each pass, so a dropped connection heals on the next pass.
    with open_connection(settings.database_url) as conn:
        result = run_pass(conn, settings)
        report_health(conn, settings, reporter)
    if result.anything_happened():
        log.info("clusterer pass: %d joined, %d new clusters, %d named, %d centers updated%s",
                 result.joined, result.created, result.named, result.centers_updated,
                 f"; failed steps: {', '.join(result.failed_steps)}" if result.failed_steps else "")


def tick(one_pass, settings, reporter):
    """One pass, then record that it finished, even if it failed (e.g. couldn't reach the
    database). --alive only fails when passes stop finishing at all: a frozen loop."""
    try:
        one_pass(settings, reporter)
    finally:
        try:
            record_pass(settings.last_pass_file)
        except OSError:
            log.exception("couldn't record the pass time in %s", settings.last_pass_file)


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
    reporter = UnhealthyReporter()
    run_forever(lambda: tick(one_pass, settings, reporter), settings.interval_seconds, "clusterer")


if __name__ == "__main__":
    main()
