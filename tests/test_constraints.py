from __future__ import annotations

import math

from geometry.constraints import (
    boundary_check,
    minimum_center_distance,
    spacing_check,
)
from geometry.metrics import evaluate_geometry


def test_minimum_center_distance_is_euclidean() -> None:
    assert minimum_center_distance([(0, 0), (3, 4), (20, 0)]) == 5.0


def test_boundary_checks_full_nozzle_radius() -> None:
    assert boundary_check([(53.0, 0.0)], R=55.0, d=4.0)
    assert not boundary_check([(53.01, 0.0)], R=55.0, d=4.0)


def test_spacing_enforces_non_overlap_even_when_s_min_is_smaller() -> None:
    assert not spacing_check([(0, 0), (3.9, 0)], d=4.0, s_min=2.0)
    assert spacing_check([(0, 0), (4.0, 0)], d=4.0, s_min=2.0)


def test_uniformity_formula_uses_nearest_neighbor_cv() -> None:
    metrics = evaluate_geometry(
        [(0, 0), (10, 0), (20, 0)],
        R=30,
        d=2,
        s_min=2,
        expected_N=3,
    )
    assert math.isclose(metrics["nearest_neighbor_cv"], 0.0)
    assert math.isclose(metrics["uniformity_score"], 1.0)
