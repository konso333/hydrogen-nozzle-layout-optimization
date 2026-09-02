from __future__ import annotations

import math

import pytest

from config import DEFAULT_CONFIG
from geometry.constraints import boundary_check, minimum_center_distance
from geometry.metrics import evaluate_geometry
from layouts import generate_layout


BASELINES = (
    "A_Rectangular",
    "B_Hexagonal",
    "C_Double_Ring",
    "D_Triple_Ring",
)


@pytest.mark.parametrize("layout_type", BASELINES)
def test_baseline_counts_and_boundaries(layout_type: str) -> None:
    config = DEFAULT_CONFIG
    points = generate_layout(layout_type, 24, config.R, config.d, config.s_min)
    assert len(points) == 24
    assert boundary_check(points, R=config.R, d=config.d)


@pytest.mark.parametrize("layout_type", BASELINES)
def test_baseline_aliases_reject_non_24_counts(layout_type: str) -> None:
    config = DEFAULT_CONFIG
    with pytest.raises(ValueError, match="fixed N=24 baseline"):
        generate_layout(layout_type, 23, config.R, config.d, config.s_min)


def test_rectangular_baseline_is_axis_and_origin_symmetric() -> None:
    config = DEFAULT_CONFIG
    points = generate_layout("A_Rectangular", 24, config.R, config.d, config.s_min)
    metrics = evaluate_geometry(
        points,
        R=config.R,
        d=config.d,
        s_min=config.s_min,
        expected_N=24,
    )
    assert metrics["x_axis_symmetry"]
    assert metrics["y_axis_symmetry"]
    assert metrics["origin_symmetry"]


def test_hexagonal_baseline_nearest_neighbor_is_16_mm() -> None:
    config = DEFAULT_CONFIG
    points = generate_layout("B_Hexagonal", 24, config.R, config.d, config.s_min)
    assert minimum_center_distance(points) == pytest.approx(16.0)


def test_custom_ring_count_is_exact() -> None:
    points = generate_layout(
        "ring",
        12,
        55,
        4,
        8,
        ring_radii=[20, 45],
        points_per_ring=[4, 8],
    )
    assert len(points) == 12


def test_sector_count_and_repeatability() -> None:
    parameters = {
        "num_sectors": 4,
        "points_per_sector": 6,
        "inner_radius": 18,
        "outer_radius": 46,
        "sector_angle": math.pi / 6,
        "angular_offset": math.pi / 16,
        "radial_levels": 3,
    }
    first = generate_layout("sector", 24, 55, 4, 8, **parameters)
    second = generate_layout("sector", 24, 55, 4, 8, **parameters)
    assert len(first) == 24
    assert first == second


def test_staggered_ring_applies_requested_phase() -> None:
    points = generate_layout(
        "staggered_ring",
        24,
        55,
        4,
        8,
        ring_radii=[25, 48],
        points_per_ring=[8, 16],
        delta_theta=math.pi / 16,
    )
    outer_first = points[8]
    assert math.atan2(outer_first[1], outer_first[0]) == pytest.approx(math.pi / 16)


def test_radial_spoke_parameters_change_radius_and_angle() -> None:
    points = generate_layout(
        "radial_spoke",
        12,
        55,
        4,
        8,
        num_spokes=4,
        points_per_spoke=3,
        inner_radius=16,
        outer_radius=48,
        angular_offset=math.pi / 8,
    )
    assert math.hypot(*points[0]) == pytest.approx(16)
    assert math.hypot(*points[2]) == pytest.approx(48)
    assert math.atan2(points[0][1], points[0][0]) == pytest.approx(math.pi / 8)


def test_nonuniform_ring_parameters_control_radii_and_offsets() -> None:
    points = generate_layout(
        "nonuniform_ring",
        12,
        55,
        4,
        8,
        ring_radii=[20, 45],
        points_per_ring=[4, 8],
        ring_offsets=[0, math.pi / 8],
    )
    assert math.hypot(*points[0]) == pytest.approx(20)
    assert math.hypot(*points[4]) == pytest.approx(45)
    assert math.atan2(points[4][1], points[4][0]) == pytest.approx(math.pi / 8)
