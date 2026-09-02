"""Geometry-only evaluation metrics for nozzle layouts."""

from __future__ import annotations

import math

import numpy as np

from geometry.constraints import (
    as_point_array,
    nearest_neighbor_distances,
    validate_layout_constraints,
)
from geometry.symmetry import symmetry_check


def evaluate_geometry(
    points,
    *,
    R: float,
    d: float,
    s_min: float,
    expected_N: int | None = None,
    tolerance: float = 1e-9,
    symmetry_tolerance: float = 1e-6,
) -> dict[str, object]:
    """Compute constraints and descriptive geometry metrics.

    ``uniformity_score`` is a geometry-only transform of the nearest-neighbour
    coefficient of variation: ``1 / (1 + std / mean)``.  Higher is more
    regular.  It is not a combustion-efficiency estimate.
    """

    array = as_point_array(points)
    radii = np.linalg.norm(array, axis=1) if len(array) else np.array([])
    nearest = nearest_neighbor_distances(array)

    if len(nearest):
        min_distance = float(np.min(nearest))
        mean_nearest = float(np.mean(nearest))
        std_nearest = float(np.std(nearest, ddof=0))
        nn_cv = std_nearest / mean_nearest if mean_nearest > 0 else math.inf
        uniformity_score = 1.0 / (1.0 + nn_cv) if math.isfinite(nn_cv) else 0.0
    else:
        min_distance = math.nan
        mean_nearest = math.nan
        std_nearest = math.nan
        nn_cv = math.nan
        uniformity_score = math.nan

    constraints = validate_layout_constraints(
        array,
        N=expected_N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
    symmetry = symmetry_check(array, tolerance=symmetry_tolerance)

    return {
        "nozzle_count": len(array),
        "min_center_distance": min_distance,
        "mean_nearest_neighbor_distance": mean_nearest,
        "std_nearest_neighbor_distance": std_nearest,
        "nearest_neighbor_cv": float(nn_cv),
        "uniformity_score": float(uniformity_score),
        "max_center_radius": float(np.max(radii)) if len(radii) else math.nan,
        "mean_center_radius": float(np.mean(radii)) if len(radii) else math.nan,
        "radial_std": float(np.std(radii, ddof=0)) if len(radii) else math.nan,
        **{key: value for key, value in constraints.items() if key != "nozzle_count"},
        **symmetry,
    }
