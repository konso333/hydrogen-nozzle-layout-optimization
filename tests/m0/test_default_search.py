"""Default CLI search regression over the full audited N=12..40 range."""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

import pytest

from config import DEFAULT_CONFIG
from optimization.layout_search import default_layout_specs


def test_default_search_space_still_has_426_specifications(reference):
    attempted = sum(len(default_layout_specs(n, DEFAULT_CONFIG)) for n in range(12, 41))
    assert attempted == reference["search"]["attempted"] == 426


def test_default_search_still_returns_415_feasible_and_18_pareto(search_run, reference):
    n_min, n_max, config = search_run["search_inputs"]
    assert (n_min, n_max, config.R, config.d, config.s_min) == (12, 40, 55, 4, 8)
    candidates, rows, pareto = search_run["search"]
    assert len(candidates) == len(rows) == reference["search"]["feasible"] == 415
    assert len(pareto) == reference["search"]["pareto"] == 18
    assert Counter(candidate.N for candidate in candidates) == {
        int(n): count for n, count in reference["search"]["counts_by_n"].items()
    }
    assert {candidate.N for candidate in candidates} == set(range(12, 41))
    ids = [candidate.candidate_id for candidate in candidates]
    assert len(set(ids)) == 415
    assert [row["candidate_id"] for row in rows] == ids
    assert [row["candidate_id"] for row in pareto] == reference["search"]["pareto_ids"]
    assert pareto == [row for row in rows if row["pareto_candidate"]]

    # Independently check real geometry, not only the returned feasible flags.
    for candidate, row in zip(candidates, rows):
        points = candidate.points
        assert len(points) == candidate.N == row["N"] == row["nozzle_count"]
        assert all(math.isfinite(value) for point in points for value in point)
        assert all(math.hypot(x, y) + 2 <= 55 + 1e-9 for x, y in points)
        minimum = min(math.dist(a, b) for a, b in combinations(points, 2))
        assert minimum >= 8 - 1e-9  # Also enforces diameter=4 non-overlap.
        assert row["min_center_distance"] == pytest.approx(minimum, rel=1e-12, abs=1e-9)
        assert all(row[key] for key in ("count_ok", "boundary_ok", "overlap_ok", "spacing_ok", "feasible"))
        assert row["failure_reasons"] == []
