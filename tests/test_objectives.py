from __future__ import annotations

import math
from decimal import Decimal

import pytest

from config import DEFAULT_CONFIG
from optimization.layout_search import LayoutSpec
from optimization.objectives import (
    DEFAULT_MAXIMIZE_OBJECTIVES, UndefinedObjectiveError, dominates,
    mark_pareto_candidates, pareto_frontier,
)
from optimization.search_n import search_variable_n
from validation import InputValidationError


def test_pareto_filter_uses_non_dominance_not_weighted_sum() -> None:
    rows = [
        {"id": "spacing", "N": 12, "uniformity_score": 1.0, "min_center_distance": 20.0},
        {"id": "count", "N": 16, "uniformity_score": 0.9, "min_center_distance": 18.0},
        {"id": "dominated", "N": 10, "uniformity_score": 0.8, "min_center_distance": 15.0},
    ]
    marked = mark_pareto_candidates(rows)
    flags = {row["id"]: row["pareto_candidate"] for row in marked}
    assert flags == {"spacing": True, "count": True, "dominated": False}


GOOD = {"N": 4, "uniformity_score": 1.0, "min_center_distance": 10.0}


def test_known_dominance_and_identical_objectives():
    worse = {**GOOD, "min_center_distance": 9.0}
    assert dominates(GOOD, worse)
    assert not dominates(worse, GOOD)
    assert not dominates(GOOD, dict(GOOD))
    rows = [{"id": "a", **GOOD}, {"id": "b", **GOOD}, {"id": "worse", **worse}]
    assert [row["id"] for row in pareto_frontier(rows)] == ["a", "b"]
    assert [row["pareto_candidate"] for row in mark_pareto_candidates(rows)] == [True, True, False]
    assert all("pareto_candidate" not in row for row in rows)


@pytest.mark.parametrize("field", DEFAULT_MAXIMIZE_OBJECTIVES)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, None, "invalid", 1 + 2j,
                                 "NaN", "+Inf", "-Inf", Decimal("NaN"),
                                 Decimal("Infinity"), Decimal("-Infinity")])
def test_invalid_objectives_never_enter_comparison_or_frontier(field, bad):
    invalid = {**GOOD, field: bad}
    for left, right in ((GOOD, invalid), (invalid, GOOD)):
        with pytest.raises(UndefinedObjectiveError, match=field):
            dominates(left, right)
    rows = [{"id": "invalid", **invalid}, {"id": "valid", **GOOD}]
    assert [row["id"] for row in pareto_frontier(rows)] == ["valid"]
    marked = mark_pareto_candidates(rows)
    assert [row["pareto_candidate"] for row in marked] == [False, True]
    if isinstance(bad, (float, Decimal)) and math.isnan(bad):
        assert math.isnan(marked[0][field])
    else:
        assert marked[0][field] == bad
    assert pareto_frontier([invalid]) == []
    assert mark_pareto_candidates([invalid])[0]["pareto_candidate"] is False


@pytest.mark.parametrize("field", DEFAULT_MAXIMIZE_OBJECTIVES)
@pytest.mark.parametrize("value", [4, 4.0, "4", "1.0", Decimal("10"), True, False])
def test_float_convertible_finite_objectives_keep_numeric_semantics(field, value):
    candidate = {**GOOD, field: value}
    numeric = {**GOOD, field: float(value)}
    worse = {**GOOD, field: float(value) - 1}
    assert dominates(candidate, worse)
    assert not dominates(worse, candidate)
    assert not dominates(candidate, numeric)
    assert not dominates(numeric, candidate)
    rows = [candidate, worse, numeric]
    assert pareto_frontier(rows) == [candidate, numeric]
    marked = mark_pareto_candidates(rows)
    assert [row["pareto_candidate"] for row in marked] == [True, False, True]
    assert marked[0][field] == value


def test_missing_objective_is_ineligible():
    missing = {"N": 4, "min_center_distance": 10}
    with pytest.raises(UndefinedObjectiveError, match="uniformity_score"):
        dominates(missing, GOOD)
    assert pareto_frontier([missing, GOOD]) == [GOOD]
    assert mark_pareto_candidates([missing])[0]["pareto_candidate"] is False


def test_only_selected_objectives_determine_eligibility():
    row = {"N": 1, "uniformity_score": math.nan}
    assert pareto_frontier([row], objectives=("N",)) == [row]
    assert dominates({"N": 2}, row, objectives=("N",))


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1])
def test_dominance_requires_finite_nonnegative_tolerance(bad):
    with pytest.raises(InputValidationError, match="tolerance"):
        dominates(GOOD, GOOD, tolerance=bad)


def test_empty_inputs_and_empty_objective_contract():
    assert pareto_frontier([]) == mark_pareto_candidates([]) == []
    for function in (pareto_frontier, mark_pareto_candidates):
        with pytest.raises(InputValidationError, match="objectives"):
            function([], objectives=())
    with pytest.raises(InputValidationError, match="objectives"):
        dominates(GOOD, GOOD, objectives=())


def test_real_single_nozzle_search_is_feasible_but_not_pareto_eligible():
    candidates, rows, pareto = search_variable_n(
        1, 2, DEFAULT_CONFIG,
        lambda n, config: [LayoutSpec("rectangular", {"spacing": 10})],
    )
    assert len(candidates) == len(rows) == 2
    assert all(candidate.metrics["feasible"] for candidate in candidates)
    assert rows[0]["min_center_distance"] is None
    assert rows[0]["uniformity_score"] is None
    assert rows[0]["pareto_candidate"] is False
    assert [row["N"] for row in pareto] == [2]
    assert pareto_frontier(rows) == pareto
