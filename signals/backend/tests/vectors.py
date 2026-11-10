"""Test helper: embeddings at exact cosine distances, for boundary tests (DESIGN.md Q14).

Every vector is a unit vector built from two axes of the 1536-dimension space:

    vector_at(distance, axis) = (1 - distance) * e0 + sqrt(1 - (1 - distance)^2) * e_axis

so its cosine distance from base_vector() (= e0) is exactly `distance`. Two vectors on
*different* axes are 1 - (1 - distance_a) * (1 - distance_b) apart, which lets a test place
an event near one cluster and predictably farther from another.

Distances are limited to 0..1 (at most 90 degrees from the base), plenty for thresholds
around 0.30.
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
