"""Verified engineering sidecars around unmodified M2 geometry archives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from engineering_config import EngineeringGeometryConfig
from engineering_geometry import validate_engineering_layout
from engineering_search import run_engineering_search
from experiments.archive import CaseRequest, archive_run, verify_run
from experiments.spec import CaseSpec, canonical_json
from io_utils.plot_engineering_layout import plot_engineering_layout


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _replay_report(raw: bytes):
    report = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(report, dict) or report.get("kind") != "engineering_layout_search":
        raise ValueError("Expected an engineering_layout_search report.")
    config = EngineeringGeometryConfig.from_dict(report["configuration"])
    config.require_complete()
    saved_space = report["search_space"]
    if not isinstance(saved_space, dict) or len(saved_space.get("blocks", [])) != 1:
        raise ValueError("Engineering searches require one installation geometry block.")
    design = {key: value for key, value in saved_space.items() if key != "blocks"}
    design["groups"] = saved_space["blocks"][0]["groups"]
    replay = run_engineering_search(
        config=config, design=design, tolerance=report["validation_geometry"]["tolerance"],
    )
    # Compare the whole scientific report, including specs, source paths,
    # constraints, counts and Pareto marks. Provenance is historical metadata.
    if set(report) - set(replay) - {"provenance"}:
        raise ValueError("Unknown engineering search report fields.")
    for key, value in replay.items():
        if key not in report or canonical_json(report[key]) != canonical_json(value):
            raise ValueError(f"Search report differs from replay: {key}.")
    return config, replay


def _selected_specs(report, selection):
    rows = {row["case_id"]: row for row in report["rows"]}
    if isinstance(selection, str):
        if selection not in {"pareto", "all"}:
            raise ValueError("selection must be pareto, all, or a list of feasible case IDs.")
        ids = [case_id for case_id, row in rows.items()
               if selection == "all" or row["pareto_candidate"]]
    elif isinstance(selection, list) and all(isinstance(item, str) for item in selection):
        ids = selection
    else:
        raise ValueError("Explicit selection must be a list of feasible case IDs.")
    if not ids or len(set(ids)) != len(ids) or any(case_id not in rows for case_id in ids):
        raise ValueError("Selection must be nonempty, unique, and contain only feasible case IDs.")
    specs = {spec.case_id: spec for spec in map(CaseSpec.from_normalized, report["specifications"])}
    return [specs[case_id] for case_id in ids]


def _engineering_case(config, spec):
    data = spec.normalized_spec
    points = spec.generate()
    check = validate_engineering_layout(
        points, config=config, N=data["N"], tolerance=data["geometry"]["tolerance"],
    )
    if not check["validation"]["feasible"]:
        raise ValueError("Replayed candidate fails its engineering clearances.")
    return points, {"schema_version": 1, "kind": "engineering_case",
                    "geometry_case_id": spec.case_id, "geometry_case_spec": data, **check}


def archive_engineering_candidates(
    report_path: str | Path, *, output_dir: str | Path, selection="pareto",
) -> Path:
    """Replay a saved search and export selected cases to a new directory.

    M2 artifacts and identities stay unchanged. Physical inputs, clearance
    checks and installation figures are added as separate engineering files.
    """
    directory = Path(output_dir)
    if directory.exists():
        raise FileExistsError(f"Output already exists: {directory}. Choose a new directory.")
    raw = Path(report_path).read_bytes()
    config, report = _replay_report(raw)
    specs = _selected_specs(report, selection)
    prepared = [(spec, *_engineering_case(config, spec)) for spec in specs]
    # No files are created until the report and every selected case pass.
    directory.mkdir(parents=True, exist_ok=False)
    run_path = archive_run([CaseRequest(spec) for spec in specs], output_root=directory,
                           name=report["search_space"]["name"],
                           description="Engineering selection from a replayed geometry search.")
    root = run_path.parent
    manifest_path = root / "engineering_run.json"
    manifest = {
        "schema_version": 1, "kind": "engineering_archive", "status": "running",
        "run_id": root.name, "configuration": config.to_dict(), "selection": selection,
        "case_id_scope": "effective_geometry_specification_only",
        "planned_case_count": len(specs), "case_count": 0, "cases": [],
        "files": {"m2_run": "run.json", "source_search_report": "search_report.json"},
        "source_report_sha256": hashlib.sha256(raw).hexdigest(),
    }
    _write_json(manifest_path, manifest)
    try:
        (root / "search_report.json").write_bytes(raw)
        for spec, points, check in prepared:
            case_root = root / "cases" / spec.case_id
            _write_json(case_root / "engineering.json", check)
            plot_engineering_layout(points, config, spec.normalized_spec["layout_type"],
                                    case_root / "engineering_layout.png")
            manifest["cases"].append({
                "case_id": spec.case_id,
                "files": {"engineering": f"cases/{spec.case_id}/engineering.json",
                          "installation_figure": f"cases/{spec.case_id}/engineering_layout.png"},
                "file_sha256": {"engineering": _sha256(case_root / "engineering.json"),
                                "installation_figure": _sha256(case_root / "engineering_layout.png")},
            })
            manifest["case_count"] = len(manifest["cases"])
            _write_json(manifest_path, manifest)
        manifest["status"] = "completed"
        _write_json(manifest_path, manifest)
        if not verify_engineering_archive(manifest_path)["overall_match"]:
            raise ValueError("Engineering archive verification failed.")
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error_type"] = type(exc).__name__
        _write_json(manifest_path, manifest)
        raise
    return manifest_path


def verify_engineering_archive(manifest_path: str | Path) -> dict:
    """Read-only verification of M2 linkage, source replay and engineering files."""
    path = Path(manifest_path)
    root = path.parent
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("kind") != "engineering_archive" or manifest.get("status") != "completed"):
        raise ValueError("Expected a completed v1 engineering archive.")
    config, report = _replay_report((root / "search_report.json").read_bytes())
    specs = _selected_specs(report, manifest["selection"])
    ids = [spec.case_id for spec in specs]
    m2 = json.loads((root / "run.json").read_text(encoding="utf-8"))
    checks = {
        "m2_match": verify_run(root / "run.json")["overall_match"],
        "configuration_match": canonical_json(manifest["configuration"]) == canonical_json(config.to_dict()),
        "source_report_hash_match": _sha256(root / "search_report.json") == manifest["source_report_sha256"],
        "linkage_match": (
            manifest["run_id"] == m2["run_id"]
            and manifest["case_id_scope"] == "effective_geometry_specification_only"
            and manifest["files"] == {"m2_run": "run.json", "source_search_report": "search_report.json"}
            and type(manifest["planned_case_count"]) is int and type(manifest["case_count"]) is int
            and manifest["planned_case_count"] == manifest["case_count"] == len(ids)
            and [entry["case_id"] for entry in manifest["cases"]] == ids
            and [entry["case_id"] for entry in m2["cases"]] == ids
        ),
        "engineering_cases_match": len(manifest["cases"]) == len(specs),
    }
    for entry, spec in zip(manifest["cases"], specs):
        # Resolve only fixed paths derived from validated CaseSpec IDs. Never
        # follow arbitrary path strings from an edited engineering manifest.
        case_root = root / "cases" / spec.case_id
        _, expected = _engineering_case(config, spec)
        saved = json.loads((case_root / "engineering.json").read_text(encoding="utf-8"))
        expected_entry = {
            "case_id": spec.case_id,
            "files": {"engineering": f"cases/{spec.case_id}/engineering.json",
                      "installation_figure": f"cases/{spec.case_id}/engineering_layout.png"},
            "file_sha256": {"engineering": _sha256(case_root / "engineering.json"),
                            "installation_figure": _sha256(case_root / "engineering_layout.png")},
        }
        checks["engineering_cases_match"] &= (
            entry == expected_entry and canonical_json(saved) == canonical_json(expected)
        )
    return {**checks, "overall_match": all(checks.values())}
