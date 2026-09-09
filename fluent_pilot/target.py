"""Select and verify the existing M5 case; no alternative geometry generator."""

import math
from pathlib import Path

import numpy as np

from experiments.search_space import ExperimentSearchSpace
from geometry.constraints import minimum_center_distance, validate_layout_constraints


def select_pilot_request():
    path = Path(__file__).resolve().parents[1] / "examples/m5_hex7_spacing_study.json"
    requests = ExperimentSearchSpace.load(path).plan().requests
    selected = [r for r in requests if r.spec.normalized_spec["layout_type"] == "hexagonal"
                and r.spec.normalized_spec["N"] == 7
                and r.spec.normalized_spec["layout_parameters"]["spacing"] == 10
                and r.spec.normalized_spec["geometry"]["d"] == 4]
    if len(selected) != 1:
        raise ValueError("Expected exactly one existing M5 H1 hex7 S10 d4 case")
    request = selected[0]
    validate_pilot_points(request.spec.generate(), request.spec.normalized_spec)
    return request


def validate_pilot_points(points, spec):
    points = np.asarray(points, dtype=float)
    if points.shape != (7, 2) or not np.isfinite(points).all():
        raise ValueError("Pilot requires seven finite 2D centers")
    radii = np.linalg.norm(points, axis=1)
    center = np.isclose(radii, 0, rtol=0, atol=1e-9)
    ring = points[~center]
    if center.sum() != 1 or not np.allclose(radii[~center], 10, rtol=0, atol=1e-9):
        raise ValueError("Pilot requires one center and six peripheral centers at S=10 mm")
    angles = np.sort(np.arctan2(ring[:, 1], ring[:, 0]) % (2 * math.pi))
    gaps = np.diff(np.r_[angles, angles[0] + 2 * math.pi])
    validation = validate_layout_constraints(points, N=7, **spec["geometry"])
    if (not validation["feasible"] or not np.allclose(gaps, math.pi / 3, rtol=0, atol=1e-9)
            or not math.isclose(minimum_center_distance(points), 10, abs_tol=1e-9)
            or spec["geometry"]["d"] != 4):
        raise ValueError("Pilot hexagonal spacing, diameter or geometry validation failed")
    return {"N": 7, "center_count": 1, "peripheral_count": 6,
            "spacing_mm": 10, "d_mm": 4, "S_over_d": 2.5,
            "reference": "H1", "scope": "M5 two-dimensional geometry mapping only",
            "validation": validation}
