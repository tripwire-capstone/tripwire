"""Steps 4 and 5 of a clusterer pass: name new clusters, then move centers after joins.

Decisions (docs/clustering/DESIGN.md):
  * Q10: a new cluster's name is the summary of its member closest to the center (the most
    typical member). No LLM call. A cluster is named once; name refresh (#20) comes after the MVP.
  * Q13 / step 5: a cluster's center is the plain average of all its members (pgvector's avg()).
    update_centers() is the only place this is computed: pool.py calls it for new clusters too.
    Cosine distance ignores length, so the average doesn't need normalizing.

Implementation choices (decided Nov 11, reasons in the work log and the PR):
  * Only clusters that gained members this pass get their center recomputed (assign returns
    them). A crash between assign and this step leaves a center slightly stale until the next
    join to that cluster, which recomputes it from all members.
  * The name comes from the closest member *that has a summary*. If no member has one (the
    skeleton's placeholder clusterer, test data, a missing row), the name stays NULL and is
    tried again next pass. Real summaries only: never a placeholder like "Cluster 12".
  * Within one Signal, a repeated name gets a number: "... (2)", "... (3)". It flags a stale name:
    a cluster's name is fixed when it forms but its center keeps moving, so a later cluster can
    form where the first one used to be and pick the same sentence.
"""

import logging

from .sql import leaf_cluster

log = logging.getLogger("clusterer")

# Recompute centers from all members. A cluster with no members left (every trace deleted)
# keeps its last center rather than losing it.
UPDATE_CENTERS_SQL = """
UPDATE clusters
SET centroid = coalesce(member_average.centroid, clusters.centroid), updated_at = now()
FROM (
    SELECT target.id,
           (SELECT avg(embedded.embedding)
            FROM cluster_members member
            JOIN event_embeddings embedded ON embedded.event_id = member.event_id
            WHERE member.cluster_id = target.id) AS centroid
    FROM clusters target
    WHERE target.id = ANY(%s)
) member_average
WHERE clusters.id = member_average.id
"""

# For each unnamed leaf cluster: the summary of its closest member that has a finished summary.
# Ties go to the lower event ID, so the choice never depends on row order.
NAME_CANDIDATES_SQL = f"""
SELECT DISTINCT ON (unnamed.id) unnamed.id, unnamed.signal_id, summary.summary
FROM clusters unnamed
JOIN cluster_members member ON member.cluster_id = unnamed.id
JOIN event_embeddings embedded ON embedded.event_id = member.event_id
JOIN event_summaries summary ON summary.event_id = member.event_id AND summary.status = 'done'
WHERE unnamed.name IS NULL AND {leaf_cluster("unnamed")} AND unnamed.centroid IS NOT NULL
ORDER BY unnamed.id, embedded.embedding <=> unnamed.centroid, member.event_id
"""


def update_centers(conn, cluster_ids):
    """Recompute the centers of `cluster_ids` as the average of all their members."""
    if not cluster_ids:
        return
    with conn.transaction():
        conn.execute(UPDATE_CENTERS_SQL, (sorted(set(cluster_ids)),))


def unique_name(base, taken):
    """`base`, or `base (2)`, `base (3)`, ... whichever isn't in `taken` yet."""
    if base not in taken:
        return base
    number = 2
    while f"{base} ({number})" in taken:
        number += 1
    return f"{base} ({number})"


def name_new_clusters(conn):
    """Name every unnamed cluster that has a member with a summary. Returns how many were named.

    Clusters are named in ID order, so when two new clusters in one Signal want the same name,
    the older one gets it plain and the newer one gets "(2)".
    """
    named = 0
    with conn.transaction():
        candidates = conn.execute(NAME_CANDIDATES_SQL).fetchall()
        taken_by_signal = {}
        for cluster_id, signal_id, summary in candidates:
            if signal_id not in taken_by_signal:
                rows = conn.execute(
                    "SELECT name FROM clusters WHERE signal_id = %s AND name IS NOT NULL",
                    (signal_id,),
                ).fetchall()
                taken_by_signal[signal_id] = {row[0] for row in rows}
            name = unique_name(summary, taken_by_signal[signal_id])
            conn.execute(
                "UPDATE clusters SET name = %s, updated_at = now() WHERE id = %s",
                (name, cluster_id),
            )
            taken_by_signal[signal_id].add(name)
            named += 1
    return named
