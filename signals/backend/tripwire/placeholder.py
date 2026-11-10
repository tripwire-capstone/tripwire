"""Helpers shared by the placeholder runner and clusterer (replaced by real logic later).

picked() decides "yes" for about `rate` of all keys, the same way every time for the
same key. It's the sampling hash from CONTRACT.md, so a rerun or a restart makes the
same choices, and tests can predict them.
"""

import hashlib
import logging
import signal
import time

FIXED_EMBEDDING = [0.1] * 1536  # not all zeros: cosine distance to a zero vector is undefined


def picked(key, rate):
    h = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    return h / 2**64 < rate


def vector_text(values):
    """Python list -> pgvector input text, e.g. '[0.1,0.1,...]'."""
    return "[" + ",".join(str(v) for v in values) + "]"


def run_forever(step, interval, name):
    """Call step() every `interval` seconds until stopped (Ctrl-C or SIGTERM).

    A step that raises is logged and the loop keeps going: a database hiccup
    shouldn't kill the worker. Each step commits its own work, so stopping between
    or during steps loses nothing.
    """
    log = logging.getLogger(name)

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        while True:
            try:
                step()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("step failed, will retry")
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("stopped")
