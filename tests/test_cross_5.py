"""Independent mathematical, M1, M2, M3, and M4 checks for cross_5."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from config import GeometryConfig
from experiments.archive import CaseRequest, archive_run, verify_run
from experiments.search_space import ExperimentSearchSpace, default_search_space
from experiments.spec import CaseSpec
from geometry.constraints import LayoutConstraintError, minimum_center_distance
from geometry.definitions import GEOMETRY_METRIC_KEYS
from geometry.metrics import evaluate_geometry_metrics
from layouts import LAYOUT_REGISTRY, generate_layout
from optimization.layout_search import default_layout_specs
from validation import InputValidationError


EXPECTED_PITCH_10 = [
    (0.0, 0.0),
    (10.0, 0.0),
    (-10.0, 0.0),
    (0.0, 10.0),
    (0.0, -10.0),
]


def test_cross_5_has_exact_order_count_centroid_and_repeatability():
    first = generate_layout("cross_5", 5, 55, 4, 8, pitch=10)
    second = generate_layout("cross_5", 5, 55, 4, 8, pitch=10)
    direct = LAYOUT_REGISTRY["cross_5"](N=5, R=55, d=4, s_min=8, pitch=10)
    assert first == second == direct == EXPECTED_PITCH_10
    assert len(first) == 5
    assert tuple(np.mean(np.asarray(first), axis=0)) == pytest.approx((0.0, 0.0))


@pytest.mark.parametrize("N", [1, 4, 6, 7])
def test_cross_5_rejects_every_other_integer_count(N):
    with pytest.raises(InputValidationError, match="only for N=5"):
        generate_layout("cross_5", N, 55, 4, 8, pitch=10)


@pytest.mark.parametrize("N", [5.0, True, False, "5", None, math.nan, math.inf])
def test_cross_5_keeps_m1_strict_count_contract(N):
    with pytest.raises(InputValidationError, match="N"):
        generate_layout("cross_5", N, 55, 4, 8, pitch=10)


@pytest.mark.parametrize("pitch", [0, -1, math.nan, math.inf, -math.inf])
def test_cross_5_rejects_nonpositive_or_nonfinite_pitch(pitch):
    with pytest.raises(InputValidationError, match="pitch"):
        generate_layout("cross_5", 5, 55, 4, 8, pitch=pitch)


def test_cross_5_reuses_hard_constraints():
    points = generate_layout("cross_5", 5, 20, 4, 8, pitch=10)
    assert minimum_center_distance(points) == pytest.approx(10)
    with pytest.raises(LayoutConstraintError, match="minimum centre distance"):
        generate_layout("cross_5", 5, 20, 4, 8, pitch=7)
    with pytest.raises(LayoutConstraintError, match="circular boundary"):
        generate_layout("cross_5", 5, 10, 4, 8, pitch=9)


def test_cross_5_m4_metrics_match_scalar_mathematics_and_rotation():
    pitch = 10.0
    points = EXPECTED_PITCH_10
    values = evaluate_geometry_metrics(points, R=55, d=4, s_min=8)
    assert tuple(values) == GEOMETRY_METRIC_KEYS
    assert len(values) == 19
    assert values["min_center_distance"] == pytest.approx(pitch)
    assert values["mean_nearest_neighbor_distance"] == pytest.approx(pitch)
    assert values["std_nearest_neighbor_distance"] == pytest.approx(0)
    assert values["nearest_neighbor_cv"] == pytest.approx(0)
    assert values["uniformity_score"] == pytest.approx(1)
    assert values["mean_center_radius"] == pytest.approx(4 * pitch / 5)
    assert values["radial_std"] == pytest.approx(2 * pitch / 5)
    assert values["centroid_offset"] == pytest.approx(0)
    assert values["second_moment_anisotropy"] == pytest.approx(0)
    assert values["x_axis_symmetry"] is True
    assert values["y_axis_symmetry"] is True
    assert values["origin_symmetry"] is True

    angle = 0.37
    rotated = [
        (x * math.cos(angle) - y * math.sin(angle),
         x * math.sin(angle) + y * math.cos(angle))
        for x, y in points
    ]
    rotated_values = evaluate_geometry_metrics(rotated, R=55, d=4, s_min=8)
    for key in (
        "min_center_distance", "mean_nearest_neighbor_distance",
        "std_nearest_neighbor_distance", "nearest_neighbor_cv", "uniformity_score",
        "mean_center_radius", "radial_std", "centroid_offset",
        "second_moment_anisotropy",
    ):
        assert rotated_values[key] == pytest.approx(values[key], abs=1e-14), key


def test_cross_5_pitch_and_topology_both_participate_in_case_identity_and_json():
    config = GeometryConfig()
    pitch_10 = CaseSpec.create("cross_5", 5, config, {"pitch": 10})
    pitch_12 = CaseSpec.create("cross_5", 5, config, {"pitch": 12})
    restored = CaseSpec.from_normalized(
        json.loads(json.dumps(pitch_10.normalized_spec, ensure_ascii=False))
    )
    assert pitch_10.case_id != pitch_12.case_id
    assert pitch_10.normalized_spec["layout_type"] == "cross_5"
    assert pitch_10.normalized_spec["generator_type"] == "cross_5"
    assert pitch_10.normalized_spec["N"] == 5
    assert pitch_10.normalized_spec["layout_parameters"] == {"pitch": 10}
    assert restored.case_id == pitch_10.case_id
    assert restored.generate() == EXPECTED_PITCH_10


def test_cross_5_m2_archive_round_trip(tmp_path):
    request = CaseRequest(CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10}))
    run_path = archive_run([request], name="M5 cross_5", output_root=tmp_path)
    result = verify_run(run_path)
    assert result["overall_match"] is True
    case = json.loads(
        (run_path.parent / "cases" / request.spec.case_id / "case.json")
        .read_text(encoding="utf-8")
    )
    assert case["layout_type"] == "cross_5"
    assert len(case["metrics"]) == 19


def test_cross_5_is_m3_opt_in_and_default_search_is_unchanged():
    design = {
        "schema_version": 1,
        "name": "M5 explicit cross_5",
        "blocks": [{
            "geometry_space": {"R": [55], "d": [4], "s_min": [8], "tolerance": [1e-9]},
            "groups": [{"N": 5, "layout_type": "cross_5",
                        "parameter_sets": [{"pitch": 10}, {"pitch": 12}]}],
        }],
    }
    plan = ExperimentSearchSpace.from_dict(design).plan()
    assert plan.planned == plan.unique == 2
    assert all(request.spec.normalized_spec["layout_type"] == "cross_5"
               for request in plan.requests)
    assert default_search_space().plan().planned == 426
    assert all(spec.layout_type != "cross_5"
               for N in range(12, 41) for spec in default_layout_specs(N, GeometryConfig()))


@pytest.mark.parametrize("N", [4, 6])
def test_m3_preflight_rejects_cross_5_with_wrong_count(N):
    design = {
        "schema_version": 1,
        "name": "invalid cross",
        "blocks": [{
            "geometry_space": {"R": [55], "d": [4], "s_min": [8], "tolerance": [1e-9]},
            "groups": [{"N": N, "layout_type": "cross_5",
                        "parameter_sets": [{"pitch": 10}]}],
        }],
    }
    with pytest.raises(InputValidationError, match="N must equal 5"):
        ExperimentSearchSpace.from_dict(design)
