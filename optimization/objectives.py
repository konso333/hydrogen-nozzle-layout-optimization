"""Non-dominated sorting for geometry-only objectives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


DEFAULT_MAXIMIZE_OBJECTIVES = (
    "N",
    "uniformity_score",
    "min_center_distance",
)


def dominates(
    candidate: Mapping[str, object],
    other: Mapping[str, object],
    objectives: tuple[str, ...] = DEFAULT_MAXIMIZE_OBJECTIVES,
    tolerance: float = 1e-12,
) -> bool:
    """Return True when candidate is no worse in all and better in one objective."""

    candidate_values = [float(candidate[name]) for name in objectives]
    other_values = [float(other[name]) for name in objectives]
    no_worse = all(
        left >= right - tolerance
        for left, right in zip(candidate_values, other_values)
    )
    strictly_better = any(
        left > right + tolerance
        for left, right in zip(candidate_values, other_values)
    )
    return no_worse and strictly_better


def pareto_frontier(
    rows: Iterable[Mapping[str, object]],
    objectives: tuple[str, ...] = DEFAULT_MAXIMIZE_OBJECTIVES,
) -> list[dict[str, object]]:
    """Return all non-dominated rows without constructing a weighted score."""

    records = [dict(row) for row in rows]
    return [
        row
        for index, row in enumerate(records)
        if not any(
            dominates(other, row, objectives)
            for other_index, other in enumerate(records)
            if other_index != index
        )
    ]


def mark_pareto_candidates(
    rows: Iterable[Mapping[str, object]],
    objectives: tuple[str, ...] = DEFAULT_MAXIMIZE_OBJECTIVES,
) -> list[dict[str, object]]:
    """Copy rows and add a boolean ``pareto_candidate`` field."""

    records = [dict(row) for row in rows]
    for index, row in enumerate(records):
        row["pareto_candidate"] = not any(
            dominates(other, row, objectives)
            for other_index, other in enumerate(records)
            if other_index != index
        )
    return records
