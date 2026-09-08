"""Non-dominated sorting for explicitly defined geometry-only objectives."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType

from geometry.definitions import METRIC_DEFINITIONS
from validation import InputValidationError, require_tolerance


@dataclass(frozen=True)
class ObjectiveTerm:
    """One referenced value and its Pareto direction."""

    key: str
    direction: str
    source: str = "metric"

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("Objective key must be nonempty text.")
        if self.direction not in {"minimize", "maximize"}:
            raise ValueError("Objective direction must be 'minimize' or 'maximize'.")
        if self.source not in {"metric", "case_descriptor"}:
            raise ValueError("Objective source must be 'metric' or 'case_descriptor'.")


@dataclass(frozen=True)
class ObjectiveProfile:
    """Named, queryable set of Pareto objectives; no weights are supported."""

    name: str
    objectives: tuple[ObjectiveTerm, ...]
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or not isinstance(self.description, str):
            raise ValueError("Objective profiles require text name and description.")
        if not self.objectives:
            raise ValueError("Objective profiles require a name and at least one objective.")
        if len({term.key for term in self.objectives}) != len(self.objectives):
            raise ValueError("Objective profile keys must be unique.")
        for term in self.objectives:
            if term.source != "metric":
                continue
            definition = METRIC_DEFINITIONS.get(term.key)
            if definition is None:
                raise ValueError(f"Objective {term.key!r} has no metric definition.")
            if not definition.optimization_eligible:
                raise ValueError(f"Metric {term.key!r} is not optimization-eligible.")
            if definition.direction != term.direction:
                raise ValueError(
                    f"Objective {term.key!r} direction conflicts with metric metadata."
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "objectives": [asdict(term) for term in self.objectives],
        }


LEGACY_OBJECTIVE_PROFILE = ObjectiveProfile(
    name="legacy",
    objectives=(
        # N is the sole grandfathered case descriptor. It is retained to keep
        # the M0-M3 default frontier byte-for-byte compatible.
        ObjectiveTerm("N", "maximize", "case_descriptor"),
        ObjectiveTerm("uniformity_score", "maximize"),
        ObjectiveTerm("min_center_distance", "maximize"),
    ),
    description=(
        "M0-M3 compatibility profile: maximize nozzle count, nearest-neighbour "
        "uniformity score, and minimum centre distance."
    ),
)
OBJECTIVE_PROFILES = MappingProxyType({"legacy": LEGACY_OBJECTIVE_PROFILE})
OBJECTIVE_PROFILE_ALIASES = MappingProxyType({"default": "legacy", "legacy": "legacy"})
DEFAULT_OBJECTIVE_PROFILE = LEGACY_OBJECTIVE_PROFILE
DEFAULT_MAXIMIZE_OBJECTIVES = tuple(
    term.key for term in LEGACY_OBJECTIVE_PROFILE.objectives
)


def _validate_profiles() -> None:
    for profile in OBJECTIVE_PROFILES.values():
        for term in profile.objectives:
            if term.source != "metric":
                continue
            definition = METRIC_DEFINITIONS.get(term.key)
            if definition is None:
                raise RuntimeError(f"Objective {term.key!r} has no metric definition.")
            if not definition.optimization_eligible or definition.direction != term.direction:
                raise RuntimeError(f"Objective {term.key!r} conflicts with metric metadata.")


_validate_profiles()


def get_objective_profile(name: str = "default") -> ObjectiveProfile:
    """Resolve a public profile name or alias."""

    try:
        canonical = OBJECTIVE_PROFILE_ALIASES[name]
        return OBJECTIVE_PROFILES[canonical]
    except (KeyError, TypeError) as exc:
        raise InputValidationError(f"Unknown objective profile: {name!r}.") from exc


def objective_profiles() -> dict[str, dict[str, object]]:
    """Return JSON-ready metadata for canonical profiles."""

    return {name: profile.to_dict() for name, profile in OBJECTIVE_PROFILES.items()}


class UndefinedObjectiveError(ValueError):
    """A Pareto objective is missing, nonnumeric, or non-finite."""


ObjectiveSelection = tuple[str, ...] | ObjectiveProfile


def _terms(objectives: ObjectiveSelection) -> tuple[ObjectiveTerm, ...]:
    if isinstance(objectives, ObjectiveProfile):
        return objectives.objectives
    if not objectives:
        raise InputValidationError("objectives must not be empty.")
    return tuple(ObjectiveTerm(name, "maximize") for name in objectives)


def _objective_values(
    row: Mapping[str, object],
    objectives: ObjectiveSelection,
) -> list[float]:
    values = []
    for term in _terms(objectives):
        try:
            value = float(row[term.key])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise UndefinedObjectiveError(
                f"objective {term.key!r} must be present and finite."
            ) from exc
        if not math.isfinite(value):
            raise UndefinedObjectiveError(
                f"objective {term.key!r} must be present and finite."
            )
        values.append(value if term.direction == "maximize" else -value)
    return values


def dominates(
    candidate: Mapping[str, object],
    other: Mapping[str, object],
    objectives: ObjectiveSelection = DEFAULT_MAXIMIZE_OBJECTIVES,
    tolerance: float = 1e-12,
) -> bool:
    """Compare finite objectives; raise for undefined values.

    A tuple of field names retains the historical all-maximize convention.
    Passing an ObjectiveProfile also supports explicit minimize terms.
    """

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
    objectives: ObjectiveSelection = DEFAULT_MAXIMIZE_OBJECTIVES,
) -> list[dict[str, object]]:
    """Return non-dominated rows, excluding rows with undefined objectives."""

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
    objectives: ObjectiveSelection = DEFAULT_MAXIMIZE_OBJECTIVES,
) -> list[dict[str, object]]:
    """Copy rows and mark finite non-dominated candidates.

    Undefined objectives are marked False. Original metric values remain
    unchanged, including explicit None values.
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


def _eligible_indices(
    records: list[dict[str, object]],
    objectives: ObjectiveSelection,
) -> set[int]:
    _terms(objectives)  # Validate an empty selection even when records is empty.
    eligible = set()
    for index, row in enumerate(records):
        try:
            _objective_values(row, objectives)
        except UndefinedObjectiveError:
            continue
        eligible.add(index)
    return eligible
