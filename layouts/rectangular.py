"""Rectangular-grid nozzle layouts."""

from __future__ import annotations

import math

import numpy as np

from layouts._common import finalize_layout


RECTANGULAR_BASELINE_PARAMETERS = {
    "rows": 4,
    "columns": 6,
    "spacing": 18.0,
}


def rectangular_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    spacing: float,
    rows: int | None = None,
    columns: int | None = None,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Generate a deterministic grid centred on the burner origin.

    When ``rows`` and ``columns`` are provided, their product must equal N.
    Otherwise, the N points nearest the origin are selected from a centred
    square grid.  The explicit 6-by-4 mode reproduces A_Rectangular exactly.
    """

    if spacing <= 0:
        raise ValueError("spacing must be positive.")
    if (rows is None) != (columns is None):
        raise ValueError("rows and columns must be supplied together.")

    if rows is not None and columns is not None:
        if rows <= 0 or columns <= 0 or rows * columns != N:
            raise ValueError("rows * columns must equal N.")
        x_values = (np.arange(columns) - (columns - 1) / 2.0) * spacing
        y_values = (np.arange(rows) - (rows - 1) / 2.0) * spacing
        points = [(float(x), float(y)) for y in y_values for x in x_values]
    else:
        side = int(math.ceil(math.sqrt(N)))
        axis = (np.arange(side) - (side - 1) / 2.0) * spacing
        candidates = [(float(x), float(y)) for y in axis for x in axis]
        candidates.sort(
            key=lambda point: (
                round(point[0] ** 2 + point[1] ** 2, 12),
                math.atan2(point[1], point[0]),
                point[0],
                point[1],
            )
        )
        points = candidates[:N]

    return finalize_layout(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )


def rectangular_baseline(
    *, N: int, R: float, d: float, s_min: float, tolerance: float = 1e-9
) -> list[tuple[float, float]]:
    """Reproduce A_Rectangular and keep the baseline fixed at N=24."""

    if N != 24:
        raise ValueError("A_Rectangular is a fixed N=24 baseline.")
    return rectangular_layout(
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
        **RECTANGULAR_BASELINE_PARAMETERS,
    )
