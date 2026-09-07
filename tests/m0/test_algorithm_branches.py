"""Independent assertions for valid irregular and power-law ring branches."""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pytest

from geometry.constraints import LayoutConstraintError
from layouts import generate_layout


def _assert_feasible(points, n):
    assert len(points) == n
    assert all(math.hypot(x, y) + 2 <= 55 + 1e-9 for x, y in points)
    assert all(math.dist(a, b) >= 8 - 1e-9 for a, b in combinations(points, 2))


@pytest.mark.parametrize("n", [12, 24, 40])
def test_irregular_midpoint_area_law_and_golden_angle(n):
    points = generate_layout("deterministic_irregular", n, 55, 4, 8,
                             inner_radius=0, outer_radius=50)
    _assert_feasible(points, n)
    z = np.array([complex(x, y) for x, y in points])
    # Midpoints of n equal intervals in squared radius; no endpoint at 0 or 50.
    np.testing.assert_allclose(abs(z) ** 2, [1250 * (2 * i + 1) / n for i in range(n)],
                               rtol=0, atol=1e-9)
    phase = z / abs(z)
    angle = math.pi * (3 - math.sqrt(5))
    np.testing.assert_allclose(phase[1:] / phase[:-1], complex(math.cos(angle), math.sin(angle)),
                               rtol=0, atol=1e-12)
    assert phase[0] == pytest.approx(1 + 0j, abs=1e-12)
    repeated = generate_layout("deterministic_irregular", n, 55, 4, 8,
                               inner_radius=0, outer_radius=50)
    np.testing.assert_allclose(repeated, points, rtol=0, atol=1e-12)


def test_irregular_custom_radius_exponent_increment_and_offset():
    points = generate_layout(
        "deterministic_irregular", 4, 55, 4, 8,
        inner_radius=8, outer_radius=40, radial_exponent=1,
        angular_increment=math.pi / 2, angular_offset=math.pi / 4,
    )
    # Radii 12,20,28,36 at 45,135,225,315 degrees, in that order.
    expected = np.array([(6, 6), (-10, 10), (-14, -14), (18, -18)]) * math.sqrt(2)
    np.testing.assert_allclose(points, expected, rtol=0, atol=1e-9)
    _assert_feasible(points, 4)


def test_irregular_single_point_uses_midpoint_and_offset():
    points = generate_layout(
        "deterministic_irregular", 1, 55, 4, 8, inner_radius=10, outer_radius=42,
        radial_exponent=2, angular_offset=math.pi / 2,
    )
    np.testing.assert_allclose(points, [(0, 18)], rtol=0, atol=1e-9)


@pytest.mark.parametrize("parameters", [
    {"inner_radius": -1, "outer_radius": 50},
    {"inner_radius": 40, "outer_radius": 20},
    {"inner_radius": 0, "outer_radius": 50, "radial_exponent": 0},
])
def test_irregular_rejects_existing_invalid_finite_parameters(parameters):
    with pytest.raises(ValueError):
        generate_layout("deterministic_irregular", 24, 55, 4, 8, **parameters)


@pytest.mark.parametrize("outer_radius", [5, 60], ids=["spacing", "boundary"])
def test_irregular_keeps_geometric_rejection(outer_radius):
    with pytest.raises(LayoutConstraintError):
        generate_layout("deterministic_irregular", 24, 55, 4, 8,
                        inner_radius=0, outer_radius=outer_radius)


@pytest.mark.parametrize("parameters, middle_radius", [
    ({}, 34),
    ({"radial_exponent": 1}, 34),
    ({"radial_exponent": 2}, 26),
    ({"radial_exponent": 0.5}, 18 + 16 * math.sqrt(2)),
], ids=["default-linear", "linear", "quadratic", "square-root"])
def test_nonuniform_power_law_radii_phases_and_center(parameters, middle_radius):
    points = generate_layout(
        "nonuniform_ring", 25, 55, 4, 8, points_per_ring=[4, 8, 12],
        inner_radius=18, outer_radius=50, include_center=True,
        angular_offset=math.pi / 12, ring_offsets=[0, math.pi / 8, math.pi / 12],
        **parameters,
    )
    _assert_feasible(points, 25)
    np.testing.assert_allclose(points[0], (0, 0), rtol=0, atol=1e-9)
    for group, radius, offset in (
        (points[1:5], 18, math.pi / 12),
        (points[5:13], middle_radius, 5 * math.pi / 24),
        (points[13:25], 50, math.pi / 6),
    ):
        z = np.array([complex(x, y) for x, y in group])
        np.testing.assert_allclose(abs(z), radius, rtol=0, atol=1e-9)
        # Explicit unit-circle angles check the whole sequence, not just endpoints.
        angles = offset + np.arange(len(group)) * (2 * math.pi / len(group))
        np.testing.assert_allclose(z / radius, np.exp(1j * angles), rtol=0, atol=1e-12)


def test_nonuniform_single_ring_power_law_uses_inner_endpoint():
    points = generate_layout(
        "nonuniform_ring", 8, 55, 4, 8, points_per_ring=[8],
        inner_radius=20, outer_radius=48, radial_exponent=2,
    )
    _assert_feasible(points, 8)
    np.testing.assert_allclose(np.linalg.norm(points, axis=1), 20, rtol=0, atol=1e-9)


@pytest.mark.parametrize("parameters", [
    {"inner_radius": 18},
    {"inner_radius": 18, "outer_radius": 50, "radial_exponent": 0},
    {"inner_radius": 50, "outer_radius": 18},
])
def test_nonuniform_power_law_existing_invalid_finite_parameters(parameters):
    with pytest.raises(ValueError):
        generate_layout("nonuniform_ring", 24, 55, 4, 8,
                        points_per_ring=[4, 8, 12], **parameters)
