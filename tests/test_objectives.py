from __future__ import annotations

from optimization.objectives import mark_pareto_candidates


def test_pareto_filter_uses_non_dominance_not_weighted_sum() -> None:
    rows = [
        {"id": "spacing", "N": 12, "uniformity_score": 1.0, "min_center_distance": 20.0},
        {"id": "count", "N": 16, "uniformity_score": 0.9, "min_center_distance": 18.0},
        {"id": "dominated", "N": 10, "uniformity_score": 0.8, "min_center_distance": 15.0},
    ]
    marked = mark_pareto_candidates(rows)
    flags = {row["id"]: row["pareto_candidate"] for row in marked}
    assert flags == {"spacing": True, "count": True, "dominated": False}
