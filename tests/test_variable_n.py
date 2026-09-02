from __future__ import annotations

from config import DEFAULT_CONFIG
from optimization.layout_search import LayoutSpec, search_layouts_for_n
from optimization.search_n import search_variable_n


def test_variable_n_search_rejects_illegal_layout() -> None:
    invalid = LayoutSpec(
        "radial_spoke",
        {
            "num_spokes": 4,
            "points_per_spoke": 2,
            "inner_radius": 52,
            "outer_radius": 60,
        },
    )
    candidates = search_layouts_for_n(8, DEFAULT_CONFIG, [invalid])
    assert candidates == []


def test_variable_n_search_returns_only_feasible_exact_counts() -> None:
    candidates, rows, pareto = search_variable_n(12, 14, DEFAULT_CONFIG)
    assert candidates
    assert {candidate.N for candidate in candidates} == {12, 13, 14}
    assert all(len(candidate.points) == candidate.N for candidate in candidates)
    assert all(row["feasible"] for row in rows)
    assert pareto
