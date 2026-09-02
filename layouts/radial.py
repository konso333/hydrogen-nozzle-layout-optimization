"""Deterministic radial-spoke layouts."""

from __future__ import annotations

import math

import numpy as np

from layouts._common import finalize_layout


def radial_spoke_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    num_spokes: int,
    points_per_spoke: int,
    inner_radius: float,
    outer_radius: float,
    angular_offset: float = 0.0,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Place points on equally spaced rays with deterministic radial positions."""

    if num_spokes <= 0 or points_per_spoke <= 0:
        raise ValueError("num_spokes and points_per_spoke must be positive.")
    if num_spokes * points_per_spoke != N:
        raise ValueError("num_spokes * points_per_spoke must equal N.")
    if inner_radius < 0 or outer_radius < inner_radius:
        raise ValueError("Require 0 <= inner_radius <= outer_radius.")
    if num_spokes > 1 and points_per_spoke > 1 and inner_radius == 0:
        raise ValueError("inner_radius must be positive to avoid duplicate centre points.")

    if points_per_spoke == 1:
        radii = [float(outer_radius)]
    else:
        radii = np.linspace(inner_radius, outer_radius, points_per_spoke)

    points: list[tuple[float, float]] = []
    for spoke_index in range(num_spokes):
        angle = angular_offset + 2.0 * math.pi * spoke_index / num_spokes
        for radius in radii:
            points.append((radius * math.cos(angle), radius * math.sin(angle)))

    return finalize_layout(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
