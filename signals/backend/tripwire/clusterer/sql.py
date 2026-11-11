"""The SQL rules the clusterer's queries share, each written once (decided Nov 11, drift cleanup).

Every query that needs one of these rules builds itself from the pieces below instead of typing
the rule out again, so changing a rule here changes it everywhere. The aliases passed in are
always fixed names from our own code, never user input.

Used by: assign.py, pool.py, naming.py, health.py, summarize.py. Not used by the placeholder (cluster.py, to be
deleted once the real loop is the default) or check.sql (a plain SQL report that can't import
Python; it carries a comment pointing here). If something outside Python (e.g. the API) needs "the
pool", turn POOL into a database view built from the same rule.
"""


def event_signal(event_alias, version_alias):
    """Join an event to its Signal: event -> its signal version -> signal_id (Q6). After this,
    `{version_alias}.signal_id` is the event's Signal."""
    return f"JOIN signal_versions {version_alias} ON {version_alias}.id = {event_alias}.signal_version_id"


def not_in_a_cluster(event_id):
    """True when the event has no cluster_members row."""
    return f"NOT EXISTS (SELECT 1 FROM cluster_members member WHERE member.event_id = {event_id})"


def in_the_pool(embedding_alias):
    """For an event_embeddings row: the event is in the pool (embedded, not in a cluster). The pool
    is a query, not a status column (CONTRACT.md)."""
    return not_in_a_cluster(f"{embedding_alias}.event_id")


def leaf_cluster(cluster_alias):
    """Only leaf clusters (`level = 0`). The MVP has no hierarchy (#18); when it arrives, every query
    using this piece is a place to revisit."""
    return f"{cluster_alias}.level = 0"


def unfinished(event_id):
    """The event hasn't finished clustering: not embedded (so neither clustered nor in the pool) and
    its summary hasn't failed. "Finished" is defined in DESIGN.md ("Definitions used by both halves")."""
    # Parenthesized, so `NOT {unfinished(...)}` or an OR around it can't change what it means.
    return (
        f"(NOT EXISTS (SELECT 1 FROM event_embeddings embedded WHERE embedded.event_id = {event_id})"
        f" AND NOT EXISTS (SELECT 1 FROM event_summaries summary"
        f" WHERE summary.event_id = {event_id} AND summary.status = 'failed'))"
    )
