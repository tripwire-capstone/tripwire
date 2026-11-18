"""Run one function over a few items at once and wait for all of them (SIG-111).

prepare.py uses it for the summarizer's LLM calls, a few events at a time. It knows nothing about
the LLM or the database: the function must be safe to run on several threads at once (the
summarizer's call is; a psycopg connection is not, so database work stays on the caller's thread).

When run_in_parallel returns, every call has finished: nothing is left running in the background.
The notifier relies on that order (an event's embedding is saved, then assign places it, in the
same pass).
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass(frozen=True)
class CallResult:
    outcome: object                # what the function returned (None if it raised)
    error: Exception | None        # what it raised (a bug in our code), else None
    seconds: float                 # how long the call took


def run_in_parallel(function, items, threads, clock=time.monotonic):
    """Call function(item) for every item, up to `threads` at a time, and wait for all of them.

    Returns one CallResult per item, in the same order as `items` (not the order they finished).
    An exception in one call is returned in its CallResult, never raised, and doesn't affect
    the others. (KeyboardInterrupt and other BaseExceptions aren't caught.)
    """
    if threads < 1:
        raise ValueError(f"threads must be at least 1, got {threads}")

    def timed(item):
        started = clock()
        try:
            outcome, error = function(item), None
        except Exception as raised:
            outcome, error = None, raised
        return CallResult(outcome, error, clock() - started)

    with ThreadPoolExecutor(max_workers=threads) as pool:   # the `with` waits for every call
        futures = [pool.submit(timed, item) for item in items]
    return [future.result() for future in futures]
