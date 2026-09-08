"""M6-to-M7 mapping: immutable source snapshot, five explicit preparation layers."""

import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re

from cfd.handoff import load_cfd_package
from cfd.spec import CFDSpec, number, text_value
from experiments.spec import canonical_json


TEMPLATE_VERSION = "m7-preparation-1"
SCHEMA_VERSION = 1
MM_TO_M = 0.001  # Exact project unit contract: 1 mm = 0.001 m.
LAYERS = ("geometry", "mesh", "physics", "solver", "post_processing")
BOUNDARY_ROLES = ("fuel_inlet", "oxidizer_inlet", "outlet", "wall", "symmetry")
MESH_FIELDS = {"global_size": "mm", "local_nozzle_size": "mm", "growth_rate": "1",
               "boundary_layer_request": "bool", "strategy": "text"}
SOLVER_FIELDS = {"discretization": "text", "convergence_criterion": "1",
                 "time_step": "s", "max_iterations": "integer"}


def setting(value, source, *, status=None, reason=None):
    status = status or ("unresolved" if value is None else "resolved")
    result = {"status": status, "source": source}
    if value is not None:
        result["value"] = value
    if status != "resolved":
        result["reason"] = reason or "required-user-input"
    return result


def _settings(values, fields):
    if not isinstance(values, dict) or set(values) - set(fields):
        raise ValueError("Unknown mesh or numerical setting.")
    for key, item in values.items():
        kind = fields[key]
        if kind == "bool":
            if type(item) is not bool:
                raise ValueError(f"{key} requires bool.")
        elif kind == "text":
            text_value(item, key)
        else:
            unit = "1" if kind == "integer" else kind
            if not isinstance(item, dict) or set(item) != {"value", "unit"} or item["unit"] != unit:
                raise ValueError(f"{key} requires value and unit {unit}.")
            amount = number(item["value"], key)
            if amount <= 0 or (key == "growth_rate" and amount < 1):
                raise ValueError(f"Invalid positive setting: {key}.")
            if kind == "integer" and type(item["value"]) is not int:
                raise ValueError("max_iterations requires integer.")
    return values


def _portable(value):
    if isinstance(value, dict):
        for key, item in value.items():
            _portable(key)
            _portable(item)
    elif isinstance(value, list):
        for item in value:
            _portable(item)
    elif isinstance(value, str):
        if (re.search(r"[A-Za-z]:[\\/]|\\\\|(?<![\w:])/[A-Za-z_.]", value)
                or any(ord(c) < 32 for c in value) or "{{" in value or "${" in value):
            raise ValueError("Nonportable text or illegal placeholder in automation input.")


def read_coordinates(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != ["nozzle_id", "x_mm", "y_mm", "z_mm"]:
        raise ValueError("Expected M6 coordinate columns in mm.")
    points = []
    for index, row in enumerate(reader, 1):
        if set(row) != set(reader.fieldnames) or row["nozzle_id"] != str(index):
            raise ValueError("Coordinate order or nozzle ID mismatch.")
        xyz = [number(float(row[k]), k) for k in ("x_mm", "y_mm", "z_mm")]
        if xyz[2] != 0:
            raise ValueError("M2 centers must lie in z=0 plane.")
        points.append(xyz)
    return points


@dataclass(frozen=True)
class FluentAutomationSpec:
    """Validated immutable mapped snapshot; constructed only by build_spec.

    Direct construction is disabled to keep mapping and source validation together.
    to_dict returns a detached JSON snapshot. Executable/version are never inputs.
    """
    _canonical: str

    def __init__(self, *args, **kwargs):
        raise TypeError("Use build_spec with a verified M6 package.")

    def to_dict(self):
        return json.loads(self._canonical)

    @property
    def automation_digest(self):
        return hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()


def build_spec(package_path, *, mesh=None, numerical_settings=None):
    package_path = Path(package_path).resolve()
    package = load_cfd_package(package_path)
    mesh = _settings({} if mesh is None else mesh, MESH_FIELDS)
    numerical = _settings({} if numerical_settings is None else numerical_settings, SOLVER_FIELDS)
    case_path = (package_path.parent / package["geometry"]["case_manifest"]).resolve()
    case_raw = case_path.read_bytes()
    if hashlib.sha256(case_raw).hexdigest() != package["geometry"]["case_manifest_sha256"]:
        raise ValueError("Geometry manifest changed after package validation.")
    case = json.loads(case_raw.decode("utf-8"))
    raw = (package_path.parent / "geometry/coordinates.csv").read_bytes()
    if hashlib.sha256(raw).hexdigest() != package["coordinates_sha256"]:
        raise ValueError("Coordinates changed after package validation.")
    points = read_coordinates(raw)
    geom = case["normalized_spec"]
    if len(points) != geom["N"] or package["geometry"]["units"]["length"] != "mm":
        raise ValueError("Coordinate count or units mismatch.")
    physical = CFDSpec.from_normalized(package["normalized_spec"]).normalized_spec
    condition, config = physical["operating_condition"], physical["simulation_config"]
    source = "cfd_case.json.normalized_spec"
    geometry = {
        "chamber_radius": setting({"value": geom["geometry"]["R"], "unit": "mm"}, "M2.normalized_spec.geometry.R"),
        "nozzle_diameter": setting({"value": geom["geometry"]["d"], "unit": "mm"}, "M2.normalized_spec.geometry.d"),
        "centers_mm": setting(points, "geometry/coordinates.csv"),
        "centers_m": setting([[v * MM_TO_M for v in p] for p in points], "geometry/coordinates.csv; mm_to_m"),
        "mm_to_m": setting(MM_TO_M, "M7 unit contract: 1 mm = 0.001 m"),
        "axial_length": setting(None, source + ".simulation_config.extras"),
        "boundary_names": setting(None, "No verified 3D faces available"),
        "builder": setting(None, "M7 scope", status="unsupported", reason="Intermediate geometry only; CAD builder not implemented"),
    }
    if "axial_length" in config["extras"]:
        length = config["extras"]["axial_length"]
        length_source = source + ".simulation_config.extras.axial_length"
        if length is None:
            geometry["axial_length"] = setting(None, length_source)
        elif (isinstance(length, dict) and set(length) == {"value", "unit"}
              and length["unit"] == "mm" and number(length["value"], "axial_length") > 0):
            geometry["axial_length"] = setting(length, length_source)
        else:
            geometry["axial_length"] = setting(length, length_source, status="unsupported",
                reason="Axial length mapping requires positive value in mm; no implicit conversion")
    physics = {k: setting(v, source + ".operating_condition." + k)
               for k, v in condition.items() if k != "extras"}
    for key, value in config.items():
        if key == "extras":
            continue
        physics[key] = setting(value, source + ".simulation_config." + key)
    geometry["dimensionality"] = physics.pop("dimensionality")
    if config["solver"] is not None and config["solver"].lower() != "fluent":
        physics["solver"] = setting(config["solver"], source + ".simulation_config.solver",
                                    status="unsupported", reason="Only Fluent concepts are mapped")
    physics["boundary_setup"] = setting(None, "M6 condition plus verified geometry faces",
        reason="Confirm topology, face roles, per-inlet flows/species, pressure reference, outlet and wall conditions; equivalence ratio alone is insufficient")
    physics["model_adapter"] = setting(None, "M7 scope", status="unsupported",
        reason="Model names are declared concepts only; material library, reaction mechanism and version-specific commands need verification")
    for group, values in (("operating_condition", condition), ("simulation_config", config)):
        for key, value in values["extras"].items():
            if group == "simulation_config" and key == "axial_length":
                continue
            physics[f"{group}.extras.{key}"] = setting(value, source + f".{group}.extras.{key}",
                status="unsupported", reason="No registered mapping for this physical extension; source retained")
    mesh_layer = {k: setting(mesh.get(k), "automation_inputs.mesh." + k) for k in MESH_FIELDS}
    mesh_layer["builder"] = setting(None, "M7 scope", status="unsupported", reason="Mesh generator not implemented")
    solver = {k: setting(numerical.get(k), "automation_inputs.numerical_settings." + k) for k in SOLVER_FIELDS}
    solver["steady_or_transient"] = physics.pop("steady_or_transient")
    solver["command_adapter"] = setting(None, "M7 scope", status="unsupported", reason="Comment-only preparation template; no executable TUI adapter")
    requirements = {
        "hydrogen_conversion": "Declared inlet/outlet faces: species H2 mass-flow reports, direction and reverse-flow check",
        "outlet_temperature_mean": "Declared outlet faces: face area and static temperature; area-weighted report",
        "outlet_temperature_std": "Same outlet faces/sample: face area and static temperature; export for population area-weighted variance",
        "pressure_loss": "Declared inlet/outlet faces: area and total pressure, shared pressure reference; two area-weighted reports",
        "max_wall_heat_flux": "Declared chamber wall faces: local wall-normal total heat flux; export then max absolute magnitude",
    }
    post = {key: setting({"contract": meta, "required_inputs": requirements[key],
                         "sample": "Researcher must declare steady sample or transient averaging window"},
                        "cfd_case.json.result_contract.metrics." + key,
                        status="unsupported", reason="Extraction plan only; report/field exporter not implemented")
            for key, meta in package["result_contract"]["metrics"].items()}
    post["NOx"] = setting(None, "M6 pending contract", status="unsupported", reason="pending_contract")
    data = {"schema_version": SCHEMA_VERSION, "template_version": TEMPLATE_VERSION,
            **{k: package[k] for k in ("run_id", "case_id", "cfd_case_id")},
            "status": "prepared", "ready_to_execute": False,
            "coordinates_sha256": package["coordinates_sha256"],
            "source_cfd_spec": physical,
            "automation_inputs": {"mesh": mesh, "numerical_settings": numerical},
            "geometry": geometry, "mesh": mesh_layer, "physics": physics,
            "solver": solver, "post_processing": post}
    # Only caller text needs path/placeholder screening. M6 metric formulas
    # contain division slashes which are not filesystem paths.
    _portable({"physical": physical, "mesh": mesh, "numerical": numerical})
    spec = object.__new__(FluentAutomationSpec)
    object.__setattr__(spec, "_canonical", canonical_json(data))
    return spec
