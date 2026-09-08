"""M4 geometry definitions, independent examples, and invariance properties."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path

import numpy as np
import pytest

from experiments import batch
from experiments.archive import CaseRequest, archive_run, verify_case, verify_run
from experiments.search_space import ExperimentSearchSpace
from experiments.spec import CaseSpec
from config import GeometryConfig
from geometry.constraints import validate_layout_constraints
from geometry.definitions import (
    CONSTRAINT_RESULT_KEYS,
    GEOMETRY_METRIC_KEYS,
    HARD_CONSTRAINT_KEYS,
    LEGACY_METRIC_KEYS,
    METRIC_DEFINITIONS,
    metric_definitions,
)
from geometry.metrics import evaluate_geometry, evaluate_geometry_metrics
from optimization.objectives import (
    DEFAULT_MAXIMIZE_OBJECTIVES,
    DEFAULT_OBJECTIVE_PROFILE,
    ObjectiveProfile,
    ObjectiveTerm,
    dominates,
    get_objective_profile,
    mark_pareto_candidates,
    objective_profiles,
)


def metrics(points, *, R=20.0, d=2.0, s_min=2.0):
    return evaluate_geometry_metrics(points, R=R, d=d, s_min=s_min)


def _write_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _replace_archived_metrics(case_path, archived_metrics):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    metrics_path = case_path.parent / case["files"]["metrics"]
    _write_json(metrics_path, archived_metrics)
    case["metrics"] = archived_metrics
    case["file_sha256"]["metrics"] = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    _write_json(case_path, case)


def test_metric_inventory_is_complete_machine_readable_and_disjoint_from_constraints():
    values = metrics([(0, 0), (3, 0), (10, 0)])
    definitions = metric_definitions()
    assert tuple(values) == GEOMETRY_METRIC_KEYS == tuple(METRIC_DEFINITIONS)
    assert tuple(definitions) == GEOMETRY_METRIC_KEYS
    assert set(GEOMETRY_METRIC_KEYS).isdisjoint(CONSTRAINT_RESULT_KEYS)
    assert set(HARD_CONSTRAINT_KEYS) < set(CONSTRAINT_RESULT_KEYS)
    for key, definition in definitions.items():
        assert definition["key"] == key
        assert definition["description"]
        assert definition["unit"] in {"mm", "dimensionless"}
        assert definition["direction"] in {"minimize", "maximize", "descriptive"}
        assert definition["category"]
        assert definition["defined_for"]
        assert definition["undefined_value"] is None


def test_composite_report_keeps_constraints_and_metric_values_separable():
    points = [(1, 1), (11, 1)]
    pure = metrics(points, R=30, d=2, s_min=5)
    validation = validate_layout_constraints(points, N=2, R=30, d=2, s_min=5)
    combined = evaluate_geometry(points, expected_N=2, R=30, d=2, s_min=5)
    assert {key: combined[key] for key in pure} == pure
    assert {key: combined[key] for key in validation} == validation
    assert validation["feasible"] is True
    assert pure["centroid_offset"] > 0
    assert pure["second_moment_anisotropy"] == pytest.approx(1)


def test_spacing_radial_centroid_and_margin_metrics_match_scalar_hand_calculation():
    points = [(0, 0), (3, 0), (10, 0)]
    result = metrics(points)
    nearest = [3.0, 3.0, 7.0]
    radii = [0.0, 3.0, 10.0]
    expected_mean = statistics.mean(nearest)
    expected_std = statistics.pstdev(nearest)
    assert result["min_center_distance"] == 3
    assert result["mean_nearest_neighbor_distance"] == pytest.approx(expected_mean)
    assert result["std_nearest_neighbor_distance"] == pytest.approx(expected_std)
    assert result["nearest_neighbor_cv"] == pytest.approx(expected_std / expected_mean)
    assert result["uniformity_score"] == pytest.approx(1 / (1 + expected_std / expected_mean))
    assert result["minimum_spacing_margin"] == 1
    assert result["minimum_boundary_margin"] == 9
    assert result["max_center_radius"] == 10
    assert result["mean_center_radius"] == pytest.approx(statistics.mean(radii))
    assert result["radial_std"] == pytest.approx(statistics.pstdev(radii))
    assert result["centroid_offset"] == pytest.approx(13 / 3)
    assert result["normalized_max_center_radius"] == pytest.approx(10 / 19)
    assert result["normalized_mean_center_radius"] == pytest.approx((13 / 3) / 19)
    assert result["normalized_radial_std"] == pytest.approx(statistics.pstdev(radii) / 19)
    assert result["normalized_centroid_offset"] == pytest.approx((13 / 3) / 19)


def test_constraint_margins_report_tolerance_band_without_redefining_pass_fail():
    tolerance = 1e-9
    points = [(0, 0), (8 - tolerance / 2, 0), (53 + tolerance / 2, 0)]
    result = evaluate_geometry(
        points, expected_N=3, R=55, d=4, s_min=8, tolerance=tolerance,
    )
    assert result["spacing_ok"] is True
    assert result["boundary_ok"] is True
    assert result["minimum_spacing_margin"] >= -tolerance
    assert result["minimum_boundary_margin"] >= -tolerance


def test_centroid_offset_covers_symmetric_translated_single_and_irregular_sets():
    square = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    shifted = [(x + 2, y - 3) for x, y in square]
    assert metrics(square)["centroid_offset"] == pytest.approx(0)
    assert metrics(shifted)["centroid_offset"] == pytest.approx(math.sqrt(13))
    single = metrics([(3, 4)])
    assert single["centroid_offset"] == 5
    assert single["normalized_centroid_offset"] == pytest.approx(5 / 19)
    assert metrics([(0, 0), (2, 0), (0, 3)])["centroid_offset"] == pytest.approx(
        math.hypot(2 / 3, 1)
    )


def test_second_moment_anisotropy_has_independent_canonical_values():
    square = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    line = [(-2, 0), (0, 0), (2, 0)]
    assert metrics(square)["second_moment_anisotropy"] == pytest.approx(0)
    assert metrics(line)["second_moment_anisotropy"] == pytest.approx(1)


def test_anisotropy_is_rotation_and_translation_invariant():
    points = np.array([[-2.0, -1.0], [3.0, -1.0], [1.0, 4.0], [-1.0, 2.0]])
    angle = 0.731
    rotation = np.array([
        [math.cos(angle), -math.sin(angle)],
        [math.sin(angle), math.cos(angle)],
    ])
    original = metrics(points)["second_moment_anisotropy"]
    rotated = metrics(points @ rotation.T)["second_moment_anisotropy"]
    translated = metrics(points + [8.0, -11.0])["second_moment_anisotropy"]
    assert rotated == pytest.approx(original, abs=1e-14)
    assert translated == pytest.approx(original, abs=1e-14)


def test_metric_scale_behavior_matches_metadata():
    points = [(-3.0, -1.0), (2.0, -2.0), (4.0, 5.0), (-1.0, 3.0)]
    scale = 3.5
    original = evaluate_geometry_metrics(points, R=20, d=2, s_min=2)
    scaled = evaluate_geometry_metrics(
        [(scale * x, scale * y) for x, y in points],
        R=scale * 20, d=scale * 2, s_min=scale * 2,
        symmetry_tolerance=scale * 1e-6,
    )
    for key, definition in METRIC_DEFINITIONS.items():
        if isinstance(original[key], bool):
            assert scaled[key] is original[key], key
        elif definition.scale_behavior == "length-dependent":
            assert scaled[key] == pytest.approx(scale * original[key]), key
        else:
            assert scaled[key] == pytest.approx(original[key]), key


def test_metrics_are_permutation_invariant():
    points = [(-3.0, -1.0), (2.0, -2.0), (4.0, 5.0), (-1.0, 3.0)]
    original = metrics(points)
    permuted = metrics([points[index] for index in (2, 0, 3, 1)])
    assert permuted.keys() == original.keys()
    for key in original:
        if isinstance(original[key], float):
            assert permuted[key] == pytest.approx(original[key]), key
        else:
            assert permuted[key] == original[key], key


def test_metrics_marked_rotation_invariant_remain_unchanged():
    points = np.array([[-3.0, -1.0], [2.0, -2.0], [4.0, 5.0], [-1.0, 3.0]])
    angle = math.pi / 7
    rotation = np.array([
        [math.cos(angle), -math.sin(angle)],
        [math.sin(angle), math.cos(angle)],
    ])
    original = metrics(points)
    rotated = metrics(points @ rotation.T)
    for key, definition in METRIC_DEFINITIONS.items():
        if not definition.rotation_invariant:
            continue
        if isinstance(original[key], bool):
            assert rotated[key] is original[key], key
        else:
            assert rotated[key] == pytest.approx(original[key], abs=1e-14), key


def test_origin_symmetry_metadata_accounts_for_componentwise_tolerance_orientation():
    tolerance = 1e-6
    points = np.array([[1.0, 0.0], [-1.0 + 0.9 * tolerance, 0.9 * tolerance]])
    angle = math.pi / 4
    rotation = np.array([
        [math.cos(angle), -math.sin(angle)],
        [math.sin(angle), math.cos(angle)],
    ])
    original = evaluate_geometry_metrics(
        points, R=20, d=2, s_min=2, symmetry_tolerance=tolerance,
    )
    rotated = evaluate_geometry_metrics(
        points @ rotation.T, R=20, d=2, s_min=2, symmetry_tolerance=tolerance,
    )
    assert original["origin_symmetry"] is True
    assert rotated["origin_symmetry"] is False
    assert all(
        METRIC_DEFINITIONS[key].rotation_invariant is False
        for key in ("x_axis_symmetry", "y_axis_symmetry", "origin_symmetry")
    )


@pytest.mark.parametrize("points", [[], [(3, 4)], [(0, 0), (0, 0)]])
def test_undefined_metrics_use_none_and_never_nonfinite_numbers(points):
    result = metrics(points)
    assert all(
        value is None or isinstance(value, (bool, int))
        or isinstance(value, float) and math.isfinite(value)
        for value in result.values()
    )
    if len(points) < 2:
        for key in (
            "min_center_distance", "mean_nearest_neighbor_distance",
            "std_nearest_neighbor_distance", "nearest_neighbor_cv",
            "uniformity_score", "minimum_spacing_margin",
            "second_moment_anisotropy",
        ):
            assert result[key] is None
    if len(points) == 2:
        assert result["min_center_distance"] == 0
        assert result["nearest_neighbor_cv"] is None
        assert result["uniformity_score"] is None
        assert result["second_moment_anisotropy"] is None


def test_nonpositive_usable_radius_makes_only_normalized_radius_metrics_undefined():
    result = metrics([(0, 0)], R=1, d=4, s_min=2)
    assert result["centroid_offset"] == 0
    assert result["minimum_boundary_margin"] == -1
    for key in (
        "normalized_max_center_radius", "normalized_mean_center_radius",
        "normalized_radial_std", "normalized_centroid_offset",
    ):
        assert result[key] is None


def test_legacy_objective_profile_is_explicit_queryable_and_unchanged():
    profile = get_objective_profile()
    assert profile is get_objective_profile("legacy") is DEFAULT_OBJECTIVE_PROFILE
    assert tuple(term.key for term in profile.objectives) == DEFAULT_MAXIMIZE_OBJECTIVES
    assert [term.direction for term in profile.objectives] == ["maximize"] * 3
    assert [term.source for term in profile.objectives] == [
        "case_descriptor", "metric", "metric",
    ]
    assert objective_profiles()["legacy"] == profile.to_dict()
    for term in profile.objectives[1:]:
        definition = METRIC_DEFINITIONS[term.key]
        assert definition.optimization_eligible
        assert definition.direction == term.direction


def test_objective_profile_supports_minimize_direction_and_rejects_none():
    profile = ObjectiveProfile(
        "spacing_cv_example",
        (ObjectiveTerm("nearest_neighbor_cv", "minimize"),),
        "Test-only opt-in profile.",
    )
    low = {"nearest_neighbor_cv": 0.1}
    high = {"nearest_neighbor_cv": 0.3}
    missing = {"nearest_neighbor_cv": None}
    assert dominates(low, high, profile)
    assert not dominates(high, low, profile)
    assert [row["pareto_candidate"] for row in mark_pareto_candidates(
        [low, high, missing], profile,
    )] == [True, False, False]
    with pytest.raises(ValueError, match="not optimization-eligible"):
        ObjectiveProfile(
            "invalid_descriptive",
            (ObjectiveTerm("radial_std", "minimize"),),
            "A descriptive metric cannot silently become an objective.",
        )


def test_m2_current_and_exact_legacy_archives_verify_but_mixed_metric_keys_fail(tmp_path):
    request = CaseRequest(
        CaseSpec.create("rectangular", 1, GeometryConfig(), {"spacing": 10})
    )
    run_path = archive_run([request], name="M4 metric compatibility", output_root=tmp_path)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    case_path = run_path.parent / run["cases"][0]["manifest"]
    current = json.loads(case_path.read_text(encoding="utf-8"))["metrics"]

    assert set(current) == set(GEOMETRY_METRIC_KEYS)
    assert len(current) == 19
    assert current["min_center_distance"] is None
    assert verify_case(case_path)["overall_match"] is True
    assert verify_run(run_path)["overall_match"] is True

    legacy = {key: current[key] for key in LEGACY_METRIC_KEYS}
    assert len(legacy) == 11
    _replace_archived_metrics(case_path, legacy)
    assert verify_case(case_path)["overall_match"] is True
    assert verify_run(run_path)["overall_match"] is True

    mixed = {**legacy, "centroid_offset": current["centroid_offset"]}
    _replace_archived_metrics(case_path, mixed)
    mixed_case_check = verify_case(case_path)
    mixed_run_check = verify_run(run_path)
    assert mixed_case_check["metrics_match"] is False
    assert mixed_case_check["overall_match"] is False
    assert mixed_run_check["case_checks"][0]["metrics_match"] is False
    assert mixed_run_check["overall_match"] is False


def test_m3_small_search_exposes_m4_metrics_without_changing_feasibility_counts():
    data = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/m3_small_search.json")
        .read_text(encoding="utf-8")
    )
    report = batch.run_batch(ExperimentSearchSpace.from_dict(data))
    assert (report["planned"], report["unique"], report["feasible"], report["infeasible"]) == (
        8, 8, 8, 0,
    )
    assert all(set(GEOMETRY_METRIC_KEYS) <= set(row) for row in report["rows"])
