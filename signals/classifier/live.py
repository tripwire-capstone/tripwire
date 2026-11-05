"""Score live turns (synced from Laminar) that have no result for the current question version.

From the repo root:
    python -m classifier.live            # a pass every 10 s until Ctrl-C
    python -m classifier.live --once     # one pass, then exit
"""

import argparse
import logging
import time
from dataclasses import dataclass

import openai
import psycopg

from classifier.config import QUESTION_VERSION, SIGNAL
from classifier.frustration import classify
from eval.storage import connect_database, save_probability

log = logging.getLogger("classifier.live")

# The database is the queue: a live turn needs work while it has no result for this
# signal and question version. Turns that already failed in this run are left out.
UNSCORED_LIVE_SQL = """
SELECT t.id, t.item
FROM turns AS t
LEFT JOIN results AS r
    ON r.turn_id = t.id AND r.signal = %s AND r.question_version = %s
WHERE t.source = 'live'
  AND r.turn_id IS NULL
  AND NOT t.id = ANY(%s)
ORDER BY t.turn_ended_at, t.id
LIMIT %s
"""

# The classifier service is briefly unavailable: stop this pass and try again next pass.
TEMPORARY_ERRORS = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)

# Something about this one turn: skip it for the rest of the run, keep going.
# RuntimeError = classify() got a non-probability answer (e.g. a refusal);
# ValueError = save_probability() rejected the probability.
TURN_ERRORS = (openai.BadRequestError, openai.UnprocessableEntityError, RuntimeError, ValueError)

# Anything else (missing or invalid API key, unknown model, a bug) is not caught:
# the process stops, so a broken setup never looks like "nothing to classify".


@dataclass
class PassResult:
    found: int = 0          # unscored live turns this pass looked at
    scored: int = 0
    failed: int = 0         # turn errors this pass; skipped until the process restarts
    stopped_early: bool = False


def classify_pending(connection, skipped_ids, *, limit=50, classify_item=classify):
    """One pass: score up to `limit` unscored live turns. Adds failing turn ids to `skipped_ids`."""
    rows = connection.execute(
        UNSCORED_LIVE_SQL, (SIGNAL, QUESTION_VERSION, sorted(skipped_ids), limit)
    ).fetchall()
    result = PassResult(found=len(rows))
    for row in rows:
        try:
            probability = classify_item(row["item"])
            # Commits right away (autocommit), so a crash keeps every score saved so far.
            save_probability(connection, row["id"], probability)
        except TEMPORARY_ERRORS as e:
            log.warning("classifier unavailable, will retry next pass: %s", e)
            result.stopped_early = True
            break
        except TURN_ERRORS as e:
            skipped_ids.add(row["id"])
            result.failed += 1
            log.error("skipping turn %s until restart: %s: %s", row["id"], type(e).__name__, e)
        else:
            result.scored += 1
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--once", action="store_true", help="run one pass and exit")
    p.add_argument("--interval", type=float, default=10, help="seconds between passes (default 10)")
    p.add_argument("--limit", type=int, default=50, help="most turns scored per pass (default 50)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("scoring live turns for %s %s", SIGNAL, QUESTION_VERSION)

    skipped_ids = set()
    try:
        while True:
            try:
                # A fresh connection per pass, like sync: nothing goes stale between passes.
                with connect_database() as connection:
                    r = classify_pending(connection, skipped_ids, limit=args.limit)
                if r.found or args.once:
                    log.info("pass: %d unscored, %d scored, %d failed; %d skipped until restart",
                             r.found, r.scored, r.failed, len(skipped_ids))
            except psycopg.OperationalError as e:
                # Postgres unreachable: nothing was half-written, so just try again.
                log.error("pass failed, will retry: %s", e)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
