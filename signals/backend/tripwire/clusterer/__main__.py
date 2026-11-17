"""The clusterer: python -m tripwire.clusterer

Two modes, chosen by CLUSTERER_MODE (decided Nov 11):
  * real (default since the prepare half landed): steps 1-5 (prepare, assign, group, name,
    centers) every CLUSTERER_INTERVAL_SECONDS. Needs OPENAI_API_KEY (summarizer, embedder and,
    unless NAMING_MODEL=off, cluster naming).
  * placeholder: the skeleton's stand-in, no OpenAI. Gives every new event a fixed embedding and
    links some to the seeded cluster, so data keeps flowing end to end.

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
from .embed import OpenAIEmbedder
from .llm_client import OpenAIClient
from .llm_naming import Namer
from .loop import open_connection, prepare_stand_in, run_pass
from .naming import name_new_clusters
from .prepare import Preparer
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


def real_pass(settings, reporter, prepare=prepare_stand_in, name=None):
    # A fresh connection each pass, so a dropped connection heals on the next pass.
    with open_connection(settings.database_url) as conn:
        result = run_pass(conn, settings, prepare=prepare, name=name)
        report_health(conn, settings, reporter)
    if result.anything_happened():
        log.info("clusterer pass: prepared %s, %d joined, %d new clusters, %d named, "
                 "%d centers updated%s", result.prepared or "nothing",
                 result.joined, result.created, result.named, result.centers_updated,
                 f"; failed steps: {', '.join(result.failed_steps)}" if result.failed_steps else "")


def naming_step(settings, llm):
    """Step 4: LLM names (llm_naming.Namer, SIG-65), or naming.py's closest-member summary
    when NAMING_MODEL=off."""
    if settings.naming_model is None:
        log.info("cluster naming: off (NAMING_MODEL=off): clusters get their closest member's summary")
        return name_new_clusters
    log.info("cluster naming: %s, renamed at %gx growth, at most %d renames per pass",
             settings.naming_model, settings.naming_rename_growth,
             settings.naming_max_renames_per_pass)
    return Namer(llm, settings.naming_model, settings.naming_rename_growth,
                 settings.naming_max_renames_per_pass)


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
    if settings.mode == "real":
        # Built once: they keep the OpenAI clients (and the preparer its skip list) between
        # passes (Q21). The summarizer and the namer share one client.
        llm = OpenAIClient(settings.openai_api_key)
        preparer = Preparer(llm, OpenAIEmbedder(settings.openai_api_key),
                            settings.summarizer_model, settings.summarizer_prompt_version)
        name = naming_step(settings, llm)

        def one_pass(settings_used, reporter_used):
            real_pass(settings_used, reporter_used, prepare=preparer.prepare_events, name=name)
    else:
        one_pass = placeholder_pass
    reporter = UnhealthyReporter()
    run_forever(lambda: tick(one_pass, settings, reporter), settings.interval_seconds, "clusterer")


if __name__ == "__main__":
    main()
