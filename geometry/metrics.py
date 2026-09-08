"""Geometry-only evaluation metrics for nozzle layouts."""

from __future__ import annotations

import math

import numpy as np

from geometry.constraints import (
    as_point_array,
    nearest_neighbor_distances,
    validate_layout_constraints,
)
from geometry.definitions import GEOMETRY_METRIC_KEYS
from geometry.symmetry import symmetry_check
from validation import require_finite


def _finite(value: float) -> float | None:
    """Return a finite Python float, otherwise the M4 undefined value."""

    result = float(value)
    return result if math.isfinite(result) else None


def evaluate_geometry_metrics(
    points,
    *,
    R: float,
    d: float,
    s_min: float,
    symmetry_tolerance: float = 1e-6,
) -> dict[str, object]:
    """Return geometry descriptors only, without hard-constraint decisions.

    Lengths use millimetres. Mathematically undefined numeric metrics use
    ``None`` and never a fabricated zero, NaN, or infinity. Symmetry fields are
    discrete, tolerance-dependent descriptors rather than hard constraints.
    """

    require_finite(R, "R")
    require_finite(d, "d")
    require_finite(s_min, "s_min")
    array = as_point_array(points)
    radii = np.linalg.norm(array, axis=1) if len(array) else np.array([], dtype=float)
    nearest = nearest_neighbor_distances(array)

    if len(nearest):
        min_distance = _finite(np.min(nearest))
        mean_nearest = _finite(np.mean(nearest))
        std_nearest = _finite(np.std(nearest, ddof=0))
        if mean_nearest is not None and mean_nearest > 0 and std_nearest is not None:
            nn_cv = _finite(std_nearest / mean_nearest)
        else:
            nn_cv = None
        uniformity_score = _finite(1.0 / (1.0 + nn_cv)) if nn_cv is not None else None
    else:
        min_distance = mean_nearest = std_nearest = None
        nn_cv = uniformity_score = None

    if len(radii):
        max_radius = _finite(np.max(radii))
        mean_radius = _finite(np.mean(radii))
        radial_std = _finite(np.std(radii, ddof=0))
        centroid_offset = _finite(np.linalg.norm(np.mean(array, axis=0)))
    else:
        max_radius = mean_radius = radial_std = centroid_offset = None

    usable_radius = float(R) - float(d) / 2.0
    if usable_radius > 0 and math.isfinite(usable_radius):
        normalized_max_radius = (
            _finite(max_radius / usable_radius) if max_radius is not None else None
        )
        normalized_mean_radius = (
            _finite(mean_radius / usable_radius) if mean_radius is not None else None
        )
        normalized_radial_std = (
            _finite(radial_std / usable_radius) if radial_std is not None else None
        )
        normalized_centroid_offset = (
            _finite(centroid_offset / usable_radius)
            if centroid_offset is not None else None
        )
    else:
        normalized_max_radius = normalized_mean_radius = None
        normalized_radial_std = normalized_centroid_offset = None

    anisotropy = None
    if len(array) >= 2:
        centered = array - np.mean(array, axis=0)
        with np.errstate(over="ignore", invalid="ignore"):
            covariance = centered.T @ centered / len(array)
        if np.all(np.isfinite(covariance)):
            eigenvalues = np.linalg.eigvalsh(covariance)
            total = float(np.sum(eigenvalues))
            if total > 0 and math.isfinite(total):
                # Tiny negative eigenvalues can occur from floating-point roundoff.
                low = max(0.0, float(eigenvalues[0]))
                high = max(0.0, float(eigenvalues[-1]))
                anisotropy = _finite((high - low) / (high + low))

    symmetry = symmetry_check(array, tolerance=symmetry_tolerance)
    values = {
        "min_center_distance": min_distance,
        "mean_nearest_neighbor_distance": mean_nearest,
        "std_nearest_neighbor_distance": std_nearest,
        "nearest_neighbor_cv": nn_cv,
        "uniformity_score": uniformity_score,
        "minimum_spacing_margin": (
            _finite(min_distance - max(float(d), float(s_min)))
            if min_distance is not None else None
        ),
        "minimum_boundary_margin": (
            _finite(usable_radius - max_radius) if max_radius is not None else None
        ),
        "max_center_radius": max_radius,
        "mean_center_radius": mean_radius,
        "radial_std": radial_std,
        "normalized_max_center_radius": normalized_max_radius,
        "normalized_mean_center_radius": normalized_mean_radius,
        "normalized_radial_std": normalized_radial_std,
        "centroid_offset": centroid_offset,
        "normalized_centroid_offset": normalized_centroid_offset,
        "second_moment_anisotropy": anisotropy,
        **symmetry,
    }
    # A direct equality guard keeps calculation and metadata from drifting.
    if tuple(values) != GEOMETRY_METRIC_KEYS:  # pragma: no cover - developer guard
        raise RuntimeError("Geometry metric values and definitions are out of sync.")
    return values


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
    """Return the backward-compatible composite validation/evaluation report.

    New code may call :func:`evaluate_geometry_metrics` and
    ``validate_layout_constraints`` separately when a strict responsibility
    boundary is useful. This composite entry point remains for existing callers.
    """

    array = as_point_array(points)
    metrics = evaluate_geometry_metrics(
        array, R=R, d=d, s_min=s_min, symmetry_tolerance=symmetry_tolerance,
    )
    constraints = validate_layout_constraints(
        array, N=expected_N, R=R, d=d, s_min=s_min, tolerance=tolerance,
    )

    # Preserve the order of every pre-M4 field; append new descriptors after it.
    legacy = {
        "nozzle_count": constraints["nozzle_count"],
        "min_center_distance": metrics["min_center_distance"],
        "mean_nearest_neighbor_distance": metrics["mean_nearest_neighbor_distance"],
        "std_nearest_neighbor_distance": metrics["std_nearest_neighbor_distance"],
        "nearest_neighbor_cv": metrics["nearest_neighbor_cv"],
        "uniformity_score": metrics["uniformity_score"],
        "max_center_radius": metrics["max_center_radius"],
        "mean_center_radius": metrics["mean_center_radius"],
        "radial_std": metrics["radial_std"],
        **{key: value for key, value in constraints.items() if key != "nozzle_count"},
        "x_axis_symmetry": metrics["x_axis_symmetry"],
        "y_axis_symmetry": metrics["y_axis_symmetry"],
        "origin_symmetry": metrics["origin_symmetry"],
    }
    return {**legacy, **{key: value for key, value in metrics.items() if key not in legacy}}
