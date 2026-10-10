"""Apply engineering edge clearances through the existing geometry validator."""

from __future__ import annotations

import math
from dataclasses import asdict

from config import GeometryConfig
from engineering_config import EngineeringGeometryConfig
from geometry.constraints import (
    as_point_array,
    minimum_center_distance,
    validate_layout_constraints,
)
from validation import require_count


def to_validation_geometry(
    config: EngineeringGeometryConfig, *, tolerance: float = 1e-9,
) -> GeometryConfig:
    """Shrink the validation region, keeping the physical nozzle diameter.

    The returned R is installation_radius_mm minus wall_clearance_mm, not the
    actual installation radius. Keep the original engineering config with it.
    """
    config.require_complete()
    return GeometryConfig(
        R=config.installation_radius_mm - config.wall_clearance_mm,
        d=config.nozzle_outer_diameter_mm,
        s_min=config.nozzle_outer_diameter_mm + config.nozzle_edge_gap_mm,
        tolerance=tolerance,
    )


def validate_engineering_layout(
    points, *, config: EngineeringGeometryConfig, N: int, tolerance: float = 1e-9,
) -> dict[str, object]:
    """Validate existing planar centres in mm; do not generate or save layouts.

    boundary_ok includes the requested wall clearance; overlap_ok still checks
    only the physical diameter. Clearance margins are raw values: small negative
    margins within the recorded numerical tolerance can pass the validator.
    """
    geometry = to_validation_geometry(config, tolerance=tolerance)
    require_count(N, "N")
    array = as_point_array(points)
    validation = validate_layout_constraints(array, N=N, **asdict(geometry))
    distance = minimum_center_distance(array)
    max_radius = max((math.hypot(float(x), float(y)) for x, y in array), default=None)
    edge_gap = distance - geometry.d if math.isfinite(distance) else None
    wall_clearance = (
        config.installation_radius_mm - geometry.d / 2 - max_radius
        if max_radius is not None and math.isfinite(max_radius) else None
    )
    return {
        **config.inspect(),
        "validation_geometry": asdict(geometry),
        "validation": validation,
        "measured_clearances_mm": {
            "minimum_nozzle_edge_gap": edge_gap,
            "minimum_wall_clearance": wall_clearance,
            "nozzle_edge_gap_margin": (
                edge_gap - config.nozzle_edge_gap_mm if edge_gap is not None else None
            ),
            "wall_clearance_margin": (
                wall_clearance - config.wall_clearance_mm
                if wall_clearance is not None else None
            ),
        },
    }
