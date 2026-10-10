"""Generate one engineering layout using existing, reproducible generators."""

from __future__ import annotations

import numpy as np

from engineering_config import EngineeringGeometryConfig
from engineering_geometry import to_validation_geometry, validate_engineering_layout
from experiments.spec import CaseSpec
from geometry.constraints import LayoutConstraintError, as_point_array


def generate_engineering_layout(
    *, config: EngineeringGeometryConfig, layout_type: str, N: int,
    parameters: dict | None = None, tolerance: float = 1e-9,
) -> tuple[np.ndarray, dict[str, object]]:
    """Return validated centres in mm and a complete geometry-only snapshot.

    Missing engineering dimensions are never replaced with legacy defaults.
    geometry_case_id identifies the effective geometry specification only;
    retain configuration too, since distinct physical profiles can have the
    same installation_radius_mm minus wall_clearance_mm.
    """
    geometry = to_validation_geometry(config, tolerance=tolerance)
    spec = CaseSpec.create(layout_type, N, geometry, parameters)
    points = as_point_array(spec.generate())
    report = validate_engineering_layout(points, config=config, N=N, tolerance=tolerance)
    if not report["validation"]["feasible"]:
        raise LayoutConstraintError("; ".join(report["validation"]["failure_reasons"]))
    return points, {
        "schema_version": 1,
        "kind": "engineering_layout_preview",
        "geometry_case_id": spec.case_id,
        "geometry_case_spec": spec.normalized_spec,
        **report,
    }
