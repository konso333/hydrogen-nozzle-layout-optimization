"""Deterministic single- and multi-sector fan-shaped layouts."""

from __future__ import annotations

import math

import numpy as np

from layouts._common import finalize_layout


def sector_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    num_sectors: int,
    points_per_sector: int,
    inner_radius: float,
    outer_radius: float,
    sector_angle: float,
    angular_offset: float = 0.0,
    radial_levels: int = 1,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Place an identical deterministic fan pattern in equally spaced sectors.

    Angles are in radians.  Points in each sector are distributed across the
    requested radial levels; any remainder is assigned to the inner levels.
    """

    if num_sectors <= 0 or points_per_sector <= 0:
        raise ValueError("num_sectors and points_per_sector must be positive.")
    if num_sectors * points_per_sector != N:
        raise ValueError("num_sectors * points_per_sector must equal N.")
    if radial_levels <= 0 or radial_levels > points_per_sector:
        raise ValueError("radial_levels must be between 1 and points_per_sector.")
    if inner_radius < 0 or outer_radius < inner_radius:
        raise ValueError("Require 0 <= inner_radius <= outer_radius.")
    if not 0 <= sector_angle < 2.0 * math.pi / num_sectors:
        raise ValueError(
            "sector_angle must be non-negative and smaller than sector spacing."
        )

    radii = np.linspace(inner_radius, outer_radius, radial_levels)
    base_count, remainder = divmod(points_per_sector, radial_levels)
    level_counts = [base_count + int(level < remainder) for level in range(radial_levels)]
    points: list[tuple[float, float]] = []

    for sector_index in range(num_sectors):
        sector_center = angular_offset + 2.0 * math.pi * sector_index / num_sectors
        for radius, count in zip(radii, level_counts):
            if count == 1:
                relative_angles = [0.0]
            else:
                relative_angles = np.linspace(
                    -sector_angle / 2.0,
                    sector_angle / 2.0,
                    count,
                )
            for relative_angle in relative_angles:
                angle = sector_center + float(relative_angle)
                points.append((radius * math.cos(angle), radius * math.sin(angle)))

    return finalize_layout(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
