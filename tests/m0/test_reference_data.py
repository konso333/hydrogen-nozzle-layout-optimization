"""Audit frozen data with scalar mathematics, without importing production code.

These assertions document the oracle. Production regressions compare against
the JSON snapshot; this module never calls a layout or metric implementation.
"""

from __future__ import annotations

import cmath
import math
import statistics

import numpy as np
import pytest


NAMES = (
    "A_Rectangular", "B_Hexagonal", "C_Double_Ring", "D_Triple_Ring",
    "E_Sector_4x6", "F_Radial_8x3", "G_Staggered_Ring_8_16",
    "H_Nonuniform_3_Ring", "I_Deterministic_Irregular",
)


def mathematical_points(name):
    """Closed-form, fixed-case oracle; no registry, linspace or shared helpers."""
    if name == "A_Rectangular":
        return [(x, y) for y in (-27, -9, 9, 27) for x in (-45, -27, -9, 9, 27, 45)]
    if name == "B_Hexagonal":
        rows = (
            (-2, (-3, -1, 1, 3)),
            (-1, (-4, -2, 0, 2, 4)),
            (0, (-5, -3, -1, 1, 3, 5)),
            (1, (-4, -2, 0, 2, 4)),
            (2, (-3, -1, 1, 3)),
        )
        return [(8 * x, 8 * math.sqrt(3) * y) for y, xs in rows for x in xs]

    # Express the frozen order as radius/angle indexed by the nozzle's position.
    polar = []
    for i in range(24):
        if name in ("C_Double_Ring", "G_Staggered_Ring_8_16"):
            if i < 8:
                radius, angle = 25, i * math.pi / 4
            else:
                phase = math.pi / 16 if name.startswith("G_") else 0
                radius, angle = 48, phase + (i - 8) * math.pi / 8
        elif name == "D_Triple_Ring":
            if i == 0:
                radius, angle = 0, 0
            elif i < 8:
                radius, angle = 25, (i - 1) * 2 * math.pi / 7
            else:
                radius, angle = 48, (i - 8) * math.pi / 8
        elif name == "E_Sector_4x6":
            radius = (18, 32, 46)[(i % 6) // 2]
            angle = (i // 6) * math.pi / 2 + (-math.pi / 48 if i % 2 == 0 else 7 * math.pi / 48)
        elif name == "F_Radial_8x3":
            radius, angle = (16, 31, 46)[i % 3], math.pi / 16 + (i // 3) * math.pi / 4
        elif name == "H_Nonuniform_3_Ring":
            if i < 4:
                radius, angle = 18, i * math.pi / 2
            elif i < 12:
                radius, angle = 34, math.pi / 8 + (i - 4) * math.pi / 4
            else:
                radius, angle = 50, math.pi / 12 + (i - 12) * math.pi / 6
        elif name == "I_Deterministic_Irregular":
            radius = math.sqrt(2500 * (2 * i + 1) / 48)
            angle = i * math.pi * (3 - math.sqrt(5))
        else:
            raise AssertionError(f"No independently reviewed formula for {name}")
        z = cmath.rect(radius, angle)
        polar.append((z.real, z.imag))
    return polar


def independent_metrics(points):
    """Scalar all-pairs distances and population statistics, independent of numpy metrics."""
    nearest = [min(math.dist(p, q) for j, q in enumerate(points) if j != i)
               for i, p in enumerate(points)]
    radii = [math.hypot(x, y) for x, y in points]
    mean = statistics.mean(nearest)
    std = statistics.pstdev(nearest)

    def symmetric(sx, sy):
        return all(any(abs(x * sx - u) <= 1e-6 and abs(y * sy - v) <= 1e-6
                       for u, v in points) for x, y in points)

    return {
        "nozzle_count": len(points),
        "min_center_distance": min(nearest),
        "mean_nearest_neighbor_distance": mean,
        "std_nearest_neighbor_distance": std,
        "nearest_neighbor_cv": std / mean,
        "uniformity_score": 1 / (1 + std / mean),
        "max_center_radius": max(radii),
        "mean_center_radius": statistics.mean(radii),
        "radial_std": statistics.pstdev(radii),
        "count_ok": len(points) == 24,
        "boundary_ok": max(radii) + 2 <= 55 + 1e-9,
        "overlap_ok": min(nearest) >= 4 - 1e-9,
        "spacing_ok": min(nearest) >= 8 - 1e-9,
        "feasible": len(points) == 24 and max(radii) + 2 <= 55 + 1e-9 and min(nearest) >= 8 - 1e-9,
        "failure_reasons": [],
        "x_axis_symmetry": symmetric(1, -1),
        "y_axis_symmetry": symmetric(-1, 1),
        "origin_symmetry": symmetric(-1, -1),
    }


@pytest.mark.parametrize("name", NAMES)
def test_frozen_reference_is_mathematically_consistent(name, reference_cases):
    case = reference_cases[name]
    points = mathematical_points(name)
    np.testing.assert_allclose(case["points"], points, rtol=0, atol=1e-9)
    metrics = independent_metrics(points)
    assert metrics.keys() == case["metrics"].keys()
    for key, expected in case["metrics"].items():
        if isinstance(expected, float):
            assert metrics[key] == pytest.approx(expected, rel=1e-12, abs=1e-9), key
        else:
            assert metrics[key] == expected, key
