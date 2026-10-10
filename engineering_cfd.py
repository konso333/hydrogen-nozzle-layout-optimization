"""Engineering-aware M6 handoff; prepares files without starting any solver."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from cfd import CFDSpec, export_cfd_package, load_cfd_package, metric_definitions
from engineering_archive import verify_engineering_archive
from engineering_config import CONFIG_FIELDS, EngineeringGeometryConfig
from experiments.spec import canonical_json


MOUNTING_EXTENSION = "engineering_mounting"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def make_engineering_cfd_spec(case_id, config, *, operating_condition, simulation_config):
    """Include all four mounting dimensions in M6 identity, excluding labels.

    These dimensions describe installation constraints, not a 3D fluid domain.
    Caller-owned physical extensions are retained; the mounting key is reserved
    by this adapter and cannot be supplied or overridden by a profile.
    """
    config.require_complete()
    base = CFDSpec.create(case_id, operating_condition, simulation_config).normalized_spec
    for group in ("operating_condition", "simulation_config"):
        if any(key.casefold() == MOUNTING_EXTENSION for key in base[group]["extras"]):
            raise ValueError(f"{MOUNTING_EXTENSION} is supplied only by the verified engineering archive.")
    simulation = base["simulation_config"]
    simulation["extras"][MOUNTING_EXTENSION] = {
        field: {"value": getattr(config, field), "unit": "mm"}
        for field in CONFIG_FIELDS if field != "name"
    }
    return CFDSpec.create(case_id, base["operating_condition"], simulation)


def _profile(value, case_id, config):
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "operating_condition", "simulation_config", "required_metrics"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Expected a v1 engineering CFD profile with condition, simulation and required metrics.")
    required = value["required_metrics"]
    if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
        raise ValueError("required_metrics must be an explicit nonempty list of metric names.")
    contract = metric_definitions(required)
    spec = make_engineering_cfd_spec(
        case_id, config, operating_condition=value["operating_condition"],
        simulation_config=value["simulation_config"],
    )
    normalized = spec.normalized_spec
    del normalized["simulation_config"]["extras"][MOUNTING_EXTENSION]
    profile = {"schema_version": 1, "operating_condition": normalized["operating_condition"],
               "simulation_config": normalized["simulation_config"],
               "required_metrics": [key for key, meta in contract.items() if meta["required"]]}
    return spec, profile


def _source(manifest_path, case_id):
    path = Path(manifest_path).resolve()
    if path.name != "engineering_run.json":
        raise ValueError("Use the archived engineering_run.json without renaming it.")
    if not verify_engineering_archive(path)["overall_match"]:
        raise ValueError("Engineering archive verification failed.")
    manifest = _read(path)
    if case_id not in [entry["case_id"] for entry in manifest["cases"]]:
        raise ValueError("Select one explicit case_id from this engineering archive.")
    config = EngineeringGeometryConfig.from_dict(manifest["configuration"])
    case_path = path.parent / "cases" / case_id / "engineering.json"
    return path, manifest, config, case_path


def _expected_handoff(package_path, profile):
    package_path = Path(package_path).resolve()
    package = load_cfd_package(package_path)
    # Derive the engineering source from M6's already-verified M2 reference.
    # Never follow source paths supplied by an edited engineering sidecar.
    run_path = (package_path.parent / package["geometry"]["run_manifest"]).resolve()
    source, manifest, config, case_path = _source(
        run_path.with_name("engineering_run.json"), package["case_id"],
    )
    spec, normalized_profile = _profile(profile, package["case_id"], config)
    if canonical_json(spec.normalized_spec) != canonical_json(package["normalized_spec"]):
        raise ValueError("CFD physical specification differs from the engineering source and profile.")
    return {
        "schema_version": 1, "kind": "engineering_cfd_handoff", "status": "prepared",
        "ready_to_execute": False, "geometry_scope": "mounting_constraints_only",
        "run_id": manifest["run_id"], "case_id": package["case_id"],
        "cfd_case_id": spec.cfd_case_id, "operating_condition_id": spec.operating_condition_id,
        "configuration": config.to_dict(), "engineering_case": _read(case_path),
        "profile": normalized_profile,
        "source": {"engineering_manifest": Path(os.path.relpath(source, package_path.parent)).as_posix(),
                   "engineering_manifest_sha256": _sha256(source),
                   "engineering_case_sha256": _sha256(case_path)},
        "files": {"m6_package": "cfd_case.json"}, "m6_package_sha256": _sha256(package_path),
    }


def prepare_engineering_cfd(manifest_path, *, case_id, profile, output_root) -> Path:
    """Prepare one explicitly selected case/condition using unchanged M6 APIs.

    The engineering completion marker is published last. Invalid inputs create
    no output; later I/O failures may leave a failed/incomplete package, which
    cannot pass the engineering verifier. Existing packages are never replaced.
    No execution attempt or numerical result is created.
    """
    source, _, config, _ = _source(manifest_path, case_id)
    spec, normalized_profile = _profile(profile, case_id, config)
    root = Path(output_root).resolve()
    if root.is_relative_to(source.parent):
        raise ValueError("CFD output must be outside the source engineering archive.")
    package_path = export_cfd_package(
        source.with_name("run.json"), spec, output_root=root,
        required_metrics=normalized_profile["required_metrics"],
    )
    marker = package_path.with_name("engineering_handoff.json")
    try:
        _write_json(marker, _expected_handoff(package_path, normalized_profile))
        if not verify_engineering_cfd(marker)["overall_match"]:
            raise ValueError("Engineering CFD handoff verification failed.")
    except Exception as exc:
        _write_json(marker, {"schema_version": 1, "kind": "engineering_cfd_handoff",
                             "status": "failed", "error_type": type(exc).__name__})
        raise
    return marker


def verify_engineering_cfd(manifest_path) -> dict:
    """Read-only replay of the M6 package, engineering source and full profile."""
    path = Path(manifest_path).resolve()
    saved = _read(path)
    if (path.name != "engineering_handoff.json" or not isinstance(saved, dict)
            or type(saved.get("schema_version")) is not int or saved["schema_version"] != 1
            or saved.get("kind") != "engineering_cfd_handoff" or saved.get("status") != "prepared"):
        raise ValueError("Expected a prepared v1 engineering_handoff.json.")
    expected = _expected_handoff(path.with_name("cfd_case.json"), saved["profile"])
    package = load_cfd_package(path.with_name("cfd_case.json"))
    contract = metric_definitions(expected["profile"]["required_metrics"])
    checks = {
        "engineering_handoff_match": canonical_json(saved) == canonical_json(expected),
        "result_contract_match": package["result_contract"] == {"schema_version": 1, "metrics": contract},
    }
    return {**checks, "overall_match": all(checks.values())}
