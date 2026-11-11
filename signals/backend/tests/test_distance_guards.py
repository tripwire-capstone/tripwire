"""Alarm tests for the clusterer's distance math (decided Nov 11, drift cleanup).

These don't test a feature; they fail if someone later changes something that would make clustering
quietly wrong:
  * assign and naming measure distance with pgvector (`<=>`, in the database); grouping measures it
    with SciPy (`metric="cosine"`, in Python). They must agree, or the two steps would disagree about
    what the threshold means.
  * assign's "nearest cluster" search must be exact. An approximate pgvector index (HNSW, IVFFlat)
    makes it fast but can return the second-closest cluster, with no error.
"""

import random

import numpy as np
import pytest
from scipy.spatial.distance import cdist

from tripwire.placeholder import vector_text

CLUSTERING_TABLES = ("event_embeddings", "clusters", "cluster_members")


def test_pgvector_and_scipy_measure_the_same_cosine_distance(conn):
    generator = random.Random(11)
    # Vectors of different lengths and directions, including ones far apart: cosine distance
    # ignores length, and both tools must agree on that too.
    vectors = [[generator.uniform(-1, 1) * scale for dimension in range(1536)] for scale in (1, 3, 0.2, 10)]

    scipy_distances = cdist(np.array(vectors), np.array(vectors), metric="cosine")
    for row, vector_a in enumerate(vectors):
        for col, vector_b in enumerate(vectors):
            pgvector_distance = conn.execute(
                "SELECT %s::vector <=> %s::vector", (vector_text(vector_a), vector_text(vector_b))
            ).fetchone()[0]
            assert pgvector_distance == pytest.approx(scipy_distances[row][col], abs=1e-6), (
                "pgvector and SciPy disagree on distance: assign/naming (pgvector <=>) and grouping "
                "(SciPy metric='cosine') must use the same metric, or they'd disagree about the threshold"
            )


def approximate_vector_indexes(conn):
    """Approximate (HNSW / IVFFlat) indexes on the clustering tables in the current schema."""
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    return conn.execute(
        """SELECT tablename, indexname FROM pg_indexes
           WHERE schemaname = %s AND tablename = ANY(%s)
             AND (indexdef ILIKE '%%using hnsw%%' OR indexdef ILIKE '%%using ivfflat%%')""",
        (schema, list(CLUSTERING_TABLES)),
    ).fetchall()


def test_clustering_tables_have_no_approximate_vector_index(conn):
    approximate = approximate_vector_indexes(conn)
    assert approximate == [], (
        f"approximate vector index found: {approximate}. Assign assumes it always finds the EXACT "
        "nearest cluster; with this index it could pick the second-closest one without any error, "
        "while pool grouping (SciPy) stays exact. Update assign and DESIGN.md first, then this test."
    )


def test_the_index_alarm_really_detects_an_approximate_index(conn):
    # Proves the alarm above works: in this throwaway schema, add the kind of index it guards against.
    conn.execute("CREATE INDEX test_hnsw ON event_embeddings USING hnsw (embedding vector_cosine_ops)")
    assert approximate_vector_indexes(conn) == [("event_embeddings", "test_hnsw")]
