"""Opt-in, file-based CFD handoff and validated execution records."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
from uuid import uuid4

from cfd.metrics import metric_definitions, validate_metrics
from cfd.spec import CFDSpec, structured, text_value
from experiments.archive import (
    CFD_STATUSES as LEGACY_STATUSES, _archive_error, _relative_file, _sha256,
    _write_json, verify_run,
)
from experiments.provenance import collect_provenance
from experiments.spec import canonical_json
from json_values import json_value


CFD_STATUSES = (*LEGACY_STATUSES, "prepared")
_TRANSITIONS = {
    "not_started": {"prepared", "exported", "running", "failed"},
    "prepared": {"exported", "running", "failed"},
    "exported": {"running", "failed"},
    "running": {"completed", "failed"},
    "completed": set(), "failed": set(),
}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _lock(directory):
    """Fail fast on concurrent writers; a crash may require manual lock removal."""
    lock = directory / ".write.lock"
    with lock.open("x", encoding="utf-8"):
        pass
    try:
        yield
    finally:
        lock.unlink()


def select_cases(run_manifest, case_ids):
    """Validate a complete M2 archive, then select an explicit ordered ID list."""
    run_path = Path(run_manifest).resolve()
    if not verify_run(run_path)["overall_match"]:
        raise ValueError("M2 archive verification failed.")
    run = _read(run_path)
    if not isinstance(case_ids, (list, tuple)) or not case_ids or any(not isinstance(i, str) for i in case_ids):
        raise ValueError("Select an explicit nonempty case_id list.")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Duplicate selected case_id.")
    index = {item["case_id"]: item["manifest"] for item in run["cases"]}
    if set(case_ids) - set(index):
        raise ValueError("Selected case_id is absent from the specified run.")
    paths = [_relative_file(run_path.parent, index[i], within=run_path.parent,
                            label="geometry manifest") for i in case_ids]
    if any(_read(path)["validation"].get("feasible") is not True for path in paths):
        raise ValueError("Only feasible geometry can be handed off.")
    return paths


def export_cfd_package(run_manifest, spec, *, output_root, required_metrics):
    """Export one explicitly selected geometry/condition; never overwrite a package.

    Repeated exports are deliberately refused. Load the existing package and
    create_attempt() for a repeat execution of the same physical specification.
    """
    spec = CFDSpec.from_normalized(spec.normalized_spec)
    data = spec.normalized_spec
    run_path = Path(run_manifest).resolve()
    case_path = select_cases(run_path, [data["case_id"]])[0]
    run, case = _read(run_path), _read(case_path)
    run_id = run["run_id"]
    if not re.fullmatch(r"run_[A-Za-z0-9_]+", run_id):
        raise ValueError("Unsafe run_id for package path.")
    contract = metric_definitions(required_metrics)
    root = Path(output_root).resolve()
    # Flatten storage to fit Windows paths. Full scientific IDs remain in JSON;
    # the full SHA-256 storage key distinguishes the same CFD spec across runs.
    storage_key = hashlib.sha256(canonical_json({
        "run_id": run_id, "cfd_case_id": spec.cfd_case_id,
    }).encode("utf-8")).hexdigest()
    directory = root / storage_key
    # A relative link across drives cannot be made portable; check before writes.
    geometry_ref = Path(os.path.relpath(case_path, directory)).as_posix()
    run_ref = Path(os.path.relpath(run_path, directory)).as_posix()
    coordinates = _relative_file(case_path.parent, case["files"]["coordinates"],
                                 within=case_path.parent, label="coordinates")
    provenance = collect_provenance()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "geometry").mkdir()
    (directory / "attempts").mkdir()
    shutil.copyfile(coordinates, directory / "geometry/coordinates.csv")
    manifest = {
        "schema_version": 1, "run_id": run_id, "case_id": data["case_id"],
        "cfd_case_id": spec.cfd_case_id, "operating_condition_id": spec.operating_condition_id,
        "normalized_spec": data, "status": "prepared", "created_at": _stamp(),
        "geometry": {"run_manifest": run_ref, "case_manifest": geometry_ref,
                     "case_manifest_sha256": _sha256(case_path), "units": case["units"]},
        "files": {"coordinates": "geometry/coordinates.csv", "attempts": "attempts"},
        "coordinates_sha256": _sha256(coordinates), "provenance": provenance,
        "result_contract": {"schema_version": 1, "metrics": contract},
    }
    path = directory / "cfd_case.json"
    _write_json(path, manifest)  # Published last; incomplete exports cannot load.
    return path


def load_cfd_package(path):
    """Check physical identity, the M2 linkage, contract and copied coordinates."""
    path = Path(path).resolve()
    data = _read(path)
    fields = {"schema_version", "run_id", "case_id", "cfd_case_id", "operating_condition_id",
              "normalized_spec", "status", "created_at", "geometry", "files",
              "coordinates_sha256", "provenance", "result_contract"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("Incomplete or unknown CFD package fields.")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported CFD package schema.")
    if not isinstance(data["provenance"], dict) or not data["provenance"]:
        raise ValueError("Missing package provenance.")
    text_value(data["created_at"], "created_at")
    spec = CFDSpec.from_normalized(data["normalized_spec"])
    if (data["case_id"] != spec.normalized_spec["case_id"] or
            data["cfd_case_id"] != spec.cfd_case_id or
            data["operating_condition_id"] != spec.operating_condition_id):
        raise ValueError("CFD package identity mismatch.")
    if data["status"] != "prepared" or data["files"] != {
        "coordinates": "geometry/coordinates.csv", "attempts": "attempts"
    }:
        raise ValueError("Invalid handoff status or file contract.")
    contract = data["result_contract"]
    if (not isinstance(contract, dict) or type(contract.get("schema_version")) is not int or contract["schema_version"] != 1
            or not isinstance(contract.get("metrics"), dict)):
        raise ValueError("Invalid result contract.")
    if any(not isinstance(meta, dict) or type(meta.get("required")) is not bool
           for meta in contract["metrics"].values()):
        raise ValueError("Invalid metric metadata.")
    expected = metric_definitions([key for key, meta in contract["metrics"].items()
                                   if meta.get("required") is True])
    if contract != {"schema_version": 1, "metrics": expected}:
        raise ValueError("Result contract metadata mismatch.")
    # Only these two read-only provenance references may leave the package.
    # Their target must be the exact verified M2 run and its indexed case.
    geometry = data["geometry"]
    if not isinstance(geometry, dict) or set(geometry) != {
        "run_manifest", "case_manifest", "case_manifest_sha256", "units"
    }:
        raise ValueError("Incomplete geometry source contract.")
    def reference(key):
        ref = geometry[key]
        if not isinstance(ref, str) or not ref or PureWindowsPath(ref).drive or PureWindowsPath(ref).root or Path(ref).is_absolute():
            raise ValueError("Geometry references must be relative.")
        return (path.parent / ref.replace("\\", "/")).resolve()
    run_path = reference("run_manifest")
    selected = select_cases(run_path, [data["case_id"]])[0]
    case_path = reference("case_manifest")
    case = _read(selected)
    if (selected != case_path or case["run_id"] != data["run_id"] or
            _sha256(case_path) != geometry["case_manifest_sha256"] or
            geometry["units"] != case["units"]):
        raise ValueError("Geometry source mismatch.")
    coordinates = _relative_file(path.parent, data["files"]["coordinates"],
                                 within=path.parent, label="handoff coordinates")
    if not (_sha256(coordinates) == data["coordinates_sha256"] == case["file_sha256"]["coordinates"]):
        raise ValueError("Handoff coordinate checksum mismatch.")
    return data


def _attempt_path(package_path, attempt_id):
    if not isinstance(attempt_id, str) or not re.fullmatch(r"attempt_[0-9a-f]{32}", attempt_id):
        raise ValueError("Invalid attempt_id.")
    directory = Path(package_path).resolve().parent
    target = directory / "attempts" / (attempt_id + ".json")
    if not target.resolve().is_relative_to(directory):
        raise ValueError("Attempt path escapes package.")
    return target


def _execution_environment(value):
    """Caller-declared solver/extraction environment; never inferred locally.

    solver_version stays exclusively in provenance.solver_version for API and
    file compatibility. Missing/None means unknown, not the handoff machine.
    """
    if value is None:
        return None
    fields = {"platform", "hostname", "machine_label", "git_commit", "git_branch",
              "git_dirty", "python_version", "extraction_tool_version"}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError("Unknown execution_environment field; solver_version belongs in provenance.solver_version.")
    value = json_value(value)
    result = dict.fromkeys(sorted(fields))
    for key, item in value.items():
        if item is not None:
            if key == "git_dirty":
                if type(item) is not bool:
                    raise ValueError("execution_environment.git_dirty must be bool or null.")
            else:
                text_value(item, f"execution_environment.{key}")
            result[key] = item
    return result if any(item is not None for item in result.values()) else None


def _provenance(value, *, executed):
    required = {"solver_version", "data_kind", "numerical_settings"}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - (required | {"execution_environment"})):
        raise ValueError("Execution provenance needs solver_version, data_kind and numerical_settings.")
    if value["data_kind"] not in {"external_cfd", "synthetic/test-only"}:
        raise ValueError("Invalid execution data_kind.")
    version = value["solver_version"]
    if version is not None:
        text_value(version, "solver_version")
    if executed and version is None:
        raise ValueError("Executed results require a recorded solver_version.")
    if not isinstance(value["numerical_settings"], dict):
        raise ValueError("numerical_settings must be an object.")
    return {**value, "numerical_settings": structured(value["numerical_settings"], physical=False),
            "execution_environment": _execution_environment(value.get("execution_environment"))}


def create_attempt(package_path, *, solver_version=None, data_kind="external_cfd", numerical_settings=None,
                   execution_environment=None):
    package = load_cfd_package(package_path)
    attempt_id = "attempt_" + uuid4().hex
    path = _attempt_path(package_path, attempt_id)
    record = {"schema_version": 1, **{k: package[k] for k in ("run_id", "case_id", "cfd_case_id")},
              "attempt_id": attempt_id, "status": "not_started", "created_at": _stamp(),
              "metrics": dict.fromkeys(package["result_contract"]["metrics"]), "error": None,
              "provenance": _provenance({"solver_version": solver_version, "data_kind": data_kind,
                                         "execution_environment": execution_environment,
                                         "numerical_settings": {} if numerical_settings is None else numerical_settings}, executed=False)}
    with _lock(path.parent):
        if path.exists():
            raise FileExistsError("Attempt already exists.")
        _write_json(path, record)
    return path


def _validate_record(record, package, package_path):
    fields = {"schema_version", "run_id", "case_id", "cfd_case_id", "attempt_id", "status",
              "created_at", "metrics", "error", "provenance"}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("Incomplete or unknown result record fields.")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("Unsupported result schema.")
    for key in ("run_id", "case_id", "cfd_case_id"):
        if record[key] != package[key]:
            raise ValueError(f"Result {key} mismatch.")
    _attempt_path(package_path, record["attempt_id"])
    text_value(record["created_at"], "created_at")
    status = record["status"]
    if not isinstance(status, str) or status not in CFD_STATUSES:
        raise ValueError("Invalid CFD status.")
    result = {**record, "metrics": validate_metrics(record["metrics"], package["result_contract"]["metrics"], status),
              "provenance": _provenance(record["provenance"], executed=status in {"running", "completed"})}
    error = record["error"]
    if error is not None:
        if status != "failed" or not isinstance(error, dict) or set(error) != {
            "error_type", "message", "solver_exit_status", "relative_log_path"
        }:
            raise ValueError("Error details are only allowed on failed executions.")
        text_value(error["error_type"], "error_type")
        if not isinstance(error["message"], str):
            raise ValueError("Error message must be text.")
        code = error["solver_exit_status"]
        if code is not None and type(code) is not int:
            raise ValueError("solver_exit_status must be integer or null.")
        ref = error["relative_log_path"]
        if ref is not None:
            _relative_file(Path(package_path).resolve().parent, ref,
                           within=Path(package_path).resolve().parent, label="solver log")
        cleaned = _archive_error(RuntimeError(error["message"]), case_id=record["case_id"], relative_path=ref)
        result["error"] = {**error, "message": cleaned["message"]}
    elif status == "failed":
        raise ValueError("failed requires structured error details.")
    if status == "failed" and any(v is not None for v in result["metrics"].values()):
        _provenance(record["provenance"], executed=True)
    return result


def load_attempt(package_path, attempt_id):
    package = load_cfd_package(package_path)
    record = _read(_attempt_path(package_path, attempt_id))
    if record.get("attempt_id") != attempt_id:
        raise ValueError("Attempt filename identity mismatch.")
    return _validate_record(record, package, package_path)


def import_cfd_results(package_path, results):
    """Atomically replace one execution record after validating all four IDs.

    Input is a complete record dict or JSON filename. Start from load_attempt(),
    edit status/metrics/error, then import. Terminal records are immutable;
    byte-equivalent normalized re-imports are idempotent.
    """
    package = load_cfd_package(package_path)
    payload = _read(results) if isinstance(results, (str, Path)) else results
    record = _validate_record(payload, package, package_path)
    path = _attempt_path(package_path, record["attempt_id"])
    with _lock(path.parent):
        old = _validate_record(_read(path), package, package_path)
        if old["attempt_id"] != record["attempt_id"] or old["created_at"] != record["created_at"]:
            raise ValueError("Attempt identity or creation time mismatch.")
        if old["provenance"]["data_kind"] != record["provenance"]["data_kind"]:
            raise ValueError("An execution cannot change its synthetic/external classification.")
        if old["status"] == "running":
            for key in ("solver_version", "numerical_settings"):
                if old["provenance"][key] != record["provenance"][key]:
                    raise ValueError("Running execution settings are fixed; create a new attempt.")
            previous = old["provenance"]["execution_environment"] or {}
            current = record["provenance"]["execution_environment"] or {}
            if any(value is not None and current.get(key) != value for key, value in previous.items()):
                raise ValueError("Known execution environment is fixed; create a new attempt.")
        if old["status"] in {"completed", "failed"}:
            if old != record:
                raise ValueError("Terminal attempt is immutable; create a new attempt.")
            return path
        if record["status"] != old["status"] and record["status"] not in _TRANSITIONS[old["status"]]:
            raise ValueError(f"Invalid CFD transition: {old['status']} -> {record['status']}.")
        _write_json(path, record)
    return path
