"""Symmetry metrics for two-dimensional nozzle point sets."""

from __future__ import annotations

import numpy as np


def symmetry_check(points, tolerance: float = 1e-6) -> dict[str, bool]:
    """Check reflection about both axes and 180-degree rotation.

    Symmetry is reported as a metric.  It is not a hard feasibility constraint.
    """

    array = np.asarray(points, dtype=float)
    if array.size == 0:
        array = np.empty((0, 2), dtype=float)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError("points must have shape (N, 2).")

    def has_point(target: tuple[float, float]) -> bool:
        return bool(
            np.any(
                np.all(
                    np.isclose(array, target, atol=tolerance, rtol=0.0),
                    axis=1,
                )
            )
        )

    return {
        "x_axis_symmetry": all(has_point((x, -y)) for x, y in array),
        "y_axis_symmetry": all(has_point((-x, y)) for x, y in array),
        "origin_symmetry": all(has_point((-x, -y)) for x, y in array),
    }
