"""Deterministic, comment-only journal and independently rechecked dry-run files."""

import hashlib
import json
from pathlib import Path
import re

from fluent.spec import LAYERS, build_spec, canonical_json


def generate_journal(spec):
    """Return UTF-8/LF bytes. No executable Fluent syntax is emitted in M7."""
    data = spec.to_dict()
    lines = ["; M7 prepared artifact: comments only; NOT executable-ready"]
    for key in ("run_id", "case_id", "cfd_case_id", "schema_version", "template_version", "ready_to_execute"):
        lines.append("; " + key + " = " + canonical_json(data[key]))
    lines.append("; geometry input = geometry_input.json")
    for layer in LAYERS:
        lines.append("; [" + layer + "]")
        for key, entry in sorted(data[layer].items()):
            lines.append("; " + layer + "." + key + " = " + canonical_json(entry))
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_journal(spec, journal, *, executable_ready=False):
    """Fail closed on encoding, injected commands or any mapping/provenance drift."""
    data = spec.to_dict()
    errors = []
    try:
        if not isinstance(journal, bytes):
            raise ValueError("Journal must be UTF-8 bytes")
        content = journal.decode("utf-8")
        if content.startswith("\ufeff") or "\r" in content or not content.endswith("\n"):
            errors.append("Journal must use UTF-8 without BOM and LF with final newline")
        if re.search(r"[A-Za-z]:[\\/]|\\\\|\{\{|\$\{|(?<![\w])(?:NaN|[+-]?Inf(?:inity)?)(?![\w])", content, re.I):
            errors.append("Absolute path, illegal placeholder or nonfinite token")
        if any(not line.startswith("; ") for line in content.splitlines()):
            errors.append("Forbidden command: M7 only permits comment lines")
        if journal != generate_journal(spec):
            errors.append("Journal differs from verified IDs, units, boundaries, settings or template")
    except (UnicodeError, ValueError) as exc:
        errors.append(str(exc))
    unresolved, unsupported, ready = [], [], []
    for layer in LAYERS:
        for key, item in sorted(data[layer].items()):
            target = {"resolved": ready, "unresolved": unresolved, "unsupported": unsupported}[item["status"]]
            target.append(layer + "." + key)
    if executable_ready:
        errors.append("M7 preparation cannot be marked executable-ready")
    return {**{k: data[k] for k in ("run_id", "case_id", "cfd_case_id")},
            "static_valid": not errors, "ready_to_execute": False, "status": "prepared",
            "ready": ready, "unresolved": unresolved, "unsupported": unsupported,
            "warnings": ["Static validity is not execution readiness", "No solver executed; no numerical CFD results"],
            "errors": errors}


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _artifacts(spec, raw):
    data = spec.to_dict()
    journal = generate_journal(spec)
    report = validate_journal(spec, journal)
    if not report["static_valid"]:
        raise ValueError("Generated journal failed static validation")
    ids = {k: data[k] for k in ("run_id", "case_id", "cfd_case_id")}
    return {"automation_spec.json": _json_bytes(data), "prepare.jou": journal,
            "geometry_input.json": _json_bytes({**ids, "geometry": data["geometry"]}),
            "readiness.json": _json_bytes(report), "coordinates_mm.csv": raw}


def export_automation(package_path, output_directory, *, mesh=None, numerical_settings=None):
    """Create a new directory; never overwrite sources or create/update attempts."""
    spec = build_spec(package_path, mesh=mesh, numerical_settings=numerical_settings)
    raw = (Path(package_path).resolve().parent / "geometry/coordinates.csv").read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec.to_dict()["coordinates_sha256"]:
        raise ValueError("Coordinates changed during export")
    artifacts = _artifacts(spec, raw)
    manifest = {"schema_version": 1, "status": "prepared", "automation_digest": spec.automation_digest,
                **{k: spec.to_dict()[k] for k in ("run_id", "case_id", "cfd_case_id")},
                "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()}}
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=False)
    for name, raw in artifacts.items():
        (directory / name).write_bytes(raw)
    # Completion marker last: partial exports cannot pass verify_automation.
    (directory / "automation_manifest.json").write_bytes(_json_bytes(manifest))
    return directory / "automation_manifest.json"


def verify_automation(package_path, automation_manifest):
    """Read-only rebuild from verified M6 source and saved explicit settings.

    Does not trust saved readiness, converted coordinates, ID headers or checksums
    alone. All six artifacts must match a fresh derivation byte for byte.
    """
    path = Path(automation_manifest).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    spec_path = path.parent / "automation_spec.json"
    if not spec_path.resolve().is_relative_to(path.parent):
        raise ValueError("Artifact symlink escapes directory")
    saved = json.loads(spec_path.read_text(encoding="utf-8"))
    inputs = saved["automation_inputs"]
    if set(inputs) != {"mesh", "numerical_settings"}:
        raise ValueError("Unknown automation input fields")
    spec = build_spec(package_path, **inputs)
    raw = (Path(package_path).resolve().parent / "geometry/coordinates.csv").read_bytes()
    expected = _artifacts(spec, raw)
    expected_manifest = {"schema_version": 1, "status": "prepared", "automation_digest": spec.automation_digest,
        **{k: spec.to_dict()[k] for k in ("run_id", "case_id", "cfd_case_id")},
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in expected.items()}}
    if manifest != expected_manifest or path.read_bytes() != _json_bytes(expected_manifest):
        raise ValueError("Automation manifest identity, digest or file contract mismatch")
    for name, value in expected.items():
        target = path.parent / name
        if not target.resolve().is_relative_to(path.parent) or target.read_bytes() != value:
            raise ValueError(f"Automation artifact mismatch: {name}")
    return validate_journal(spec, expected["prepare.jou"])
