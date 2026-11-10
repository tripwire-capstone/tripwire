"""Tests for the test helper in tests/vectors.py: the vectors really are the distances asked for,
measured the way the clusterer measures them (pgvector's <=>)."""

import pytest

from tests.clustering_data import cosine_distance
from tests.vectors import (
    base_vector,
    distance_between,
    vector_at,
    vectors_all_apart,
    vectors_with_distances,
)

TOLERANCE = 1e-5


def test_vector_at_gives_exact_distance_from_base(conn):
    for distance in (0.0, 0.1, 0.29, 0.31, 0.5, 1.0):
        measured = cosine_distance(conn, base_vector(), vector_at(distance, axis=1))
        assert measured == pytest.approx(distance, abs=TOLERANCE)


def test_vector_at_predicts_distance_between_two_axes(conn):
    measured = cosine_distance(conn, vector_at(0.1, axis=1), vector_at(0.2, axis=2))
    assert measured == pytest.approx(distance_between(0.1, 0.2), abs=TOLERANCE)


def test_vector_at_rejects_out_of_range_input():
    with pytest.raises(ValueError):
        vector_at(1.5, axis=1)
    with pytest.raises(ValueError):
        vector_at(0.2, axis=0)


def test_vectors_with_distances_gives_every_pairwise_distance(conn):
    wanted = [
        [0.00, 0.20, 0.40],
        [0.20, 0.00, 0.28],
        [0.40, 0.28, 0.00],
    ]
    vectors = vectors_with_distances(wanted)
    for row in range(3):
        for col in range(3):
            measured = cosine_distance(conn, vectors[row], vectors[col])
            assert measured == pytest.approx(wanted[row][col], abs=TOLERANCE)


def test_vectors_all_apart_gives_equal_distances(conn):
    vectors = vectors_all_apart(4, 0.29)
    for row in range(4):
        for col in range(row + 1, 4):
            assert cosine_distance(conn, vectors[row], vectors[col]) == pytest.approx(0.29, abs=TOLERANCE)


def test_vectors_with_distances_rejects_impossible_distances():
    # A and C can't be 0.9 apart when both are 0.1 from B.
    with pytest.raises(ValueError):
        vectors_with_distances([[0, 0.1, 0.9], [0.1, 0, 0.1], [0.9, 0.1, 0]])
