"""Step 2 of a clusterer pass: assign pool events to existing clusters.

Decisions (docs/clustering/DESIGN.md):
  * Q6: clusters belong to a Signal, so an event is only compared with its own Signal's clusters.
  * Q7: join the nearest center within the threshold; otherwise wait in the pool. One decide()
    with distance bands, so an LLM can later decide the middle band.
  * Q13: "decide then apply". Every decision in a pass uses the centers as they stood at the start
    of the pass; the joins are then applied together. Centers only move in step 5, so the order
    of events within a pass can't change the result.

Every pool event is checked on every pass, not only newly embedded ones: an event that was too
far can join once a cluster's center moves closer.
"""

import enum
from dataclasses import dataclass

from .sql import event_signal, in_the_pool, leaf_cluster


class Decision(enum.Enum):
    JOIN = "join"
    WAIT = "wait"


@dataclass(frozen=True)
class Candidate:
    """A pool event and the nearest leaf cluster of its own Signal."""

    event_id: int
    cluster_id: int
    distance: float  # cosine distance from the event to the cluster's center


def decide(distance, threshold):
    """Join or wait, by cosine distance to the nearest center (Q7).

    Three bands: close joins, far waits, and the middle band is decided by the threshold for
    now. Both band edges equal the threshold, so the middle band is empty today. When an LLM
    check is added (after the MVP), the edges move apart and only the middle branch changes.
    """
    join_at_or_below = threshold
    wait_above = threshold
    if distance <= join_at_or_below:
        return Decision.JOIN
    if distance > wait_above:
        return Decision.WAIT
    # Middle band (join_at_or_below < distance <= wait_above). Later: an LLM decides here.
    return Decision.JOIN if distance <= threshold else Decision.WAIT


# For each pool event (an embedding with no cluster_members row), the nearest leaf cluster of
# the same Signal by cosine distance (pgvector's <=>). Ties go to the lower cluster ID, so the
# result never depends on row order. Events whose Signal has no cluster with a center yet
# don't appear: they stay in the pool.
#
# Distance must stay the same metric as pool.py's SciPy grouping (cosine), and this search must
# stay EXACT: an approximate pgvector index (HNSW / IVFFlat) could return the second-closest
# cluster without any error. Both are guarded by tests/test_distance_guards.py.
NEAREST_CLUSTER_SQL = f"""
SELECT pool_event.event_id, nearest.cluster_id, nearest.distance
FROM event_embeddings pool_event
JOIN signal_events sig_event ON sig_event.id = pool_event.event_id
{event_signal("sig_event", "sig_version")}
CROSS JOIN LATERAL (
    SELECT candidate.id AS cluster_id,
           candidate.centroid <=> pool_event.embedding AS distance
    FROM clusters candidate
    WHERE candidate.signal_id = sig_version.signal_id
      AND {leaf_cluster("candidate")}
      AND candidate.centroid IS NOT NULL
    ORDER BY distance, candidate.id
    LIMIT 1
) nearest
WHERE {in_the_pool("pool_event")}
ORDER BY pool_event.event_id
"""


def find_nearest(conn):
    """Every pool event that has a candidate cluster, with its nearest one."""
    rows = conn.execute(NEAREST_CLUSTER_SQL).fetchall()
    return [Candidate(event_id, cluster_id, distance) for event_id, cluster_id, distance in rows]


def assign_pool(conn, threshold):
    """Decide for every pool event, then apply all the joins in one transaction.

    Returns the joins (one Candidate per event that joined), so the caller knows which clusters
    gained members: step 5 recomputes only those centers. Doesn't move any center itself.
    """
    with conn.transaction():
        joins = [
            candidate
            for candidate in find_nearest(conn)
            if decide(candidate.distance, threshold) is Decision.JOIN
        ]
        if not joins:
            return []
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO cluster_members (event_id, cluster_id, distance)
                   VALUES (%s, %s, %s)""",
                [(join.event_id, join.cluster_id, join.distance) for join in joins],
            )
            cursor.execute(
                "UPDATE clusters SET updated_at = now() WHERE id = ANY(%s)",
                (sorted({join.cluster_id for join in joins}),),
            )
    return joins
