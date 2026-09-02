"""Search a range of N values and identify geometry-only Pareto candidates."""

from __future__ import annotations

from collections.abc import Callable

from config import GeometryConfig
from optimization.layout_search import (
    LayoutCandidate,
    LayoutSpec,
    default_layout_specs,
    search_layouts_for_n,
)
from optimization.objectives import mark_pareto_candidates


SpecFactory = Callable[[int, GeometryConfig], list[LayoutSpec]]


def search_variable_n(
    N_min: int,
    N_max: int,
    config: GeometryConfig,
    spec_factory: SpecFactory = default_layout_specs,
) -> tuple[list[LayoutCandidate], list[dict[str, object]], list[dict[str, object]]]:
    """Return candidates, all marked rows, and the non-dominated rows."""

    if N_min <= 0 or N_max < N_min:
        raise ValueError("Require 0 < N_min <= N_max.")

    candidates: list[LayoutCandidate] = []
    for N in range(N_min, N_max + 1):
        candidates.extend(search_layouts_for_n(N, config, spec_factory(N, config)))

    rows = [candidate.summary_row() for candidate in candidates]
    marked_rows = mark_pareto_candidates(rows)
    pareto_rows = [row for row in marked_rows if row["pareto_candidate"]]
    return candidates, marked_rows, pareto_rows
