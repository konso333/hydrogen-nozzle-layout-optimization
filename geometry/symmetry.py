"""Symmetry metrics for two-dimensional nozzle point sets."""

from __future__ import annotations

import numpy as np

from geometry.constraints import as_point_array
from validation import require_tolerance


def symmetry_check(points, tolerance: float = 1e-6) -> dict[str, bool]:
    """Check reflection about both axes and 180-degree rotation.

    Symmetry is reported as a metric.  It is not a hard feasibility constraint.
    """

    require_tolerance(tolerance)
    array = as_point_array(points)

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
