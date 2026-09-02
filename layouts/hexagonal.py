"""Triangular-lattice (hexagonal packing projection) layouts."""

from __future__ import annotations

import math

import numpy as np

from layouts._common import finalize_layout


HEXAGONAL_BASELINE_PARAMETERS = {
    "row_counts": [4, 5, 6, 5, 4],
    "spacing": 16.0,
}


def hexagonal_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    spacing: float,
    row_counts: list[int] | tuple[int, ...] | None = None,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Generate points on a deterministic triangular lattice.

    ``row_counts=[4, 5, 6, 5, 4]`` reproduces B_Hexagonal.  Without explicit
    row counts, the N lattice sites nearest the origin are selected.
    """

    if spacing <= 0:
        raise ValueError("spacing must be positive.")
    dy = math.sqrt(3.0) * spacing / 2.0

    if row_counts is not None:
        counts = [int(value) for value in row_counts]
        if any(value <= 0 for value in counts) or sum(counts) != N:
            raise ValueError("row_counts must contain positive integers summing to N.")
        y_values = (np.arange(len(counts)) - (len(counts) - 1) / 2.0) * dy
        points = []
        for count, y in zip(counts, y_values):
            x_values = (np.arange(count) - (count - 1) / 2.0) * spacing
            points.extend((float(x), float(y)) for x in x_values)
    else:
        allowed_radius = R - d / 2.0
        extent = int(math.ceil(allowed_radius / min(spacing, dy))) + 2
        candidates: list[tuple[float, float]] = []
        for row in range(-extent, extent + 1):
            y = row * dy
            offset = 0.5 * spacing if row % 2 else 0.0
            for column in range(-extent, extent + 1):
                x = column * spacing + offset
                if math.hypot(x, y) <= allowed_radius + tolerance:
                    candidates.append((float(x), float(y)))
        candidates.sort(
            key=lambda point: (
                round(point[0] ** 2 + point[1] ** 2, 12),
                math.atan2(point[1], point[0]),
                point[0],
                point[1],
            )
        )
        if len(candidates) < N:
            raise ValueError(
                f"Only {len(candidates)} triangular-lattice sites fit, fewer than N={N}."
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


def hexagonal_baseline(
    *, N: int, R: float, d: float, s_min: float, tolerance: float = 1e-9
) -> list[tuple[float, float]]:
    """Reproduce B_Hexagonal and keep the baseline fixed at N=24."""

    if N != 24:
        raise ValueError("B_Hexagonal is a fixed N=24 baseline.")
    return hexagonal_layout(
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
        **HEXAGONAL_BASELINE_PARAMETERS,
    )
