"""M5 H1 geometry cases and mixed M2/M3 integration."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from experiments.archive import verify_run
from experiments.batch import run_batch
from experiments.search_space import ExperimentSearchSpace
from geometry.constraints import minimum_center_distance


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_h1_hex7_example_matches_independent_regular_hexagon_mathematics():
    space = ExperimentSearchSpace.load(PROJECT_ROOT / "examples/m5_hex7_spacing_study.json")
    plan = space.plan()
    assert plan.planned == plan.unique == 3
    assert plan.duplicates == 0

    for request, spacing, expected_ratio in zip(
        plan.requests, (8.0, 10.0, 12.0), (2.0, 2.5, 3.0),
    ):
        spec = request.spec.normalized_spec
        points = request.spec.generate()
        height = math.sqrt(3.0) * spacing / 2.0
        expected = [
            (0.0, 0.0),
            (-spacing / 2.0, -height),
            (spacing / 2.0, -height),
            (spacing, 0.0),
            (spacing / 2.0, height),
            (-spacing / 2.0, height),
            (-spacing, 0.0),
        ]
        assert spec["layout_type"] == "hexagonal"
        assert spec["N"] == 7
        assert spec["geometry"]["d"] == 4
        assert spec["layout_parameters"]["spacing"] == spacing
        np.testing.assert_allclose(points, expected, rtol=0, atol=1e-12)
        assert minimum_center_distance(points) == pytest.approx(spacing)
        assert spacing / spec["geometry"]["d"] == pytest.approx(expected_ratio)
        radii = [math.hypot(x, y) for x, y in points]
        assert radii[0] == 0
        assert radii[1:] == pytest.approx([spacing] * 6)
        angles = sorted(math.atan2(y, x) % (2 * math.pi) for x, y in points[1:])
        gaps = [
            (angles[(index + 1) % 6] - angles[index]) % (2 * math.pi)
            for index in range(6)
        ]
        assert gaps == pytest.approx([math.pi / 3] * 6)

    report = run_batch(space)
    assert (report["planned"], report["unique"], report["feasible"],
            report["infeasible"]) == (3, 3, 3, 0)


def test_mixed_cross_and_literature_backed_hex_plan_generate_evaluate_archive_verify(tmp_path):
    space = ExperimentSearchSpace.from_dict({
        "schema_version": 1,
        "name": "M5 cross and H1 integration",
        "description": "Geometry-only integration test.",
        "blocks": [{
            "geometry_space": {"R": [55], "d": [4], "s_min": [8], "tolerance": [1e-9]},
            "groups": [
                {"N": 5, "layout_type": "cross_5", "parameter_sets": [{"pitch": 10}]},
                {"N": 7, "layout_type": "hexagonal", "parameter_sets": [{"spacing": 10}]},
            ],
        }],
    })
    report = run_batch(space, output_root=tmp_path)
    assert (report["planned"], report["unique"], report["feasible"],
            report["infeasible"]) == (2, 2, 2, 0)
    assert {row["layout_type"] for row in report["rows"]} == {"cross_5", "hexagonal"}
    assert all(len({key for key in row if key not in {
        "case_id", "legacy_candidate_id", "N", "layout_type", "pareto_candidate",
        "nozzle_count", "count_ok", "boundary_ok", "overlap_ok", "spacing_ok",
        "feasible", "failure_reasons",
    }}) == 19 for row in report["rows"])
    run_path = tmp_path / report["run_id"] / report["run_manifest"]
    assert verify_run(run_path)["overall_match"] is True
