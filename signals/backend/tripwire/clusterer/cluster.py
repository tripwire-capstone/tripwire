"""PLACEHOLDER clusterer: one step of clustering new events, without real embeddings.

For every event with no embedding yet:
  * store a fixed embedding (the same vector for every event)
  * for about STUB_CLUSTER_RATE of them (decided by the event ID's hash), link the event
    to its signal's first cluster; the rest stay in the pool (embedded, no cluster)

Assumes one clusterer. To be replaced by real embed() and assign_and_discover().
"""

import logging

from ..placeholder import FIXED_EMBEDDING, picked, vector_text

log = logging.getLogger("clusterer")

# Events with no embedding, with their signal's first cluster (NULL if it has none).
NEW_EVENTS_SQL = """
SELECT e.id,
       (SELECT c.id FROM clusters c
        WHERE c.signal_id = v.signal_id AND c.level = 0
        ORDER BY c.id LIMIT 1) AS cluster_id
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
WHERE NOT EXISTS (SELECT 1 FROM event_embeddings m WHERE m.event_id = e.id)
ORDER BY e.id
LIMIT %s
"""


def cluster_step(conn, cluster_rate, batch=100):
    """Embed up to `batch` events and link some to a cluster, in one transaction.

    Returns (embedded, linked).
    """
    embedded = linked = 0
    vector = vector_text(FIXED_EMBEDDING)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(NEW_EVENTS_SQL, (batch,))
        for event_id, cluster_id in cur.fetchall():
            cur.execute(
                """INSERT INTO event_embeddings (event_id, model, embedding)
                   VALUES (%s, 'placeholder', %s) ON CONFLICT DO NOTHING""",
                (event_id, vector),
            )
            if cur.rowcount == 0:
                continue
            embedded += 1
            if cluster_id is not None and picked(f"cluster:{event_id}", cluster_rate):
                cur.execute(
                    """INSERT INTO cluster_members (event_id, cluster_id, distance)
                       VALUES (%s, %s, 0)""",
                    (event_id, cluster_id),
                )
                cur.execute("UPDATE clusters SET updated_at = now() WHERE id = %s", (cluster_id,))
                linked += 1
    return embedded, linked
