"""The clusterer's health checks (DESIGN.md Q12, decided Nov 11).

Two separate checks, because they need different responses:

  * "Is the clusterer alive?"  python -m tripwire.clusterer.health --alive
    For ECS. Every pass (even one whose steps failed, even one that couldn't reach the database)
    records its time in CLUSTERER_LAST_PASS_FILE; --alive fails when no pass has finished for
    CLUSTER_HEALTH_ALIVE_MINUTES. Only a frozen loop fails it, and a restart fixes that, so ECS
    restarting the task on failure is the right response. A file, not the database, so it works
    when the database is the problem.

  * "Is clustering working?"  python -m tripwire.clusterer.health
    Q12's two rules, for people (and Compose, which only displays health):
      1. a LIVE event has been unfinished for more than CLUSTER_HEALTH_STALL_MINUTES. Unfinished =
         not embedded (so neither clustered nor in the pool) and its summary hasn't failed.
         Backfill events don't count: they're prepared after live ones by design (#14).
      2. the last CLUSTER_HEALTH_FAILURE_RUN finished summaries all failed.
    A restart fixes none of their usual causes (OpenAI down, an expired key, a prepare bug, the
    database down, a backlog), so the loop logs them as warnings instead (log_line_prefix below; a
    CloudWatch alarm can watch for it on AWS, see infra/docs/BACKEND_INFRA_NOTES.md).

Exit code 0 = healthy, 1 = unhealthy (or the settings are invalid). The reason is printed.
"""

import argparse
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field

import psycopg
from dotenv import load_dotenv

from .settings import SettingsError, load_settings
from .sql import unfinished

log = logging.getLogger("clusterer")

# Every warning about Q12's rules starts with this, so a log alarm can match one fixed string.
UNHEALTHY_PREFIX = "clustering unhealthy:"

# Rule 1: the oldest live event that has waited too long and isn't finished.
STALLED_EVENT_SQL = f"""
SELECT sig_event.id, floor(extract(epoch FROM now() - sig_event.created_at) / 60)::int
FROM signal_events sig_event
JOIN signal_runs run ON run.id = sig_event.run_id
WHERE run.source = 'live'
  AND sig_event.created_at < now() - make_interval(mins => %s)
  AND {unfinished("sig_event.id")}
ORDER BY sig_event.created_at, sig_event.id
LIMIT 1
"""

# Rule 2: the most recently finished summaries, newest first.
RECENT_SUMMARIES_SQL = """
SELECT status FROM event_summaries
WHERE status IN ('done', 'failed')
ORDER BY finished_at DESC, event_id DESC
LIMIT %s
"""


@dataclass
class HealthReport:
    problems: list = field(default_factory=list)

    @property
    def healthy(self):
        return not self.problems

    def summary(self):
        return "healthy" if self.healthy else f"{UNHEALTHY_PREFIX} " + "; ".join(self.problems)


def check_clustering(conn, settings):
    """Q12's two rules against the database. Returns a HealthReport."""
    report = HealthReport()

    stalled = conn.execute(STALLED_EVENT_SQL, (settings.health_stall_minutes,)).fetchone()
    if stalled is not None:
        event_id, minutes = stalled
        report.problems.append(
            f"event {event_id} has waited {minutes} minutes to be clustered "
            f"(limit {settings.health_stall_minutes})"
        )

    statuses = [row[0] for row in conn.execute(RECENT_SUMMARIES_SQL, (settings.health_failure_run,))]
    if len(statuses) == settings.health_failure_run and all(status == "failed" for status in statuses):
        report.problems.append(f"the last {settings.health_failure_run} summaries all failed")

    return report


def record_pass(path, now=time.time):
    """Record that a pass just finished (for --alive). Written atomically: never a half-written file."""
    directory = os.path.dirname(os.path.abspath(path))
    with tempfile.NamedTemporaryFile("w", dir=directory, delete=False) as handle:
        handle.write(str(now()))
        temporary = handle.name
    os.replace(temporary, path)


def check_alive(path, max_age_minutes, now=time.time):
    """(alive, message): whether a pass finished within the last `max_age_minutes`."""
    try:
        with open(path) as handle:
            last_pass = float(handle.read().strip())
    except (FileNotFoundError, ValueError):
        return False, "no clusterer pass has finished yet"
    age_minutes = (now() - last_pass) / 60
    if age_minutes > max_age_minutes:
        return False, f"no clusterer pass for {age_minutes:.1f} minutes (limit {max_age_minutes:g})"
    return True, f"alive: last pass {age_minutes * 60:.0f} s ago"


class UnhealthyReporter:
    """Logs Q12 problems for people: once when they start, again every `repeat_seconds` while they
    last, and once when they clear, so the log doesn't repeat the same line every pass."""

    def __init__(self, repeat_seconds=300, clock=time.monotonic):
        self.repeat_seconds = repeat_seconds
        self.clock = clock
        self.last_warning = None    # clock time of the last warning; None = currently healthy

    def report(self, health_report):
        if health_report.healthy:
            if self.last_warning is not None:
                log.info("clustering healthy again")
                self.last_warning = None
            return
        now = self.clock()
        if self.last_warning is None or now - self.last_warning >= self.repeat_seconds:
            log.warning("%s", health_report.summary())
            self.last_warning = now


def main(argv=None):
    parser = argparse.ArgumentParser(description="Clusterer health check (exit 0 = healthy).")
    parser.add_argument("--alive", action="store_true",
                        help="only check that the loop is running (for ECS); no database needed")
    arguments = parser.parse_args(argv)

    load_dotenv()
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"unhealthy: {error}")
        return 1

    if arguments.alive:
        alive, message = check_alive(settings.last_pass_file, settings.health_alive_minutes)
        print(message if alive else f"unhealthy: {message}")
        return 0 if alive else 1

    try:
        with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5) as conn:
            report = check_clustering(conn, settings)
    except psycopg.OperationalError as error:
        print(f"{UNHEALTHY_PREFIX} can't reach the database ({str(error).strip().splitlines()[0]})")
        return 1
    print(report.summary())
    return 0 if report.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
