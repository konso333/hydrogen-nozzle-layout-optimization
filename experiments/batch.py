"""Opt-in search execution and M2 archival, with separate M3 design/report files."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.archive import archive_run
from experiments.search_space import ExperimentSearchSpace
from geometry.constraints import LayoutConstraintError, validate_layout_constraints
from geometry.metrics import evaluate_geometry
from json_values import json_value
from optimization.objectives import mark_pareto_candidates


def run_batch(space: ExperimentSearchSpace, *, output_root=None, pareto=True):
    """Return a JSON-ready report; archive all feasible cases if output_root is set.

    Only generation's LayoutConstraintError is caught. A zero-feasible search
    returns a report without calling M2 (which requires a nonempty batch).
    Pareto compares the whole design, including different geometry values.
    """
    if not isinstance(space, ExperimentSearchSpace):
        raise TypeError("space must be an ExperimentSearchSpace.")
    if not isinstance(pareto, bool):
        raise TypeError("pareto must be bool.")
    plan = space.plan()  # Validate every request before the first generation.
    feasible, rows, infeasible = [], [], []
    for request in plan.requests:
        spec = request.spec
        data = spec.normalized_spec
        try:
            points = spec.generate()
        except LayoutConstraintError as exc:
            infeasible.append({"case_id": spec.case_id, "reason": str(exc)})
            continue
        validation = validate_layout_constraints(points, N=data["N"], **data["geometry"])
        if not validation["feasible"]:
            infeasible.append({"case_id": spec.case_id, "validation": validation})
            continue
        metrics = evaluate_geometry(points, expected_N=data["N"], **data["geometry"],
                                    symmetry_tolerance=data["symmetry_tolerance"])
        feasible.append(request)
        rows.append({"case_id": spec.case_id, "legacy_candidate_id": request.legacy_candidate_id,
                     "N": data["N"], "layout_type": data["layout_type"], **metrics})
    if pareto:
        rows = mark_pareto_candidates(rows)
    report = {
        "schema_version": 1, "planned": plan.planned, "unique": plan.unique,
        "duplicates": plan.duplicates, "feasible": len(feasible), "infeasible": len(infeasible),
        "pareto_count": sum(row["pareto_candidate"] for row in rows) if pareto else None,
        "pareto_scope": "all_unique_feasible_cases" if pareto else None,
        "run_id": None, "run_manifest": None,
        "case_ids": [request.spec.case_id for request in plan.requests],
        "sources": plan.sources, "infeasible_cases": infeasible, "rows": rows,
        "specifications": [request.spec.normalized_spec for request in plan.requests],
        "archive_status": "not_requested" if output_root is None else "no_feasible_cases",
    }
    report = json_value(report, nonfinite="null")
    if output_root is not None and feasible:
        design = space.to_dict()
        path = archive_run(feasible, name=design["name"], description=design.get("description", ""),
                           output_root=output_root)
        report.update(run_id=path.parent.name, run_manifest="run.json", archive_status="completed")
        # Keep M2 manifests/provenance untouched. Failure to write either sidecar
        # propagates; M2's completed status describes its own case archive only.
        space.save(path.parent / "search_space.json")
        save_report(report, path.parent / "search_report.json")
    return report


def save_report(report, path):
    """Save reports also for unarchived / zero-feasible experiments."""
    Path(path).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")
