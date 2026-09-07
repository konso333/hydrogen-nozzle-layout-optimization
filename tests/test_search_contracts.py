"""M1 search distinguishes expected infeasibility from bad inputs and bugs."""

import math

import numpy as np
import pytest

import layouts
import optimization.layout_search as search
from config import DEFAULT_CONFIG
from geometry.constraints import LayoutConstraintError
from layouts import UnknownLayoutTypeError
from optimization.layout_search import LayoutSpec, evaluate_spec, search_layouts_for_n
from optimization.search_n import search_variable_n
from validation import InputValidationError


def test_valid_spec_returns_candidate():
    spec = LayoutSpec("rectangular", {"spacing": 10})
    candidate = evaluate_spec(4, DEFAULT_CONFIG, spec, "valid")
    assert candidate.candidate_id == "valid"
    assert candidate.metrics["feasible"]
    assert candidate.points == layouts.generate_layout("rectangular", 4, 55, 4, 8, spacing=10)


@pytest.mark.parametrize("spec,n", [
    (LayoutSpec("rectangular", {"spacing": 100}), 4),
    (LayoutSpec("rectangular", {"spacing": 1}), 4),
    (LayoutSpec("hexagonal", {"spacing": 100}), 4),
])
def test_only_geometric_infeasibility_becomes_none(spec, n):
    assert evaluate_spec(n, DEFAULT_CONFIG, spec, "infeasible") is None
    assert search_layouts_for_n(n, DEFAULT_CONFIG, [spec]) == []


def test_insufficient_lattice_capacity_has_geometric_exception():
    with pytest.raises(LayoutConstraintError, match="sites fit"):
        layouts.generate_layout("hexagonal", 4, 55, 4, 8, spacing=100)


@pytest.mark.parametrize("spec", [
    LayoutSpec("rectangular", {"spacing": -1}),
    LayoutSpec("ring", {"ring_radii": [20], "points_per_ring": [4.9]}),
    LayoutSpec("ring", {"ring_radii": [20], "points_per_ring": [3]}),
    LayoutSpec("deterministic_irregular", {"inner_radius": 40, "outer_radius": 20}),
])
def test_invalid_spec_is_explicit_input_error(spec):
    with pytest.raises(InputValidationError):
        evaluate_spec(4, DEFAULT_CONFIG, spec, "bad_input")


def test_unknown_layout_is_distinct_and_propagates_through_search():
    spec = LayoutSpec("m1_typo", {})
    with pytest.raises(UnknownLayoutTypeError, match="m1_typo"):
        evaluate_spec(4, DEFAULT_CONFIG, spec, "unknown")
    with pytest.raises(UnknownLayoutTypeError):
        search_variable_n(4, 4, DEFAULT_CONFIG, lambda n, config: [spec])


@pytest.mark.parametrize("parameters", [{}, {"spacing": 10, "typo": 1}])
def test_missing_and_unexpected_keywords_propagate(parameters):
    with pytest.raises(TypeError):
        evaluate_spec(4, DEFAULT_CONFIG, LayoutSpec("rectangular", parameters), "bad_keywords")


@pytest.mark.parametrize("error", [ValueError("internal value bug"), RuntimeError("internal bug"),
                                  TypeError("internal type bug"), KeyError("internal key bug")])
def test_generator_bugs_are_not_swallowed(monkeypatch, error):
    def broken(**kwargs):
        raise error

    monkeypatch.setattr(layouts, "LAYOUT_REGISTRY", dict(layouts.LAYOUT_REGISTRY))
    layouts.register_layout("m1_broken", broken)
    with pytest.raises(type(error)) as caught:
        search_variable_n(4, 4, DEFAULT_CONFIG, lambda n, config: [LayoutSpec("m1_broken", {})])
    assert caught.value is error


@pytest.mark.parametrize("error", [ValueError("metric bug"), LayoutConstraintError("unexpected metric bug")])
def test_metric_errors_are_outside_the_infeasibility_catch(monkeypatch, error):
    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(search, "evaluate_geometry", broken)
    with pytest.raises(type(error)) as caught:
        evaluate_spec(4, DEFAULT_CONFIG, LayoutSpec("rectangular", {"spacing": 10}), "metric_bug")
    assert caught.value is error


@pytest.mark.parametrize("points", [[(math.nan, 0)], [(math.inf, 0)], [0, 0], [[0], [0, 1]]])
def test_malformed_extension_output_is_not_infeasibility(monkeypatch, points):
    monkeypatch.setattr(layouts, "LAYOUT_REGISTRY", {"m1_bad_output": lambda **kwargs: points})
    with pytest.raises(ValueError) as caught:
        evaluate_spec(1, DEFAULT_CONFIG, LayoutSpec("m1_bad_output", {}), "bad_output")
    assert not isinstance(caught.value, LayoutConstraintError)


@pytest.mark.parametrize("bad", [4.0, 4.9, True, False, 0, -1, "4", None, math.nan, math.inf])
def test_search_counts_are_validated_even_for_empty_specs(bad):
    with pytest.raises(InputValidationError, match="N"):
        search_layouts_for_n(bad, DEFAULT_CONFIG, [])
    with pytest.raises(InputValidationError, match="N"):
        search.default_layout_specs(bad, DEFAULT_CONFIG)
    with pytest.raises(InputValidationError, match="N_min"):
        search_variable_n(bad, 4, DEFAULT_CONFIG)
    with pytest.raises(InputValidationError, match="N_max"):
        search_variable_n(1, bad, DEFAULT_CONFIG)


def test_search_accepts_numpy_integer_range():
    candidates, rows, pareto = search_variable_n(
        np.int64(4), np.int64(4), DEFAULT_CONFIG,
        lambda n, config: [LayoutSpec("rectangular", {"spacing": 10})],
    )
    assert len(candidates) == len(rows) == len(pareto) == 1
