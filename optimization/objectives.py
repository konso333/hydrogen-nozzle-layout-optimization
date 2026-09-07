"""Non-dominated sorting for geometry-only objectives."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from validation import InputValidationError, require_tolerance

DEFAULT_MAXIMIZE_OBJECTIVES = (
    "N",
    "uniformity_score",
    "min_center_distance",
)


class UndefinedObjectiveError(ValueError):
    """A Pareto objective is missing, nonnumeric, or non-finite."""


def _objective_values(row: Mapping[str, object], objectives: tuple[str, ...]) -> list[float]:
    if not objectives:
        raise InputValidationError("objectives must not be empty.")
    values = []
    for name in objectives:
        try:
            value = float(row[name])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise UndefinedObjectiveError(f"objective {name!r} must be present and finite.") from exc
        if not math.isfinite(value):
            raise UndefinedObjectiveError(f"objective {name!r} must be present and finite.")
        values.append(value)
    return values


def dominates(
    candidate: Mapping[str, object],
    other: Mapping[str, object],
    objectives: tuple[str, ...] = DEFAULT_MAXIMIZE_OBJECTIVES,
    tolerance: float = 1e-12,
) -> bool:
    """Compare finite objectives; raise UndefinedObjectiveError for undefined values."""

    require_tolerance(tolerance)
    candidate_values = _objective_values(candidate, objectives)
    other_values = _objective_values(other, objectives)
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
    """Return non-dominated rows, excluding rows with any undefined objective."""

    records = [dict(row) for row in rows]
    eligible = _eligible_indices(records, objectives)
    return [
        row
        for index, row in enumerate(records)
        if index in eligible and not any(
            dominates(records[other_index], row, objectives)
            for other_index in eligible
            if other_index != index
        )
    ]


def mark_pareto_candidates(
    rows: Iterable[Mapping[str, object]],
    objectives: tuple[str, ...] = DEFAULT_MAXIMIZE_OBJECTIVES,
) -> list[dict[str, object]]:
    """Copy rows and mark finite non-dominated candidates.

    Objectives must convert to finite floats; numeric strings and Decimal values
    are supported. Undefined objectives are marked False. Original metrics
    remain unchanged, including N=1 NaNs.
    """

    records = [dict(row) for row in rows]
    eligible = _eligible_indices(records, objectives)
    for index, row in enumerate(records):
        row["pareto_candidate"] = index in eligible and not any(
            dominates(records[other_index], row, objectives)
            for other_index in eligible
            if other_index != index
        )
    return records


def _eligible_indices(records, objectives: tuple[str, ...]) -> set[int]:
    if not objectives:
        raise InputValidationError("objectives must not be empty.")
    eligible = set()
    for index, row in enumerate(records):
        try:
            _objective_values(row, objectives)
        except UndefinedObjectiveError:
            continue
        eligible.add(index)
    return eligible
