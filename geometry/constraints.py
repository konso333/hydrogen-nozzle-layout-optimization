"""Hard geometric constraints for nozzle layouts."""

from __future__ import annotations

import math

import numpy as np


class LayoutConstraintError(ValueError):
    """Raised when a generated point set violates a requested constraint."""


def as_point_array(points) -> np.ndarray:
    """Return a finite float array with shape ``(N, 2)``."""

    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return np.empty((0, 2), dtype=float)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError("points must have shape (N, 2).")
    if not np.all(np.isfinite(array)):
        raise ValueError("points must contain only finite coordinates.")
    return array


def pairwise_distance_matrix(points) -> np.ndarray:
    """Return the full Euclidean centre-distance matrix."""

    array = as_point_array(points)
    if len(array) == 0:
        return np.empty((0, 0), dtype=float)
    differences = array[:, None, :] - array[None, :, :]
    return np.linalg.norm(differences, axis=2)


def pairwise_distances(points) -> np.ndarray:
    """Return each unordered pairwise centre distance once."""

    matrix = pairwise_distance_matrix(points)
    if len(matrix) < 2:
        return np.array([], dtype=float)
    return matrix[np.triu_indices(len(matrix), k=1)]


def minimum_center_distance(points) -> float:
    """Return the minimum centre distance, or NaN for fewer than two points."""

    distances = pairwise_distances(points)
    return float(np.min(distances)) if len(distances) else math.nan


def nearest_neighbor_distances(points) -> np.ndarray:
    """Return one nearest-neighbour distance per point."""

    matrix = pairwise_distance_matrix(points)
    if len(matrix) < 2:
        return np.array([], dtype=float)
    np.fill_diagonal(matrix, np.inf)
    return np.min(matrix, axis=1)


def boundary_check(
    points,
    R: float,
    d: float,
    tolerance: float = 1e-9,
) -> bool:
    """Check that every full nozzle lies inside the circular burner face."""

    array = as_point_array(points)
    if R <= 0 or d <= 0 or d > 2 * R:
        return False
    radii = np.linalg.norm(array, axis=1) if len(array) else np.array([])
    return bool(np.all(radii <= R - d / 2.0 + tolerance))


def overlap_check(points, d: float, tolerance: float = 1e-9) -> bool:
    """Check the independent physical non-overlap condition."""

    if d <= 0:
        return False
    minimum = minimum_center_distance(points)
    return bool(math.isnan(minimum) or minimum >= d - tolerance)


def spacing_check(
    points,
    d: float,
    s_min: float,
    tolerance: float = 1e-9,
) -> bool:
    """Check both nozzle non-overlap and the requested centre spacing."""

    if d <= 0 or s_min < 0:
        return False
    required_distance = max(d, s_min)
    minimum = minimum_center_distance(points)
    return bool(math.isnan(minimum) or minimum >= required_distance - tolerance)


def validate_layout_constraints(
    points,
    *,
    N: int | None,
    R: float,
    d: float,
    s_min: float,
    tolerance: float = 1e-9,
) -> dict[str, object]:
    """Evaluate hard constraints and return explicit failure reasons."""

    array = as_point_array(points)
    if N is not None and N < 0:
        raise ValueError("N cannot be negative.")

    count_ok = N is None or len(array) == N
    boundary_ok = boundary_check(array, R=R, d=d, tolerance=tolerance)
    no_overlap = overlap_check(array, d=d, tolerance=tolerance)
    spacing_ok = spacing_check(
        array,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
    reasons: list[str] = []
    if not count_ok:
        reasons.append(f"expected {N} points but generated {len(array)}")
    if not boundary_ok:
        reasons.append("one or more nozzles cross the circular boundary")
    if not no_overlap:
        reasons.append("one or more nozzles overlap")
    if not spacing_ok:
        reasons.append(
            "minimum centre distance is below "
            f"max(d, s_min)={max(d, s_min):.6g} mm"
        )

    return {
        "nozzle_count": len(array),
        "count_ok": bool(count_ok),
        "boundary_ok": bool(boundary_ok),
        "overlap_ok": bool(no_overlap),
        "spacing_ok": bool(spacing_ok),
        "feasible": bool(count_ok and boundary_ok and no_overlap and spacing_ok),
        "failure_reasons": reasons,
    }


def ensure_layout_feasible(
    points,
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Return normalized points or raise ``LayoutConstraintError``."""

    array = as_point_array(points)
    report = validate_layout_constraints(
        array,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
    if not report["feasible"]:
        raise LayoutConstraintError("; ".join(report["failure_reasons"]))
    return [(float(x), float(y)) for x, y in array]
