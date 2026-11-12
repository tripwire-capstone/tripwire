"""One runner thread: claim a run, judge it, record the outcome; repeat until stopped."""

import logging
import random
from datetime import datetime, timezone

import psycopg

from ..judge.judge import judge
from ..judge.laminar import JudgeLaminarSQL
from ..judge.types import JudgeError
from . import jobs

log = logging.getLogger("runner")


class LaminarClients:
    """Create a fresh Laminar client for each judgment. The judge closes it."""

    def __init__(self, api_key):
        self.api_key = api_key

    def get(self, api_url):
        return JudgeLaminarSQL(self.api_key, api_url)


def backoff_seconds(retry_after, age_seconds, settings, rand=random.random):
    """How long a run waits before its next try. The provider's retry-after wins; otherwise the
    wait grows with the run's age, with jitter so stuck runs don't all retry at once."""
    if retry_after is not None:
        return min(retry_after, settings.max_backoff_seconds)
    base = min(settings.max_backoff_seconds, 5 + age_seconds / 2)
    return min(settings.max_backoff_seconds, base * (0.75 + rand() / 2))


def work_once(conn, settings, clients, judge_fn=judge):
    """Claim and process one run. Returns False when there was nothing to claim."""
    claimed = jobs.claim(conn, settings)
    if claimed is None:
        return False
    trace, version, api_url = jobs.load(conn, claimed.run_id)
    try:
        result = judge_fn(clients.get(api_url), trace, version)
    except JudgeError as e:
        error = e
    except Exception as e:
        # A bug in the judge: record it like bad output so the run never sticks in `running`.
        log.exception("run %s: unexpected error from the judge", claimed.run_id)
        error = JudgeError(f"unexpected error: {e!r}", retryable=True, uses_attempt=True)
    else:
        if jobs.record_success(conn, claimed, trace.id, version.id, result):
            log.info("run %s: done, %d findings", claimed.run_id, len(result.findings))
        else:
            log.warning("run %s: lease lost before finishing; result discarded", claimed.run_id)
        return True
    record_error(conn, claimed, error, settings)
    return True


def record_error(conn, claimed, error, settings):
    final = not error.retryable or (error.uses_attempt and claimed.attempts >= settings.max_attempts)
    if final:
        ok = jobs.record_failure(conn, claimed, error)
        outcome = "failed"
    else:
        age = (datetime.now(timezone.utc) - claimed.created_at).total_seconds()
        wait = backoff_seconds(error.retry_after, age, settings)
        ok = jobs.record_retry(conn, claimed, error, wait)
        outcome = f"retry in {wait:.0f}s"
    if ok:
        log.info("run %s: %s (%s)", claimed.run_id, outcome, error)
    else:
        log.warning("run %s: lease lost before recording an error; discarded", claimed.run_id)


def worker_loop(name, database_url, api_key, settings, stop, judge_fn=judge):
    """Keep working until `stop` is set. A lost database connection is retried, never fatal."""
    clients = LaminarClients(api_key)
    while not stop.is_set():
        try:
            with psycopg.connect(database_url, autocommit=True) as conn:
                while not stop.is_set():
                    if not work_once(conn, settings, clients, judge_fn):
                        stop.wait(settings.idle_seconds)
        except Exception:
            log.exception("%s: error, reconnecting in 5 s", name)
            stop.wait(5)
