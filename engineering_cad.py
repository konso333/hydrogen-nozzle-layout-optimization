"""Place an explicitly declared internal-flow STEP template at verified centres.

This is a CAD construction plan, not a CAD kernel, mesh builder or Fluent adapter.
The source profile remains an explicit input to read-only verification.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from cfd.spec import number
from experiments.spec import canonical_json
from engineering_fluent import SPEC_NAME, verify_engineering_fluent


PLAN_NAME = "cad_placement_plan.json"
MANIFEST_NAME = "cad_placement_manifest.json"
PROFILE_FIELDS = {"schema_version", "kind", "source_step", "source_sha256", "source_length_unit",
                  "nozzle_outer_diameter_mm", "reference_origin", "reference_x_direction",
                  "reference_axis_direction"}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_manifest(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(_bytes(value))
    temporary.replace(path)


def _step_unit(raw):
    """Recognise only simple SI metre/mm declarations; do not parse BRep topology."""
    text = raw.decode("latin-1")
    if not text.lstrip().startswith("ISO-10303-21;") or not text.rstrip().endswith("END-ISO-10303-21;"):
        raise ValueError("Expected an uncompressed STEP Part 21 file.")
    # Ignore comments and quoted metadata when inspecting entity declarations.
    text = re.sub(r"'(?:[^']|'')*'|/\*.*?\*/", "", text, flags=re.DOTALL)
    units = []
    si = r"SI_UNIT\s*\(\s*(\$|\.MILLI\.)\s*,\s*\.METRE\.\s*\)"
    length = r"LENGTH_UNIT\s*\(\s*\)"
    named = r"NAMED_UNIT\s*\(\s*\*\s*\)"
    for entity in re.findall(r"#\d+\s*=\s*(.*?);", text, re.DOTALL):
        if not re.search(r"\bLENGTH_UNIT\s*\(", entity):
            continue
        matches = re.findall(si, entity)
        remaining = re.sub(f"{si}|{length}|{named}", "", entity)
        if (len(matches) != 1 or len(re.findall(length, entity)) != 1
                or len(re.findall(named, entity)) != 1 or re.sub(r"[()\s]", "", remaining)):
            raise ValueError("Only simple SI metre/mm STEP length units are supported.")
        units.append("mm" if matches[0] == ".MILLI." else "m")
    if not units or len(set(units)) != 1:
        raise ValueError("STEP length units are missing or mixed; an explicit CAD conversion is required.")
    return units[0]


def _vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} requires three explicit coordinates.")
    return [number(item, label) for item in value]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _template(profile_path):
    path = Path(profile_path).resolve()
    raw_profile = path.read_bytes()
    profile = json.loads(raw_profile.decode("utf-8-sig"))
    if (not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS
            or type(profile["schema_version"]) is not int or profile["schema_version"] != 1
            or profile["kind"] != "single_nozzle_internal_fluid"):
        raise ValueError("Expected a v1 single_nozzle_internal_fluid template profile.")
    source = profile["source_step"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("An explicit source_step path is required.")
    source = (path.parent / source).resolve()
    if source.suffix.casefold() not in {".step", ".stp"}:
        raise ValueError("Use an internal-flow STEP template, not an existing mesh or case.")
    expected_hash = profile["source_sha256"]
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("An explicit lowercase source_sha256 is required.")
    raw = source.read_bytes()
    if _sha(raw) != expected_hash:
        raise ValueError("STEP template SHA-256 mismatch.")
    if profile["source_length_unit"] not in ("mm", "m") or _step_unit(raw) != profile["source_length_unit"]:
        raise ValueError("source_length_unit must match the supported STEP SI declaration.")
    if number(profile["nozzle_outer_diameter_mm"], "nozzle_outer_diameter_mm") <= 0:
        raise ValueError("A positive template nozzle outer diameter is required.")
    origin = _vector(profile["reference_origin"], "reference_origin")
    x = _vector(profile["reference_x_direction"], "reference_x_direction")
    axis = _vector(profile["reference_axis_direction"], "reference_axis_direction")
    if (not math.isclose(_dot(x, x), 1, rel_tol=0, abs_tol=1e-10)
            or not math.isclose(_dot(axis, axis), 1, rel_tol=0, abs_tol=1e-10)
            or not math.isclose(_dot(x, axis), 0, rel_tol=0, abs_tol=1e-10)):
        raise ValueError("Reference X and axial directions must be unit length and perpendicular.")
    # Rows of a proper rotation: X, Z cross X, Z. Never reflect or rescale hardware.
    y = [axis[1] * x[2] - axis[2] * x[1], axis[2] * x[0] - axis[0] * x[2],
         axis[0] * x[1] - axis[1] * x[0]]
    metadata = {key: value for key, value in profile.items() if key != "source_step"}
    return source, raw, metadata, _sha(raw_profile), origin, [x, y, axis]


def _artifacts(handoff_path, automation_manifest, template_profile):
    handoff = Path(handoff_path).resolve()
    automation = Path(automation_manifest).resolve()
    verify_engineering_fluent(handoff, automation)
    raw_spec = automation.with_name(SPEC_NAME).read_bytes()
    spec = json.loads(raw_spec.decode("utf-8"))
    if spec["source_cfd_spec"]["simulation_config"]["dimensionality"] != "3D":
        raise ValueError("CAD placement requires an explicitly declared 3D CFD specification.")
    source, raw_step, metadata, profile_hash, origin, rotation = _template(template_profile)
    geometry = spec["geometry"]
    if metadata["nozzle_outer_diameter_mm"] != geometry["nozzle_outer_diameter"]["value"]["value"]:
        raise ValueError("Template nozzle outer diameter differs from the verified engineering configuration.")
    scale = 1 if metadata["source_length_unit"] == "mm" else 1000
    instances = []
    for index, centre in enumerate(geometry["centers_mm"]["value"], 1):
        matrix = [[scale * value for value in row] + [centre[i] - scale * _dot(row, origin)]
                  for i, row in enumerate(rotation)] + [[0, 0, 0, 1]]
        if not all(math.isfinite(value) for row in matrix for value in row):
            raise ValueError("CAD placement transform overflow.")
        instances.append({"nozzle_id": index, "centre_mm": centre,
                          "transform_mm_from_source": matrix})
    raw_coordinates = (handoff.parent / "geometry/coordinates.csv").read_bytes()
    if _sha(raw_coordinates) != spec["coordinates_sha256"]:
        raise ValueError("Coordinates changed during CAD preparation.")
    ids = {key: spec[key] for key in ("run_id", "case_id", "cfd_case_id")}
    plan = {"schema_version": 1, "kind": "engineering_cad_placement_plan", **ids,
            "automation_digest": _sha(canonical_json(spec).encode("utf-8")),
            "source_automation_manifest_sha256": _sha(automation.read_bytes()),
            "source_template_profile_sha256": profile_hash,
            "template": {**metadata, "file": "template.step"},
            "target_frame": "layout_xy_origin_z_axis", "target_length_unit": "mm",
            "transform_convention": "row_major_4x4_times_source_column_xyz1",
            "orientation_policy": "same_reference_frame_for_all_nozzles",
            "scope": "independent_internal_passage_instances_without_chamber_or_manifolds",
            "nozzle_count": len(instances), "instances": instances,
            "placement_plan_valid": True, "source_geometry_checked": False,
            "cad_built": False, "mesh_generated": False, "ready_to_execute": False,
            "scientific_eligible": False,
            "pending_checks": ["CAD topology and internal passage identity", "transformed CAD containment and intersections",
                               "shared chamber or manifold construction if required", "actual boundary association",
                               "mesh generation and quality acceptance", "Fluent execution readiness"]}
    files = {PLAN_NAME: _bytes(plan), "template.step": raw_step, "coordinates_mm.csv": raw_coordinates}
    manifest = {"schema_version": 1, "kind": "engineering_cad_placement", "status": "prepared", **ids,
                "placement_digest": _sha(canonical_json(plan).encode("utf-8")),
                "ready_to_execute": False, "files": {name: _sha(raw) for name, raw in files.items()}}
    return files, manifest, source


def export_engineering_cad(handoff_path, automation_manifest, template_profile, output_directory) -> Path:
    """Copy a frozen template and export a construction plan; no CAD/solver process."""
    files, manifest, source = _artifacts(handoff_path, automation_manifest, template_profile)
    directory = Path(output_directory).resolve()
    handoff = Path(handoff_path).resolve()
    package = _read(handoff.with_name("cfd_case.json"))
    archive = (handoff.parent / package["geometry"]["run_manifest"]).resolve().parent
    protected = (handoff.parent, archive, Path(automation_manifest).resolve().parent, source.parent,
                 Path(template_profile).resolve().parent)
    if any(directory.is_relative_to(root) for root in protected):
        raise ValueError("CAD output must be outside all source package/template directories.")
    directory.mkdir(parents=True, exist_ok=False)
    marker = directory / MANIFEST_NAME
    try:
        for name, raw in files.items():
            (directory / name).write_bytes(raw)
        _write_manifest(marker, manifest)
        verify_engineering_cad(handoff_path, automation_manifest, template_profile, marker)
    except Exception as exc:
        _write_manifest(marker, {"schema_version": 1, "kind": "engineering_cad_placement",
                                 "status": "failed", "error_type": type(exc).__name__})
        raise
    return marker


def verify_engineering_cad(handoff_path, automation_manifest, template_profile, manifest_path) -> dict:
    """Replay from explicit verified source inputs; never trust edited transforms."""
    path = Path(manifest_path).resolve()
    saved = _read(path)
    if path.name != MANIFEST_NAME or not isinstance(saved, dict) or saved.get("status") != "prepared":
        raise ValueError("Expected a prepared cad_placement_manifest.json.")
    files, manifest, _ = _artifacts(handoff_path, automation_manifest, template_profile)
    if path.read_bytes() != _bytes(manifest):
        raise ValueError("CAD placement manifest mismatch.")
    for name, raw in files.items():
        target = path.parent / name
        if not target.resolve().is_relative_to(path.parent) or target.read_bytes() != raw:
            raise ValueError(f"CAD placement artifact mismatch: {name}.")
    return {"placement_plan_valid": True, "source_geometry_checked": False, "cad_built": False,
            "mesh_generated": False, "ready_to_execute": False, "scientific_eligible": False}
