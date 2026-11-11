"""One pass of the real clusterer: prepare -> assign -> group -> name -> centers (DESIGN.md Q13).

Each step stands alone (decided Nov 11): if a step fails, the error is logged and the pass carries
on with the remaining steps; the failed step is tried again next pass. This is safe because the
steps communicate through the tables, not by handing work to each other: each step re-reads the
tables and finds its own work, so a skipped step leaves its work waiting, not broken. The one
hand-off in memory is assign -> centers (which clusters gained members); if assign fails, the
center step has nothing to do.

Every step saves its own work, so the connection must be in autocommit mode (open_connection()).
Otherwise the first query opens an implicit transaction, each step's save becomes a savepoint
inside it, and nothing commits until the pass ends.
"""

import logging
from dataclasses import dataclass, field

import psycopg

from .assign import assign_pool
from .naming import name_new_clusters, update_centers
from .pool import group_pool

log = logging.getLogger("clusterer")


def prepare_stand_in(conn, limit):
    """Stand-in for the prepare half's `prepare_events(conn, limit)` until it lands: does nothing.

    The real one summarizes and embeds up to `limit` new events, live before backfill
    (HANDOFF_PREPARE.md PR 4). When it merges, replace this body with a call to it.
    """
    return {}


@dataclass
class PassResult:
    prepared: object = None          # whatever prepare_events returns (its counts)
    joined: int = 0                  # events that joined an existing cluster (step 2)
    created: int = 0                 # new clusters (step 3)
    named: int = 0                   # clusters named (step 4)
    centers_updated: int = 0         # clusters whose center was recomputed (step 5)
    failed_steps: list = field(default_factory=list)

    def anything_happened(self):
        return bool(self.joined or self.created or self.named or self.centers_updated
                    or self.prepared or self.failed_steps)


def open_connection(database_url):
    """A connection where each step's transaction commits on its own."""
    return psycopg.connect(database_url, autocommit=True)


def run_pass(conn, settings, prepare=prepare_stand_in):
    """Run steps 1-5 once. Returns a PassResult; never raises for a failing step."""
    result = PassResult()

    def attempt(step_name, function):
        try:
            return function()
        except Exception:
            log.exception("clusterer step %r failed; the rest of the pass carries on", step_name)
            result.failed_steps.append(step_name)
            return None

    result.prepared = attempt("prepare", lambda: prepare(conn, settings.prepare_batch))

    joins = attempt("assign", lambda: assign_pool(conn, settings.assign_threshold)) or []
    result.joined = len(joins)

    created = attempt("group", lambda: group_pool(
        conn,
        settings.assign_threshold,      # the same threshold as assign (Q8)
        settings.min_events,
        settings.min_conversations,
        settings.pool_window_days,
    ))
    result.created = created or 0

    result.named = attempt("name", lambda: name_new_clusters(conn)) or 0

    touched = sorted({join.cluster_id for join in joins})   # empty if assign failed
    attempt("centers", lambda: update_centers(conn, touched))
    if "centers" not in result.failed_steps:
        result.centers_updated = len(touched)

    return result
