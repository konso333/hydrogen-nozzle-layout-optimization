"""Concentric, staggered, and radially nonuniform ring layouts."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from layouts._common import finalize_layout, validate_layout_inputs
from validation import InputValidationError, require_counts, require_finite


DOUBLE_RING_BASELINE_PARAMETERS = {
    "ring_radii": [25.0, 48.0],
    "points_per_ring": [8, 16],
}

TRIPLE_RING_BASELINE_PARAMETERS = {
    "ring_radii": [25.0, 48.0],
    "points_per_ring": [7, 16],
    "include_center": True,
}


def _finite_float(value, name: str) -> float:
    """Preserve float-convertible continuous parameters without allowing NaN/Inf."""

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputValidationError(f"{name} must convert to a finite float.") from exc
    return require_finite(result, name)


def _validate_ring_inputs(
    ring_radii: Sequence[float],
    points_per_ring: Sequence[int],
) -> tuple[list[float], list[int]]:
    radii = [_finite_float(value, f"ring_radii[{index}]") for index, value in enumerate(ring_radii)]
    counts = require_counts(points_per_ring, "points_per_ring")
    if not radii or len(radii) != len(counts):
        raise InputValidationError("ring_radii and points_per_ring must be non-empty and equal length.")
    if any(radius < 0 for radius in radii):
        raise InputValidationError("ring radii cannot be negative.")
    if any(radius == 0 and count > 1 for radius, count in zip(radii, counts)):
        raise InputValidationError("A zero-radius ring can contain only one point.")
    return radii, counts


def _ring_points(
    ring_radii: Sequence[float],
    points_per_ring: Sequence[int],
    offsets: Sequence[float],
    include_center: bool,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = [(0.0, 0.0)] if include_center else []
    for radius, count, offset in zip(ring_radii, points_per_ring, offsets):
        for index in range(count):
            angle = offset + 2.0 * math.pi * index / count
            points.append((radius * math.cos(angle), radius * math.sin(angle)))
    return points


def ring_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    ring_radii: Sequence[float],
    points_per_ring: Sequence[int],
    angular_offset: float = 0.0,
    ring_offsets: Sequence[float] | None = None,
    include_center: bool = False,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Generate rings with float-convertible radii/phases and strict integer counts.

    include_center accepts Python bool and NumPy bool scalars only.
    """

    validate_layout_inputs(N=N, R=R, d=d, s_min=s_min, tolerance=tolerance)
    angular_offset = _finite_float(angular_offset, "angular_offset")
    if not isinstance(include_center, (bool, np.bool_)):
        raise InputValidationError("include_center must be a Python or NumPy bool scalar.")
    include_center = bool(include_center)
    radii, counts = _validate_ring_inputs(ring_radii, points_per_ring)
    expected = sum(counts) + int(include_center)
    if expected != N:
        raise InputValidationError(f"Ring counts plus centre equal {expected}, not N={N}.")
    if ring_offsets is None:
        offsets = [float(angular_offset)] * len(radii)
    else:
        if len(ring_offsets) != len(radii):
            raise InputValidationError("ring_offsets must match ring_radii length.")
        offsets = [float(angular_offset) + _finite_float(value, f"ring_offsets[{index}]")
                   for index, value in enumerate(ring_offsets)]
    points = _ring_points(radii, counts, offsets, include_center)
    return finalize_layout(
        points,
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
    )


def staggered_ring_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    ring_radii: Sequence[float],
    points_per_ring: Sequence[int],
    delta_theta: float,
    angular_offset: float = 0.0,
    include_center: bool = False,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Rotate each successive ring by ``delta_theta`` relative to the previous."""

    delta_theta = _finite_float(delta_theta, "delta_theta")
    offsets = [index * float(delta_theta) for index in range(len(ring_radii))]
    return ring_layout(
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        ring_radii=ring_radii,
        points_per_ring=points_per_ring,
        angular_offset=angular_offset,
        ring_offsets=offsets,
        include_center=include_center,
        tolerance=tolerance,
    )


def nonuniform_ring_layout(
    *,
    N: int,
    R: float,
    d: float,
    s_min: float,
    points_per_ring: Sequence[int],
    ring_radii: Sequence[float] | None = None,
    inner_radius: float | None = None,
    outer_radius: float | None = None,
    radial_exponent: float = 1.0,
    angular_offset: float = 0.0,
    ring_offsets: Sequence[float] | None = None,
    include_center: bool = False,
    tolerance: float = 1e-9,
) -> list[tuple[float, float]]:
    """Generate rings with explicit or power-law radial spacing.

    If ``ring_radii`` is omitted, radius k is computed from a normalized power
    law between ``inner_radius`` and ``outer_radius``.  This parameterizes
    nonuniform radial gaps without claiming any physical optimum.
    """

    validate_layout_inputs(N=N, R=R, d=d, s_min=s_min, tolerance=tolerance)
    counts = require_counts(points_per_ring, "points_per_ring")
    radial_exponent = _finite_float(radial_exponent, "radial_exponent")
    if inner_radius is not None:
        inner_radius = _finite_float(inner_radius, "inner_radius")
    if outer_radius is not None:
        outer_radius = _finite_float(outer_radius, "outer_radius")
    if ring_radii is None:
        if inner_radius is None or outer_radius is None:
            raise InputValidationError(
                "Provide ring_radii or both inner_radius and outer_radius."
            )
        if radial_exponent <= 0:
            raise InputValidationError("radial_exponent must be positive.")
        if outer_radius < inner_radius:
            raise InputValidationError("outer_radius must be at least inner_radius.")
        normalized = np.linspace(0.0, 1.0, len(counts))
        radii = inner_radius + (outer_radius - inner_radius) * normalized**radial_exponent
        ring_radii = [float(value) for value in radii]

    return ring_layout(
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        ring_radii=ring_radii,
        points_per_ring=counts,
        angular_offset=angular_offset,
        ring_offsets=ring_offsets,
        include_center=include_center,
        tolerance=tolerance,
    )


def double_ring_baseline(
    *, N: int, R: float, d: float, s_min: float, tolerance: float = 1e-9
) -> list[tuple[float, float]]:
    """Reproduce C_Double_Ring: 8 points at 25 mm and 16 at 48 mm."""

    if N != 24:
        raise InputValidationError("C_Double_Ring is a fixed N=24 baseline.")
    return ring_layout(
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
        **DOUBLE_RING_BASELINE_PARAMETERS,
    )


def triple_ring_baseline(
    *, N: int, R: float, d: float, s_min: float, tolerance: float = 1e-9
) -> list[tuple[float, float]]:
    """Reproduce D_Triple_Ring: centre plus 7 and 16 point rings."""

    if N != 24:
        raise InputValidationError("D_Triple_Ring is a fixed N=24 baseline.")
    return ring_layout(
        N=N,
        R=R,
        d=d,
        s_min=s_min,
        tolerance=tolerance,
        **TRIPLE_RING_BASELINE_PARAMETERS,
    )
