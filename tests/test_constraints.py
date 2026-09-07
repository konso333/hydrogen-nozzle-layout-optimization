from __future__ import annotations

import math

import numpy as np
import pytest

from geometry.constraints import (
    LayoutConstraintError,
    as_point_array,
    boundary_check,
    ensure_layout_feasible,
    minimum_center_distance,
    overlap_check,
    spacing_check,
    validate_layout_constraints,
)
from geometry.metrics import evaluate_geometry
from geometry.symmetry import symmetry_check
from layouts import generate_layout
from validation import InputValidationError


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


@pytest.mark.parametrize("function,parameters,field", [
    (boundary_check, {"R": 55, "d": 4}, "R"),
    (boundary_check, {"R": 55, "d": 4}, "d"),
    (boundary_check, {"R": 55, "d": 4}, "tolerance"),
    (overlap_check, {"d": 4}, "d"),
    (overlap_check, {"d": 4}, "tolerance"),
    (spacing_check, {"d": 4, "s_min": 8}, "d"),
    (spacing_check, {"d": 4, "s_min": 8}, "s_min"),
    (spacing_check, {"d": 4, "s_min": 8}, "tolerance"),
    (symmetry_check, {}, "tolerance"),
    (evaluate_geometry, {"R": 55, "d": 4, "s_min": 8}, "symmetry_tolerance"),
    (validate_layout_constraints, {"N": 2, "R": 55, "d": 4, "s_min": 8}, "tolerance"),
])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_public_geometry_checks_reject_nonfinite_inputs(function, parameters, field, bad):
    with pytest.raises(InputValidationError, match="tolerance" if "tolerance" in field else field):
        function([(54, 0), (54, 0)], **{**parameters, field: bad})


@pytest.mark.parametrize("bad", [2.0, 2.9, True, False, -1, "2", math.nan, math.inf])
def test_validator_requires_integral_expected_count(bad):
    with pytest.raises(InputValidationError, match="N"):
        validate_layout_constraints([(0, 0), (10, 0)], N=bad, R=55, d=4, s_min=8)


def test_validator_keeps_optional_and_zero_counts_for_geometry_inspection():
    assert validate_layout_constraints([], N=0, R=55, d=4, s_min=8)["feasible"]
    assert validate_layout_constraints([(0, 0)], N=None, R=55, d=4, s_min=8)["feasible"]
    assert validate_layout_constraints([(0, 0)], N=np.int64(1), R=55, d=4, s_min=8)["feasible"]


def test_zero_tolerance_keeps_exact_boundary_and_nonoverlap_checks():
    assert boundary_check([(53, 0)], R=55, d=4, tolerance=0)
    assert not boundary_check([(53.000001, 0)], R=55, d=4, tolerance=0)
    assert overlap_check([(0, 0), (4, 0)], d=4, tolerance=0)
    assert not overlap_check([(0, 0), (3.999999, 0)], d=4, tolerance=0)


@pytest.mark.parametrize("points", [[[], []], np.empty((0, 3)), np.empty((2, 0))])
def test_empty_size_does_not_hide_invalid_point_shape(points):
    with pytest.raises(ValueError, match="shape"):
        as_point_array(points)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_symmetry_rejects_nonfinite_coordinates(bad):
    with pytest.raises(ValueError, match="finite"):
        symmetry_check([(bad, 0)])


@pytest.mark.parametrize("parameters,failed_flags", [
    ({"R": 0}, ["boundary_ok"]),
    ({"R": -1}, ["boundary_ok"]),
    ({"d": 0}, ["boundary_ok", "overlap_ok", "spacing_ok"]),
    ({"d": -1}, ["boundary_ok", "overlap_ok", "spacing_ok"]),
    ({"d": 111}, ["boundary_ok", "overlap_ok", "spacing_ok"]),
    ({"s_min": -1}, ["spacing_ok"]),
])
def test_finite_invalid_geometry_returns_diagnostic_report(parameters, failed_flags):
    geometry = {"R": 55, "d": 4, "s_min": 8, **parameters}
    points = [(0, 0), (10, 0)]
    report = validate_layout_constraints(points, N=2, **geometry)
    assert report["nozzle_count"] == 2
    assert report["count_ok"] is True
    assert report["feasible"] is False
    assert report["failure_reasons"]
    assert all(report[field] is False for field in failed_flags)
    assert evaluate_geometry(points, expected_N=2, **geometry)["feasible"] is False
    with pytest.raises(LayoutConstraintError):
        ensure_layout_feasible(points, N=2, **geometry)
    with pytest.raises(InputValidationError):
        generate_layout("rectangular", 2, **geometry, spacing=10)


@pytest.mark.parametrize("field", ["R", "d", "s_min", "tolerance"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_diagnostic_report_still_rejects_nonfinite_geometry(field, bad):
    geometry = {"R": 55, "d": 4, "s_min": 8, "tolerance": 1e-9, field: bad}
    with pytest.raises(InputValidationError, match=field):
        validate_layout_constraints([(0, 0)], N=1, **geometry)


def test_diagnostic_tolerance_still_requires_nonnegative_value():
    with pytest.raises(InputValidationError, match="tolerance"):
        validate_layout_constraints([(0, 0)], N=1, R=55, d=4, s_min=8, tolerance=-1)
