"""Signal runner: python -m tripwire.runner

Worker threads claim pending runs, judge them and record the outcome; the main thread
sweeps runs that can never finish. Stops cleanly on Ctrl-C or SIGTERM: threads finish
their current run, and any run they can't finish is picked up again when its lease expires.

Settings (env or .env): DATABASE_URL, TRIPWIRE_SECRET_KEY (for keys saved on the Settings page),
and the RUNNER_* / JUDGE_* variables in settings.py. Each run uses its project's Laminar and OpenAI
keys, looked up per run: saved on the Settings page, else LMNR_PROJECT_API_KEY / OPENAI_API_KEY.
A run with a key missing waits and retries without using an attempt.
"""

import logging
import os
import signal
import threading

import psycopg
from dotenv import load_dotenv

from ..project_keys import KeyWatch
from . import jobs
from .settings import Settings
from .worker import worker_loop

log = logging.getLogger("runner")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    settings = Settings.from_env()
    settings.check()
    database_url = os.environ["DATABASE_URL"]
    watch = KeyWatch(log)   # shared by the threads, so a missing key is logged once, not per thread

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    threads = [threading.Thread(target=worker_loop, name=f"worker-{i}",
                                args=(f"worker-{i}", database_url, settings, stop, watch))
               for i in range(settings.threads)]
    for t in threads:
        t.start()
    log.info("runner started: %d threads, lease %ds", settings.threads, settings.lease_seconds)

    while not stop.wait(settings.sweep_seconds):
        try:
            with psycopg.connect(database_url, autocommit=True) as conn:
                expired, gave_up = jobs.sweep(conn, settings)
            if expired or gave_up:
                log.info("sweep: %d expired on their last attempt, %d given up", expired, gave_up)
        except Exception:
            log.exception("sweep failed, will retry")

    for t in threads:
        t.join(timeout=settings.lease_seconds)
    log.info("stopped")


if __name__ == "__main__":
    main()
    