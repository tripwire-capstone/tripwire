"""Test helper: embeddings at exact cosine distances, for boundary tests (DESIGN.md Q14).

Every vector is a unit vector built from two axes of the 1536-dimension space:

    vector_at(distance, axis) = (1 - distance) * e0 + sqrt(1 - (1 - distance)^2) * e_axis

so its cosine distance from base_vector() (= e0) is exactly `distance`. Two vectors on
*different* axes are 1 - (1 - distance_a) * (1 - distance_b) apart, which lets a test place
an event near one cluster and predictably farther from another.

Distances are limited to 0..1 (at most 90 degrees from the base), plenty for thresholds
around 0.30.

For arbitrary shapes (any set of pairwise distances), use vectors_with_distances().
"""

import math

DIMENSIONS = 1536


def base_vector():
    values = [0.0] * DIMENSIONS
    values[0] = 1.0
    return values


def vector_at(distance, axis):
    """A unit vector exactly `distance` (cosine) from base_vector(), tilted toward `axis`."""
    if not 0 <= distance <= 1:
        raise ValueError(f"distance must be between 0 and 1, got {distance}")
    if not 1 <= axis < DIMENSIONS:
        raise ValueError(f"axis must be between 1 and {DIMENSIONS - 1}, got {axis}")
    cosine = 1 - distance
    values = [0.0] * DIMENSIONS
    values[0] = cosine
    values[axis] = math.sqrt(1 - cosine**2)
    return values


def distance_between(distance_a, distance_b):
    """Cosine distance between vector_at(distance_a, axis_a) and vector_at(distance_b, axis_b)
    when axis_a != axis_b."""
    return 1 - (1 - distance_a) * (1 - distance_b)


def vectors_with_distances(distances):
    """Unit vectors whose pairwise cosine distances are exactly `distances` (a square matrix).

    For grouping tests that need arbitrary shapes, e.g. "A-B 0.20, B-C 0.28, A-C 0.40".
    How: cosine similarity = 1 - distance, and a matrix of similarities between unit vectors can
    be factored as L @ L.T (Cholesky); the rows of L are those vectors. Raises ValueError if no
    vectors can have these distances (e.g. A-B 0.1, B-C 0.1, A-C 0.9).
    """
    count = len(distances)
    if count > DIMENSIONS:
        raise ValueError(f"at most {DIMENSIONS} vectors")
    similarity = [[1 - distances[row][col] for col in range(count)] for row in range(count)]
    factor = [[0.0] * count for row in range(count)]
    for row in range(count):
        for col in range(row + 1):
            overlap = sum(factor[row][inner] * factor[col][inner] for inner in range(col))
            if row == col:
                remaining = similarity[row][row] - overlap
                if remaining < -1e-9:
                    raise ValueError("no set of vectors has these distances")
                factor[row][col] = math.sqrt(max(remaining, 0.0))
            elif factor[col][col] > 1e-12:
                factor[row][col] = (similarity[row][col] - overlap) / factor[col][col]
    return [row_values + [0.0] * (DIMENSIONS - count) for row_values in factor]


def vectors_all_apart(count, distance):
    """`count` unit vectors, every pair exactly `distance` apart."""
    return vectors_with_distances(
        [[0.0 if row == col else distance for col in range(count)] for row in range(count)]
    )
