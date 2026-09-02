"""Shared finalization for deterministic layout generators."""

from __future__ import annotations

from geometry.constraints import ensure_layout_feasible


def finalize_layout(
    points,
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    tolerance: float,
) -> list[tuple[float, float]]:
    if not isinstance(N, int) or isinstance(N, bool) or N <= 0:
        raise ValueError("N must be a positive integer.")
    return ensure_layout_feasible(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
