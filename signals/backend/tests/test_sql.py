"""Tests for the shared SQL rules (tripwire/clusterer/sql.py): they're used, and used safely."""

import re
from pathlib import Path

from tests.clustering_data import add_event, add_failed_summary, add_signal
from tests.vectors import base_vector
from tripwire.clusterer.sql import unfinished

CLUSTERER = Path(__file__).resolve().parents[1] / "tripwire" / "clusterer"
# The placeholder (cluster.py) is to be deleted once the real loop is the default; sql.py is the source.
NOT_CHECKED = {"sql.py", "cluster.py"}

# Each rule as it would look typed out by hand.
HAND_WRITTEN_RULES = {
    "leaf clusters only": re.compile(r"\blevel\s*=\s*0\b"),
    "in the pool / not in a cluster": re.compile(r"FROM\s+cluster_members\s+\w+\s+WHERE\s+\w+\.event_id\s*=", re.I),
    "event -> its Signal": re.compile(r"JOIN\s+signal_versions\s+\w+\s+ON\s+\w+\.id\s*=\s*\w+\.signal_version_id", re.I),
}


def test_clusterer_queries_use_the_shared_rules_instead_of_retyping_them():
    # Anti-drift: a rule retyped in a query is a second copy that can drift. Use tripwire/clusterer/sql.py.
    found = []
    for source in sorted(CLUSTERER.glob("*.py")):
        if source.name in NOT_CHECKED:
            continue
        text = source.read_text()
        for rule, pattern in HAND_WRITTEN_RULES.items():
            if pattern.search(text):
                found.append(f"{source.name}: {rule}")
    assert found == [], f"rule typed out by hand instead of using sql.py: {found}"


def test_unfinished_keeps_its_meaning_when_negated(conn):
    # It's parenthesized, so NOT applies to the whole rule, not just its first half.
    _, version_id = add_signal(conn)
    unembedded_failed = add_event(conn, version_id, None)
    add_failed_summary(conn, unembedded_failed)              # finished (summary failed)
    still_waiting = add_event(conn, version_id, None)        # unfinished
    add_event(conn, version_id, base_vector())               # finished (embedded)

    rows = conn.execute(
        f"SELECT id FROM signal_events sig_event WHERE NOT {unfinished('sig_event.id')} ORDER BY id"
    ).fetchall()
    assert still_waiting not in [row[0] for row in rows]
    assert unembedded_failed in [row[0] for row in rows]
