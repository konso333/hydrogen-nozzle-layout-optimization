"""Engineering Fluent preparation with distinct mounting and fluid dimensions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from cfd.spec import number
from engineering_cfd import MOUNTING_EXTENSION, verify_engineering_cfd
from experiments.spec import canonical_json
from fluent.artifacts import generate_journal, validate_journal
from fluent.spec import build_spec, setting


FLUID_EXTENSION = "engineering_fluid_domain"
FLUID_FRAME = "layout_xy_origin_z_axis"
MANIFEST_NAME = "engineering_automation_manifest.json"
SPEC_NAME = "engineering_automation_spec.json"


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_manifest(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(_json_bytes(value))
    temporary.replace(path)


def _fluid_domain(extras):
    if any(key.casefold() == FLUID_EXTENSION and key != FLUID_EXTENSION for key in extras):
        raise ValueError(f"Use the exact physical extension name {FLUID_EXTENSION}.")
    domain = extras.get(FLUID_EXTENSION)
    fields = {"coordinate_frame", "chamber_radius", "axial_length"}
    if domain is None:
        return dict.fromkeys(fields)
    if not isinstance(domain, dict) or set(domain) != fields:
        raise ValueError("Fluid domain requires coordinate_frame, chamber_radius and axial_length; use null for unknowns.")
    if domain["coordinate_frame"] not in (None, FLUID_FRAME):
        raise ValueError(f"Fluid domain coordinate_frame must be {FLUID_FRAME} or null.")
    for key in ("chamber_radius", "axial_length"):
        value = domain[key]
        if value is None:
            continue
        if (not isinstance(value, dict) or set(value) != {"value", "unit"}
                or value["unit"] != "mm" or number(value["value"], key) <= 0):
            raise ValueError(f"Fluid {key} requires a positive value in mm.")
        if domain["coordinate_frame"] is None:
            raise ValueError("Known fluid dimensions require an explicit coordinate_frame.")
    if (domain["axial_length"] is not None and extras.get("axial_length") is not None
            and canonical_json(domain["axial_length"]) != canonical_json(extras["axial_length"])):
        raise ValueError("Conflicting legacy and engineering fluid axial_length declarations.")
    return domain


@dataclass(frozen=True)
class EngineeringFluentSpec:
    _canonical: str

    def __init__(self, *args, **kwargs):
        raise TypeError("Use build_engineering_fluent_spec with a verified engineering handoff.")

    def to_dict(self):
        return json.loads(self._canonical)

    @property
    def automation_digest(self):
        return _sha256(self._canonical.encode("utf-8"))


def build_engineering_fluent_spec(handoff_path, *, mesh=None, numerical_settings=None):
    """Reuse M7 validation/conversion, mapping engineering dimensions explicitly.

    Physical inputs are read only from the verified M6 specification. Preparation
    settings cannot override the fluid domain or the archived mounting profile.
    """
    path = Path(handoff_path).resolve()
    if not verify_engineering_cfd(path)["overall_match"]:
        raise ValueError("Engineering CFD handoff verification failed.")
    source = _read(path)
    data = build_spec(path.with_name("cfd_case.json"), mesh=mesh,
                      numerical_settings=numerical_settings).to_dict()
    geometry = data["geometry"]
    geometry.pop("chamber_radius")
    geometry.pop("nozzle_diameter")
    geometry.pop("axial_length")
    extras = data["source_cfd_spec"]["simulation_config"]["extras"]
    mounting_source = "cfd_case.json.normalized_spec.simulation_config.extras." + MOUNTING_EXTENSION
    for field, key in {
        "installation_radius_mm": "installation_radius",
        "nozzle_outer_diameter_mm": "nozzle_outer_diameter",
        "nozzle_edge_gap_mm": "minimum_nozzle_edge_gap",
        "wall_clearance_mm": "minimum_wall_clearance",
    }.items():
        geometry[key] = setting(extras[MOUNTING_EXTENSION][field], mounting_source + "." + field)
    original_geometry = source["engineering_case"]["geometry_case_spec"]["geometry"]
    for field, key in (("R", "validation_radius"), ("s_min", "minimum_center_distance")):
        geometry[key] = setting({"value": original_geometry[field], "unit": "mm"},
                                "engineering_handoff.json.engineering_case.geometry_case_spec.geometry." + field)
    domain = _fluid_domain(extras)
    domain_source = "cfd_case.json.normalized_spec.simulation_config.extras." + FLUID_EXTENSION
    for field, key in (("coordinate_frame", "fluid_domain_frame"),
                       ("chamber_radius", "chamber_radius"), ("axial_length", "axial_length")):
        geometry[key] = setting(domain[field], domain_source + "." + field,
                                reason="Explicit fluid-domain declaration required; mounting dimensions are not fluid dimensions")
    if domain["chamber_radius"] is not None:
        radius = domain["chamber_radius"]["value"]
        tolerance = original_geometry["tolerance"]
        if any(math.hypot(*point[:2]) > radius + tolerance for point in geometry["centers_mm"]["value"]):
            raise ValueError("A nozzle centre lies outside the declared circular chamber projection.")
    geometry["nozzle_passage_geometry"] = setting(None, "No verified internal CAD passages supplied",
        reason="Outer diameter does not define the fuel and oxidizer flow passages")
    for extension in (MOUNTING_EXTENSION, FLUID_EXTENSION):
        data["physics"].pop("simulation_config.extras." + extension, None)
    if "axial_length" in extras:
        data["physics"]["simulation_config.extras.axial_length"] = setting(
            extras["axial_length"], "cfd_case.json.normalized_spec.simulation_config.extras.axial_length",
            status="unsupported", reason="Legacy axial_length alone does not declare a fluid-domain frame")
    data.update(kind="engineering_fluent_preparation", template_version="engineering-m7-preparation-1",
                source_engineering_handoff_sha256=_sha256(path.read_bytes()))
    spec = object.__new__(EngineeringFluentSpec)
    object.__setattr__(spec, "_canonical", canonical_json(data))
    return spec


def _artifacts(spec, raw):
    data = spec.to_dict()
    journal = generate_journal(spec)
    readiness = validate_journal(spec, journal)
    if not readiness["static_valid"]:
        raise ValueError("Engineering journal failed static validation.")
    ids = {key: data[key] for key in ("run_id", "case_id", "cfd_case_id")}
    files = {
        SPEC_NAME: _json_bytes(data), "prepare_engineering.jou": journal,
        "engineering_geometry_input.json": _json_bytes({"schema_version": 1,
            "kind": "engineering_geometry_input", **ids, "geometry": data["geometry"]}),
        "readiness.json": _json_bytes(readiness), "coordinates_mm.csv": raw,
    }
    manifest = {"schema_version": 1, "kind": "engineering_fluent_preparation", "status": "prepared",
                "ready_to_execute": False, **ids, "automation_digest": spec.automation_digest,
                "files": {name: _sha256(value) for name, value in files.items()}}
    return files, manifest, readiness


def _prepared_artifacts(handoff_path, *, mesh=None, numerical_settings=None):
    path = Path(handoff_path).resolve()
    spec = build_engineering_fluent_spec(path, mesh=mesh, numerical_settings=numerical_settings)
    raw = (path.parent / "geometry/coordinates.csv").read_bytes()
    if _sha256(raw) != spec.to_dict()["coordinates_sha256"]:
        raise ValueError("Coordinates changed during engineering preparation.")
    return _artifacts(spec, raw)


def export_engineering_fluent(handoff_path, output_directory, *, mesh=None, numerical_settings=None) -> Path:
    """Export a new comment-only preparation; never create or change attempts."""
    source = Path(handoff_path).resolve()
    files, manifest, _ = _prepared_artifacts(source, mesh=mesh, numerical_settings=numerical_settings)
    package = _read(source.with_name("cfd_case.json"))
    archive_root = (source.parent / package["geometry"]["run_manifest"]).resolve().parent
    directory = Path(output_directory).resolve()
    if directory.is_relative_to(source.parent) or directory.is_relative_to(archive_root):
        raise ValueError("Preparation output must be outside the CFD package and source geometry archive.")
    directory.mkdir(parents=True, exist_ok=False)
    marker = directory / MANIFEST_NAME
    try:
        for name, value in files.items():
            (directory / name).write_bytes(value)
        _write_manifest(marker, manifest)
        if not verify_engineering_fluent(source, marker)["static_valid"]:
            raise ValueError("Engineering preparation verification failed.")
    except Exception as exc:
        _write_manifest(marker, {"schema_version": 1, "kind": "engineering_fluent_preparation",
                                 "status": "failed", "error_type": type(exc).__name__})
        raise
    return marker


def verify_engineering_fluent(handoff_path, manifest_path) -> dict:
    """Read-only replay from the verified engineering/M6/M2 source, not saved geometry."""
    path = Path(manifest_path).resolve()
    manifest = _read(path)
    if (path.name != MANIFEST_NAME or not isinstance(manifest, dict)
            or manifest.get("status") != "prepared"):
        raise ValueError("Expected a prepared engineering_automation_manifest.json.")
    def fixed_file(name):
        target = path.parent / name
        if not target.resolve().is_relative_to(path.parent):
            raise ValueError("Engineering artifact escapes its directory.")
        return target
    saved = _read(fixed_file(SPEC_NAME))
    inputs = saved["automation_inputs"]
    if not isinstance(inputs, dict) or set(inputs) != {"mesh", "numerical_settings"}:
        raise ValueError("Unknown engineering preparation settings.")
    files, expected, report = _prepared_artifacts(handoff_path, **inputs)
    if path.read_bytes() != _json_bytes(expected):
        raise ValueError("Engineering preparation manifest mismatch.")
    for name, value in files.items():
        if fixed_file(name).read_bytes() != value:
            raise ValueError(f"Engineering preparation artifact mismatch: {name}.")
    return report
