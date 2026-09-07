"""Freeze all coordinates in their original order and all saved geometry metrics."""

from __future__ import annotations

import numpy as np
import pytest

from config import DEFAULT_CONFIG
from geometry.metrics import evaluate_geometry
from layouts import BASELINE_LAYOUT_PARAMETERS, generate_layout
from scripts.compare_layouts import COMPARISON_SPECS


BASELINES = ("A_Rectangular", "B_Hexagonal", "C_Double_Ring", "D_Triple_Ring")
COMPARISONS = BASELINES + (
    "E_Sector_4x6", "F_Radial_8x3", "G_Staggered_Ring_8_16",
    "H_Nonuniform_3_Ring", "I_Deterministic_Irregular",
)


def _assert_result(points, case):
    assert len(points) == 24
    # No sorting: a permutation must fail even if the geometry is unchanged.
    np.testing.assert_allclose(points, case["points"], rtol=0, atol=1e-9)
    actual = evaluate_geometry(points, R=55, d=4, s_min=8, expected_N=24)
    for key, expected in case["metrics"].items():
        if isinstance(expected, float):
            assert actual[key] == pytest.approx(expected, rel=1e-12, abs=1e-9), key
        else:
            assert actual[key] == expected, key


@pytest.mark.parametrize("name", BASELINES)
def test_fixed_baseline_complete_coordinates_and_metrics(name, reference_cases):
    points = generate_layout(name, 24, 55, 4, 8)
    _assert_result(points, reference_cases[name])


def test_default_geometry_and_layout_inventory():
    assert (DEFAULT_CONFIG.R, DEFAULT_CONFIG.d, DEFAULT_CONFIG.s_min) == (55, 4, 8)
    assert tuple(BASELINE_LAYOUT_PARAMETERS) == BASELINES
    assert tuple(spec[0] for spec in COMPARISON_SPECS) == COMPARISONS


@pytest.mark.parametrize("name", COMPARISONS)
def test_comparison_complete_coordinates_order_and_metrics(name, reference_cases):
    case = reference_cases[name]
    _, layout_type, parameters = next(spec for spec in COMPARISON_SPECS if spec[0] == name)
    assert layout_type == case["layout_type"]
    # Parameters come from the audited saved comparison, not today's constants.
    assert parameters.keys() == case["parameters"].keys()
    for key, value in case["parameters"].items():
        assert parameters[key] == pytest.approx(value, rel=1e-12, abs=1e-12), key
    points = generate_layout(layout_type, 24, 55, 4, 8, **parameters)
    _assert_result(points, case)
