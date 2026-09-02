"""Deterministic non-grid layouts used as geometry-only comparison families."""

from __future__ import annotations

import math

import numpy as np

from layouts._common import finalize_layout


def deterministic_irregular_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    inner_radius: float,
    outer_radius: float,
    angular_increment: float = math.pi * (3.0 - math.sqrt(5.0)),
    angular_offset: float = 0.0,
    radial_exponent: float = 0.5,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Generate a reproducible non-ring sequence from radial and angular laws.

    This is a deterministic comparison layout, not a claim of physical or
    literature-proven optimality.
    """

    if N <= 0:
        raise ValueError("N must be positive.")
    if inner_radius < 0 or outer_radius < inner_radius:
        raise ValueError("Require 0 <= inner_radius <= outer_radius.")
    if radial_exponent <= 0:
        raise ValueError("radial_exponent must be positive.")
    fractions = (np.arange(N, dtype=float) + 0.5) / N
    radii = inner_radius + (outer_radius - inner_radius) * fractions**radial_exponent
    points = []
    for index, radius in enumerate(radii):
        angle = angular_offset + index * angular_increment
        points.append((radius * math.cos(angle), radius * math.sin(angle)))
    return finalize_layout(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
