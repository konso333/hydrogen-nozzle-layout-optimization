"""Isolated run archives and replay through the existing geometry pipeline."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import numpy as np

from config import OUTPUT_ROOT, GeometryConfig
from experiments.provenance import collect_provenance
from experiments.spec import SCHEMA_VERSION, CaseSpec
from geometry.constraints import validate_layout_constraints
from geometry.metrics import evaluate_geometry
from io_utils import export_coordinates, plot_layout
from json_values import json_value
from optimization.cfd_metrics import CFD_FIELDS


CFD_STATUSES = ("not_started", "exported", "running", "completed", "failed")
REPLAY_ATOL_MM = 1e-9


@dataclass(frozen=True)
class CaseRequest:
    spec: CaseSpec
    legacy_candidate_id: str | None = None

    @classmethod
    def from_candidate(cls, candidate, config: GeometryConfig):
        """Bridge old search results; the caller must supply their original config.

        LayoutCandidate does not retain R/d/s_min/tolerance. Re-generation guards
        against stale points/metrics, but cannot infer constraints from coordinates.
        """
        spec = CaseSpec.create(candidate.layout_type, candidate.N, config,
                               candidate.layout_parameters)
        replay = generate_case(spec)
        if not _points_match(candidate.points, replay.points):
            raise ValueError("Candidate coordinates do not match its specification/config.")
        combined = {**replay.metrics, **replay.validation}
        if _report(candidate.metrics) != combined:
            raise ValueError("Candidate metrics do not match its specification/config.")
        return cls(spec, candidate.candidate_id)


@dataclass
class GeneratedCase:
    spec: CaseSpec
    points: list[tuple[float, float]]
    validation: dict
    metrics: dict


def _report(value):
    # N=1 has undefined nearest-neighbour statistics. Strict JSON uses null;
    # this is a missing result, never a zero or an eligible Pareto objective.
    return json_value(value, nonfinite="null")


def generate_case(spec: CaseSpec) -> GeneratedCase:
    spec = CaseSpec.from_normalized(spec.normalized_spec)
    data = spec.normalized_spec
    points = spec.generate()
    validation = validate_layout_constraints(points, N=data["N"], **data["geometry"])
    evaluated = evaluate_geometry(points, expected_N=data["N"], **data["geometry"],
                                  symmetry_tolerance=data["symmetry_tolerance"])
    metrics = {key: value for key, value in evaluated.items() if key not in validation}
    return GeneratedCase(spec, points, _report(validation), _report(metrics))


def _write_json(path: Path, data):
    text = json.dumps(json_value(data), ensure_ascii=False, sort_keys=True,
                      indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_error(exc, *, case_id, relative_path):
    """Keep error semantics and operation context without persisting local paths."""
    # OSError.strerror omits filename/filename2 (unlike str(exc)). For arbitrary
    # messages, redact drive, UNC and POSIX absolute paths conservatively, also
    # covering paths with spaces and escaped backslashes in repr-style messages.
    message = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    message = re.sub(
        r'''(?i)(?:[a-z]:[\\/]|\\\\|//|(?<![\w:])/(?!/))[^\r\n"'<>|]*''',
        "[absolute path omitted]", str(message),
    )
    return {
        "type": type(exc).__name__,  # Retain the original M2 compatibility key.
        "error_type": type(exc).__name__,
        "error_code": getattr(exc, "winerror", None) or getattr(exc, "errno", None),
        "message": message, "case_id": case_id, "relative_path": relative_path,
    }


def archive_run(cases, *, name: str, description: str = "",
                output_root: str | Path = OUTPUT_ROOT / "runs") -> Path:
    """Generate a batch, returning run.json. Duplicate specs are rejected.

    A run directory is exclusively reserved; partial failures retain a failed
    manifest and propagate the exception. Only completed cases enter its index.
    No existing legacy outputs are touched. Results are not automatically ranked.
    """
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        raise ValueError("A nonempty experiment name and string description are required.")
    requests = list(cases)
    if not requests or any(not isinstance(item, CaseRequest) for item in requests):
        raise ValueError("Provide a nonempty sequence of CaseRequest objects.")
    for item in requests:
        CaseSpec.from_normalized(item.spec.normalized_spec)
        if item.legacy_candidate_id is not None and (
            not isinstance(item.legacy_candidate_id, str) or not item.legacy_candidate_id.strip()
        ):
            raise ValueError("legacy_candidate_id must be nonempty text or None.")
    ids = [item.spec.case_id for item in requests]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate case_id in one run; use separate runs for repeats.")

    provenance = collect_provenance()  # Capture before creating any run artifacts.
    created = datetime.now(timezone.utc)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = "run_" + created.strftime("%Y%m%dT%H%M%S%fZ_") + secrets.token_hex(8)
        directory = root / run_id
        try:
            directory.mkdir(exist_ok=False)
            break
        except FileExistsError:
            continue

    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id,
        "created_at": created.isoformat(), "name": name, "description": description,
        "status": "running", "provenance": provenance,
        "planned_case_count": len(requests), "case_count": 0, "cases": [],
        "requested_cases": [
            {"case_id": item.spec.case_id, "normalized_spec": item.spec.normalized_spec,
             "legacy_candidate_id": item.legacy_candidate_id} for item in requests
        ],
        "files": {"cases": "cases"},
        "replay_tolerances": {"coordinates_atol_mm": REPLAY_ATOL_MM,
                              "metrics_atol": 1e-9, "metrics_rtol": 1e-12},
    }
    run_path = directory / "run.json"
    _write_json(run_path, manifest)
    active_case_id = None
    active_relative_path = None
    try:
        for request in requests:
            active_case_id = request.spec.case_id
            active_relative_path = None  # Generation has no output file yet.
            generated = generate_case(request.spec)
            data = generated.spec.normalized_spec
            case_id = generated.spec.case_id
            case_directory = directory / "cases" / case_id
            active_relative_path = case_directory.relative_to(directory).as_posix()
            case_directory.mkdir(parents=True, exist_ok=False)
            files = {"coordinates": "coordinates.csv", "figure": "layout.png",
                     "validation": "validation.json", "metrics": "metrics.json"}
            active_relative_path = (case_directory / files["coordinates"]).relative_to(directory).as_posix()
            export_coordinates(generated.points, case_directory / files["coordinates"])
            active_relative_path = (case_directory / files["figure"]).relative_to(directory).as_posix()
            plot_layout(generated.points, R=data["geometry"]["R"], d=data["geometry"]["d"],
                        title=f"{data['layout_type']} ({case_id[:20]})",
                        png_path=case_directory / files["figure"], metrics=generated.metrics)
            for key in ("validation", "metrics"):
                active_relative_path = (case_directory / files[key]).relative_to(directory).as_posix()
                _write_json(case_directory / files[key], getattr(generated, key))
            active_relative_path = None  # Hashing covers all four artifacts.
            case_manifest = {
                "schema_version": SCHEMA_VERSION, "run_id": run_id, "case_id": case_id,
                "legacy_candidate_id": request.legacy_candidate_id,
                "layout_type": data["layout_type"], "normalized_spec": data,
                "units": data["units"], "validation": generated.validation,
                "metrics": generated.metrics, "files": files,
                "file_sha256": {key: _sha256(case_directory / path) for key, path in files.items()},
                "provenance": {"file": "../../run.json", "json_pointer": "/provenance"},
                "cfd": {"run_id": run_id, "case_id": case_id, "status": "not_started",
                        "metrics": {field: None for field in CFD_FIELDS},
                        "results_file": None},
            }
            active_relative_path = (case_directory / "case.json").relative_to(directory).as_posix()
            _write_json(case_directory / "case.json", case_manifest)
            manifest["cases"].append({
                "case_id": case_id, "legacy_candidate_id": request.legacy_candidate_id,
                "manifest": (case_directory / "case.json").relative_to(directory).as_posix(),
            })
            manifest["case_count"] = len(manifest["cases"])
            active_relative_path = "run.json"
            _write_json(run_path, manifest)
        manifest["status"] = "completed"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        active_case_id = None
        active_relative_path = "run.json"
        _write_json(run_path, manifest)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest.pop("completed_at", None)
        manifest["error"] = _archive_error(
            exc, case_id=active_case_id, relative_path=active_relative_path)
        _write_json(run_path, manifest)
        raise
    return run_path


def _load_case(path: str | Path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported case manifest schema_version.")
    spec = CaseSpec.from_normalized(data["normalized_spec"])
    if spec.case_id != data["case_id"]:
        raise ValueError("case_id does not match normalized_spec.")
    if data["layout_type"] != spec.normalized_spec["layout_type"] or data["units"] != spec.normalized_spec["units"]:
        raise ValueError("Case metadata disagrees with normalized_spec.")
    return data, spec


def reproduce_case(case_manifest: str | Path) -> GeneratedCase:
    """Read only case.json and regenerate; saved CSV/PNG are never inputs."""
    _, spec = _load_case(case_manifest)
    return generate_case(spec)


def _points_match(first, second):
    first, second = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    return first.shape == second.shape and bool(np.allclose(
        first, second, rtol=0, atol=REPLAY_ATOL_MM))


def _metrics_match(first, second):
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(
            _metrics_match(first[key], second[key]) for key in first)
    if isinstance(first, bool) or isinstance(second, bool) or first is None or second is None:
        return first is second
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return bool(np.isclose(first, second, atol=1e-9, rtol=1e-12))
    return first == second


def verify_case(case_manifest: str | Path) -> dict:
    """Check one case's local artifacts and regeneration, not its run provenance.

    Archive checksums detect accidental file changes; they are not signatures.
    Use verify_run() for the full archive linkage. Reproduction itself remains
    possible with only case.json, including when the parent run is unavailable.
    """
    path = Path(case_manifest)
    data, spec = _load_case(path)
    generated = generate_case(spec)
    paths = {}
    for key in ("coordinates", "figure", "validation", "metrics"):
        relative = Path(data["files"][key])
        target = (path.parent / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(path.parent.resolve()):
            raise ValueError("Case file references must remain inside the case directory.")
        paths[key] = target
    with paths["coordinates"].open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    coordinates_match = _points_match(
        [(float(row["x_mm"]), float(row["y_mm"])) for row in rows], generated.points)
    coordinate_metadata_match = all(
        row["nozzle_id"] == str(index) and float(row["z_mm"]) == 0
        for index, row in enumerate(rows, start=1))
    checks = {
        "case_id_match": generated.spec.case_id == data["case_id"],
        "count_match": len(rows) == spec.normalized_spec["N"],
        "coordinates_match": coordinates_match,
        "coordinate_metadata_match": coordinate_metadata_match,
        "validation_match": generated.validation == data["validation"] == json.loads(
            paths["validation"].read_text(encoding="utf-8")),
        "metrics_match": _metrics_match(generated.metrics, data["metrics"]) and
                         _metrics_match(data["metrics"], json.loads(
                             paths["metrics"].read_text(encoding="utf-8"))),
        "file_hashes_match": all(_sha256(target) == data["file_sha256"][key]
                                 for key, target in paths.items()),
        "cfd_identity_match": data["cfd"]["run_id"] == data["run_id"] and
                              data["cfd"]["case_id"] == data["case_id"],
    }
    return {**checks, "overall_match": all(checks.values())}


def _relative_file(base: Path, reference, *, within: Path, label: str) -> Path:
    """Resolve portable archive references, rejecting absolute paths and escapes."""
    if not isinstance(reference, str) or not reference:
        raise ValueError(f"Missing {label} reference.")
    windows = PureWindowsPath(reference)
    if windows.drive or windows.root or Path(reference).is_absolute():
        raise ValueError(f"{label} must be a relative path.")
    target = (base / reference.replace("\\", "/")).resolve()
    if not target.is_relative_to(within.resolve()):
        raise ValueError(f"{label} must remain inside its archive directory.")
    if not target.is_file():
        raise ValueError(f"Missing {label} file.")
    return target


def verify_run(run_manifest: str | Path) -> dict:
    """Verify a completed run's index, provenance, CFD linkage and local cases.

    Missing/inconsistent metadata raises ValueError (unreadable JSON/files may
    raise their native exceptions). Comparable artifact differences are returned
    as False in case_checks / overall_match. No files are written or repaired.
    """
    path = Path(run_manifest).resolve()
    run = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(run, dict) or type(run.get("schema_version")) is not int
            or run["schema_version"] != SCHEMA_VERSION):
        raise ValueError("Unsupported run manifest schema_version.")
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Missing run_id.")
    if run.get("status") != "completed":
        raise ValueError("Full archive verification requires a completed run.")
    if not isinstance(run.get("provenance"), dict) or not run["provenance"]:
        raise ValueError("Missing run provenance.")
    entries, requested = run.get("cases"), run.get("requested_cases")
    if not isinstance(entries, list) or not entries or not isinstance(requested, list):
        raise ValueError("Missing run case index or requested specifications.")
    for field in ("case_count", "planned_case_count"):
        if type(run.get(field)) is not int or run[field] != len(entries):
            raise ValueError(f"Run {field} disagrees with case index.")
    if len(requested) != len(entries):
        raise ValueError("Requested case count disagrees with completed case index.")
    requested_by_id = {}
    for item in requested:
        if not isinstance(item, dict):
            raise ValueError("Invalid requested case entry.")
        spec = CaseSpec.from_normalized(item["normalized_spec"])
        if spec.case_id != item.get("case_id") or spec.case_id in requested_by_id:
            raise ValueError("Invalid or duplicate requested case_id.")
        requested_by_id[spec.case_id] = item

    checked, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("case_id"), str):
            raise ValueError("Invalid run case index entry.")
        case_id = entry["case_id"]
        if case_id in seen or case_id not in requested_by_id:
            raise ValueError("Duplicate or unexpected case_id in run index.")
        seen.add(case_id)
        case_path = _relative_file(path.parent, entry.get("manifest"),
                                   within=path.parent, label="Case manifest")
        case, _ = _load_case(case_path)
        if case.get("run_id") != run_id or case["case_id"] != case_id:
            raise ValueError("Case run_id / case_id disagrees with run index.")
        if not (case.get("legacy_candidate_id") == entry.get("legacy_candidate_id")
                == requested_by_id[case_id].get("legacy_candidate_id")):
            raise ValueError("legacy_candidate_id disagrees with run index/request.")
        reference = case.get("provenance")
        if not isinstance(reference, dict) or reference.get("json_pointer") != "/provenance":
            raise ValueError("Missing or invalid case provenance reference.")
        provenance_path = _relative_file(case_path.parent, reference.get("file"),
                                         within=path.parent, label="Provenance")
        # v1 provenance lives in this exact run.json, not an unrelated readable file.
        if provenance_path != path:
            raise ValueError("Case provenance must refer to its owning run manifest.")
        cfd = case.get("cfd")
        if not isinstance(cfd, dict) or cfd.get("status") not in CFD_STATUSES:
            raise ValueError("Invalid CFD status.")
        if cfd.get("run_id") != run_id or cfd.get("case_id") != case_id:
            raise ValueError("CFD run_id / case_id disagrees with its case.")
        if not isinstance(cfd.get("metrics"), dict) or set(cfd["metrics"]) != set(CFD_FIELDS):
            raise ValueError("Missing CFD metric fields.")
        if cfd["status"] == "not_started" and (
            any(value is not None for value in cfd["metrics"].values())
            or cfd.get("results_file") is not None
        ):
            raise ValueError("CFD not_started requires null metrics and results_file.")
        if cfd.get("results_file") is not None:
            _relative_file(case_path.parent, cfd["results_file"],
                           within=case_path.parent, label="CFD results")
        for key in ("coordinates", "figure", "validation", "metrics"):
            _relative_file(case_path.parent, case["files"][key],
                           within=case_path.parent, label=f"Case {key}")
        result = verify_case(case_path)
        checked.append({"case_id": case_id, **result})
    return {"run_id": run_id, "case_count": len(checked), "case_checks": checked,
            "overall_match": all(item["overall_match"] for item in checked)}
