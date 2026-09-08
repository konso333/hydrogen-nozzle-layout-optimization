"""Literature-backed five-nozzle cross topology."""

from __future__ import annotations

from layouts._common import finalize_layout, validate_layout_inputs
from validation import InputValidationError, require_finite


def cross_5_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    pitch: float,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Place one centre and four axis-aligned neighbours at pitch ``p``.

    The five-point cross topology is literature-backed. ``pitch`` remains a
    project-controlled geometry parameter; this generator does not infer it
    from a paper figure or extend the topology to other nozzle counts.
    """

    validate_layout_inputs(N=N, R=R, d=d, s_min=s_min, tolerance=tolerance)
    if N != 5:
        raise InputValidationError("cross_5 is defined only for N=5.")
    pitch = require_finite(pitch, "pitch")
    if pitch <= 0:
        raise InputValidationError("pitch must be positive.")

    points = [
        (0.0, 0.0),
        (pitch, 0.0),
        (-pitch, 0.0),
        (0.0, pitch),
        (0.0, -pitch),
    ]
    return finalize_layout(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )
