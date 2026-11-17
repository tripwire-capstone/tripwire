"""Step 4 with an LLM: name new clusters, and rename clusters that have grown (SIG-65, SIG-66).

Decisions: "Signals Prototype: Design Decisions (Phase 2)", section "Cluster quality".
Runs instead of naming.name_new_clusters unless NAMING_MODEL=off; naming.py is untouched and
still the fallback: when the LLM can't name a NEW cluster, it gets exactly the name naming.py
would have given it (the summary of its member closest to the center).

The pieces, in call order (one pass of step 4):

  find_work       two reads: unnamed clusters (to name) and named clusters with their sizes;
                  renames_due (pure) picks which named ones have grown enough to rename
  ... (load_input, pick_sample, build_messages, llm.complete, clean_name, save: next pieces)

No LLM call happens inside a database transaction: every read ends before the call, and each
write is its own short transaction that checks nothing changed meanwhile.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from .naming import NAME_CANDIDATES_SQL
from .sql import leaf_cluster

log = logging.getLogger("clusterer.naming")


@dataclass(frozen=True)
class Job:
    """One cluster to name or rename this pass."""
    cluster_id: int
    signal_id: int
    kind: str                         # "new" (no name yet) or "rename" (grown since last named)
    fallback: str | None = None       # new only: the closest member's summary (naming.py's name)
    current_name: str | None = None   # rename only
    current_source: str | None = None  # rename only: 'llm', 'fallback', or None (old step 4)
    named_at: datetime | None = None  # rename only: the write checks it hasn't changed


# Every named leaf cluster with its size: how many members, and how many have a finished summary.
# Which of them are due for a rename is decided in Python (renames_due), not here.
NAMED_CLUSTERS_SQL = f"""
SELECT named.id, named.signal_id, named.name, named.name_source, named.named_at,
       named.named_member_count,
       count(*) AS members,
       count(summary.event_id) AS summarized
FROM clusters named
JOIN cluster_members member ON member.cluster_id = named.id
LEFT JOIN event_summaries summary ON summary.event_id = member.event_id AND summary.status = 'done'
WHERE named.name IS NOT NULL AND {leaf_cluster("named")} AND named.centroid IS NOT NULL
GROUP BY named.id
"""


@dataclass(frozen=True)
class NamedCluster:
    """One row of NAMED_CLUSTERS_SQL."""
    cluster_id: int
    signal_id: int
    name: str
    source: str | None                # 'llm', 'fallback', or None (named by the old step 4)
    named_at: datetime | None
    named_member_count: int | None    # None = never named by llm_naming
    members: int
    summarized: int


def rename_is_due(cluster, growth):
    """Never named by llm_naming, or grown to `growth` x its size when last named (rounded up).
    Decimal, not float: 1.1 x 50 is exactly 55, where the float 55.00000000000001 would wait for 56."""
    if cluster.named_member_count is None:
        return True
    threshold = math.ceil(Decimal(str(growth)) * cluster.named_member_count)
    return cluster.members >= threshold


def renames_due(clusters, growth, max_renames):
    """The clusters to rename this pass, at most `max_renames`, most overdue first:
    old-step names first (one member's sentence: the worst names we have), then by how much the
    cluster has grown since named (x4 before x2), then the older cluster.
    A cluster with no summarized member is skipped: there'd be nothing to show the LLM, and it
    would take a slot every pass (placeholder mode, test data)."""
    due = [cluster for cluster in clusters
           if cluster.summarized > 0 and rename_is_due(cluster, growth)]

    def most_overdue_first(cluster):
        if cluster.named_member_count is None:
            return (0, 0, cluster.cluster_id)
        growth_so_far = Fraction(cluster.members, cluster.named_member_count)
        return (1, -growth_so_far, cluster.cluster_id)

    return sorted(due, key=most_overdue_first)[:max_renames]


def find_work(conn, growth, max_renames):
    """This pass's jobs: every unnamed cluster that can be named, then up to `max_renames`
    renames. New clusters come in ID order, so when two want the same name in one Signal the
    older one gets it plain (as naming.py does)."""
    new = [Job(cluster_id, signal_id, "new", fallback=summary)
           for cluster_id, signal_id, summary in conn.execute(NAME_CANDIDATES_SQL).fetchall()]
    named = [NamedCluster(*row) for row in conn.execute(NAMED_CLUSTERS_SQL).fetchall()]
    renames = [Job(cluster.cluster_id, cluster.signal_id, "rename", current_name=cluster.name,
                   current_source=cluster.source, named_at=cluster.named_at)
               for cluster in renames_due(named, growth, max_renames)]
    return new + renames
