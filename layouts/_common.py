"""Shared finalization for deterministic layout generators."""

from __future__ import annotations

from config import GeometryConfig
from geometry.constraints import ensure_layout_feasible
from validation import require_count


def validate_layout_inputs(*, N: int, R: float, d: float, s_min: float, tolerance: float) -> None:
    """Validate common inputs before any grid allocation or trigonometry."""

    require_count(N, "N")
    GeometryConfig(R=R, d=d, s_min=s_min, tolerance=tolerance)


def finalize_layout(
    points,
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    tolerance: float,
) -> list[tuple[float, float]]:
    require_count(N, "N")
    return ensure_layout_feasible(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
