"""Step 3 of a clusterer pass: turn groups of similar pool events into new clusters.

Decisions (docs/clustering/DESIGN.md):
  * Q8: group pool events with average linkage at the same threshold as assign (Q7). A group
    becomes a cluster with at least CLUSTER_MIN_EVENTS events from at least
    CLUSTER_MIN_CONVERSATIONS conversations. Conversation = traces.session_id; a trace with no
    session ID is its own conversation.
  * Q9: only pool events created in the last CLUSTER_POOL_WINDOW_DAYS can form clusters
    (measured from when the event was created, so late and backfilled events still count).
  * Q6: clusters belong to a Signal, so each Signal's pool is grouped on its own.

Groups that don't qualify stay in the pool and are tried again next pass. The pool is a query
(embedded, no cluster_members row), so nothing here needs a status column.

Implementation choices (decided Nov 10, reasons in the PR):
  * The merging uses SciPy's standard average linkage, replacing the prototype's hand-written
    version: real pools can reach thousands of events.
  * A new cluster gets its center (the average of its members) when it's created, so the next
    pass's assign can use it straight away. The center comes from naming.update_centers(), the
    same function step 5 uses (Nov 11), so the formula is written once.
  * One transaction per Signal. An error in one Signal is logged and the others carry on, so a
    Signal with bad data can't stop every other Signal from getting new clusters.
"""

import json
import logging
from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage

from .naming import update_centers

log = logging.getLogger("clusterer")


@dataclass(frozen=True)
class PoolEvent:
    event_id: int
    signal_id: int
    conversation: tuple  # ("session", session_id) or ("trace", trace_id) when there's no session
    embedding: np.ndarray


# Pool events inside the window, with their Signal and conversation. Ordered so every pass
# sees the same events in the same order.
POOL_SQL = """
SELECT pool_event.event_id, sig_version.signal_id, trace.session_id, trace.id,
       pool_event.embedding::text
FROM event_embeddings pool_event
JOIN signal_events sig_event ON sig_event.id = pool_event.event_id
JOIN signal_versions sig_version ON sig_version.id = sig_event.signal_version_id
JOIN traces trace ON trace.id = sig_event.trace_id
WHERE NOT EXISTS (
    SELECT 1 FROM cluster_members member WHERE member.event_id = pool_event.event_id
)
  AND sig_event.created_at >= now() - make_interval(days => %s)
ORDER BY sig_version.signal_id, pool_event.event_id
"""

# A new cluster is saved without a center; naming.update_centers() sets it, so the "center =
# average of members" rule lives in one place for new and grown clusters alike.
CREATE_CLUSTER_SQL = "INSERT INTO clusters (signal_id) VALUES (%s) RETURNING id"

ADD_MEMBERS_SQL = """
INSERT INTO cluster_members (event_id, cluster_id)
SELECT event_id, %s FROM event_embeddings WHERE event_id = ANY(%s)
"""

# Each member's distance to the center it was just given (the "distance when assigned").
RECORD_DISTANCES_SQL = """
UPDATE cluster_members member
SET distance = embedded.embedding <=> owner.centroid
FROM event_embeddings embedded, clusters owner
WHERE member.cluster_id = %s
  AND embedded.event_id = member.event_id
  AND owner.id = member.cluster_id
"""


def load_pool(conn, window_days):
    """Pool events created in the last `window_days`, grouped by Signal: {signal_id: [PoolEvent]}."""
    pool_by_signal = {}
    for event_id, signal_id, session_id, trace_id, embedding_text in conn.execute(
        POOL_SQL, (window_days,)
    ):
        conversation = ("session", session_id) if session_id is not None else ("trace", trace_id)
        embedding = np.array(json.loads(embedding_text), dtype=np.float64)
        pool_by_signal.setdefault(signal_id, []).append(
            PoolEvent(event_id, signal_id, conversation, embedding)
        )
    return pool_by_signal


def group_vectors(vectors, threshold):
    """Average-linkage groups (lists of row indexes) whose merges are all within `threshold`.

    Every vector starts as its own group; the two closest groups (by the average distance between
    their members) merge until the closest pair is farther than `threshold`. A distance exactly at
    the threshold still merges, matching assign's "within the threshold joins".
    Raises ValueError for vectors with no direction (all zeros), where cosine distance is undefined.
    """
    if len(vectors) == 0:
        return []
    if len(vectors) == 1:
        return [[0]]
    merges = linkage(np.asarray(vectors), method="average", metric="cosine")
    labels = fcluster(merges, t=threshold, criterion="distance")
    groups = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)
    return sorted(groups.values())


def qualifying_groups(events, threshold, min_events, min_conversations):
    """The groups of `events` (one Signal's pool) that are big and varied enough to be clusters."""
    groups = group_vectors([event.embedding for event in events], threshold)
    qualifying = []
    for indexes in groups:
        members = [events[index] for index in indexes]
        conversations = {member.conversation for member in members}
        if len(members) >= min_events and len(conversations) >= min_conversations:
            qualifying.append(members)
    return qualifying


def create_cluster(conn, signal_id, event_ids):
    """One new leaf cluster with its center and members. Returns its ID. Unnamed until naming.

    Runs inside the caller's per-Signal transaction, so the cluster never exists without its
    center, nor members without their distances.
    """
    cluster_id = conn.execute(CREATE_CLUSTER_SQL, (signal_id,)).fetchone()[0]
    conn.execute(ADD_MEMBERS_SQL, (cluster_id, event_ids))
    update_centers(conn, [cluster_id])
    conn.execute(RECORD_DISTANCES_SQL, (cluster_id,))
    return cluster_id


def group_pool(conn, threshold, min_events, min_conversations, window_days):
    """Turn qualifying groups of pool events into new clusters, one Signal at a time.

    Returns how many clusters were created.
    """
    created = 0
    for signal_id, events in load_pool(conn, window_days).items():
        try:
            with conn.transaction():
                groups = qualifying_groups(events, threshold, min_events, min_conversations)
                for members in groups:
                    create_cluster(conn, signal_id, [member.event_id for member in members])
            created += len(groups)  # counted only once this Signal's transaction has committed
        except Exception:
            log.exception("pool grouping failed for signal %s; other signals carry on", signal_id)
    return created
