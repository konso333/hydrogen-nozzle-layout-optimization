"""Read-only M6/M7 validation, target selection and local launch preview."""

from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from cfd import load_cfd_package
from fluent import build_spec, verify_automation
from fluent.spec import read_coordinates
from fluent_pilot.local import FluentLaunchPlan, inspect_environment
from fluent_pilot.research import ResearchInputGate, NOX, EXEMPTIONS, portable_reference
from fluent_pilot.target import select_pilot_request, validate_pilot_points


# Only registered paths count as package evidence. In particular, never hide
# numerical choices in M6 physical extras to work around M7's limited schema.
_CONDITION_FIELDS = {
    "inlet_temperature": "inlet_temperature", "inlet_pressure": "inlet_pressure",
    "equivalence_ratio": "equivalence_ratio",
}
_CONFIG_FIELDS = {"turbulence_model": "turbulence_model", "combustion_model": "combustion_model"}
_M7_FIELDS = {
    "global_mesh_strategy": "mesh.strategy", "nozzle_refinement": "mesh.local_nozzle_size",
    "boundary_layer_strategy": "mesh.boundary_layer_request",
    "discretization": "numerical_settings.discretization",
}
_REVIEW_ONLY = frozenset({"stable_operating_envelope", "mesh_reference", "convergence_reference",
                          "grid_independence_plan", "benchmark_operating_condition",
                          "flame_measurement_locations"})


def _present(value):
    if value is None:
        return False
    if isinstance(value, dict):
        return bool(value) and all(_present(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_present(v) for v in value)
    return not isinstance(value, str) or bool(value.strip())


def _at(value, path):
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _package_readiness(package, automation, digest, gate, local_config, package_review):
    """Check input presence/mapping, not CFD correctness or execution capability."""
    physical = package["normalized_spec"]
    condition, config = physical["operating_condition"], physical["simulation_config"]
    placeholder = not any(_present(v) for group in (condition, config) for v in group.values())
    dimension = config["dimensionality"]
    solver = config["solver"]
    compatibility = {
        "dimensionality": "unresolved" if dimension is None else
            "compatible" if dimension == local_config.dimension == "3D" else "conflict",
        "solver_family": "unresolved" if solver is None else
            "compatible" if solver.casefold() == "fluent" else "conflict",
        "adapter_verified": False,
    }
    binding = "unresolved"
    if package_review is not None:
        if not isinstance(package_review, dict) or set(package_review) != {
                "cfd_case_id", "automation_digest", "evidence_reference"}:
            raise ValueError("Package review needs exact CFD ID, automation digest and portable evidence")
        portable_reference(package_review["evidence_reference"])
        binding = "matched" if (package_review["cfd_case_id"] == package["cfd_case_id"]
            and package_review["automation_digest"] == digest) else "conflict"
    mapping = []
    for item in gate.items:
        key, category = item.key, item.category
        path, value, status = None, None, "mapping_unresolved"
        if key in NOX:
            status = "pending_contract"
        elif item.status == "not_required":
            kind = next(d.kind for d in gate.decisions if key in EXEMPTIONS[d.kind])
            expected = {"nonstaged_baseline": ("extras.pilot_baseline_scope", "non_staged"),
                        "steady_sampling": ("steady_or_transient", "steady"),
                        "transient_sampling": ("steady_or_transient", "transient"),
                        "laminar_inlet": ("turbulence_model", "laminar")}[kind]
            path = "M6.simulation_config." + expected[0]
            actual = _at(config, expected[0])
            status = "not_required" if actual == expected[1] else "mapping_unresolved" if actual is None else "conflict"
        elif key == "operating_mode":
            path = "M6.simulation_config.{dimensionality,steady_or_transient,solver}"
            value = {k: config[k] for k in ("dimensionality", "steady_or_transient", "solver")}
        elif key == "physical_model_configuration":
            path = "M6.simulation_config.extras.physical_model_configuration"
            raw = config["extras"].get(key, {})
            value = {k: raw.get(k) for k in ("energy_equation", "species_reaction_enablement",
                                           "density_compressibility", "gravity_decision")} if isinstance(raw, dict) else None
        elif key == "outlet_definition":
            path = "M6.operating_condition.extras.outlet_definition"
            raw = condition["extras"].get(key, {})
            value = {k: raw.get(k) for k in ("boundary_type", "backflow_specification")} if isinstance(raw, dict) else None
        elif key in {"h2_inlet_definition", "oxidizer_inlet_definition"}:
            path = "M6.operating_condition.extras." + key + " + declared mole fractions"
            raw = condition["extras"].get(key)
            composition = "fuel_mole_fractions" if key == "h2_inlet_definition" else "oxidizer_mole_fractions"
            value = {"boundary_definition": raw, "composition": condition[composition]}
        elif key == "required_result_subset":
            path = "M6.result_contract.metrics + digest-bound package review"
            value = [k for k, meta in package["result_contract"]["metrics"].items() if meta["required"]]
            status = "mapped" if binding == "matched" and item.status == "confirmed" else "mapping_unresolved"
        elif key in _REVIEW_ONLY:
            path = "digest-bound research reference (no physical value)"
            status = "mapped" if binding == "matched" and item.status == "confirmed" else "mapping_unresolved"
        elif key in _CONDITION_FIELDS:
            path = "M6.operating_condition." + _CONDITION_FIELDS[key]
            value = condition[_CONDITION_FIELDS[key]]
        elif key in _CONFIG_FIELDS:
            path = "M6.simulation_config." + _CONFIG_FIELDS[key]
            value = config[_CONFIG_FIELDS[key]]
        elif key == "combustor_axial_length":
            path = "M6.simulation_config.extras.axial_length"
            value = config["extras"].get("axial_length")
        elif key in _M7_FIELDS:
            path = "M7.automation_inputs." + _M7_FIELDS[key]
            value = _at(automation["automation_inputs"], _M7_FIELDS[key])
        elif category in {"mesh", "numerical_solver"} or key in {"steady_sample_definition", "transient_time_window"}:
            status = "pending_mapping_contract"
        else:
            group = "operating_condition" if category in {"boundary_conditions", "experiment_alignment"} else "simulation_config"
            path = "M6." + group + ".extras." + key
            value = physical[group]["extras"].get(key)
        if value is not None and key not in _REVIEW_ONLY and key != "required_result_subset":
            status = "mapped" if _present(value) else "mapping_unresolved"
        mapping.append({"key": key, "category": category, "gate_status": item.status,
                        "package_mapping_status": status, "source_path": path,
                        "reason": "evidence confirmed but package mapping unresolved"
                            if item.status == "confirmed" and status in {"mapping_unresolved", "pending_mapping_contract"}
                            else status})
    valid = {"mapped", "not_required", "pending_contract"}
    physical_ready = not placeholder and all(
        m["package_mapping_status"] in valid for m in mapping
        if m["category"] in {"geometry", "boundary_conditions", "physics"})
    compatible = all(compatibility[k] == "compatible" for k in ("dimensionality", "solver_family"))
    return {"research_ready": gate.readiness()["pilot_ready"],
            "physical_spec_status": "preparation_placeholder" if placeholder else
                "mapped_inputs_present" if physical_ready else "incomplete",
            "package_physics_ready": physical_ready,
            "package_ready_for_pilot": gate.readiness()["pilot_ready"] and physical_ready and compatible
                and binding == "matched" and all(m["package_mapping_status"] in valid for m in mapping),
            "package_review_status": binding,
            "package_review": dict(package_review) if package_review is not None else None,
            "compatibility": compatibility,
            "package_input_mapping": mapping, "nox_research_ready": False}


def preflight(package_path, automation_manifest, local_config, *, research_gate=None, package_review=None):
    """Reject corrupt/wrong-target packages; report missing software/research inputs.

    Does not export or repair M2/M6/M7 files, create attempts or run processes.
    Returned local report must not be stored as a scientific manifest.
    """
    gate = ResearchInputGate.initial() if research_gate is None else research_gate
    if not isinstance(gate, ResearchInputGate):
        raise TypeError("research_gate must be ResearchInputGate")
    audit = verify_automation(package_path, automation_manifest)
    if audit["static_valid"] is not True or audit["ready_to_execute"] is not False:
        raise ValueError("M8A requires a static-valid, non-executable M7 package")
    request = select_pilot_request()
    if audit["case_id"] != request.spec.case_id:
        raise ValueError("M6/M7 package is not the selected M5 H1 hex7 S10 case")
    points = np.asarray(read_coordinates(
        (Path(package_path).resolve().parent / "geometry/coordinates.csv").read_bytes()))[:, :2]
    pilot = validate_pilot_points(points, request.spec.normalized_spec)
    manifest = json.loads(Path(automation_manifest).read_text(encoding="utf-8"))
    package = load_cfd_package(package_path)
    inputs = json.loads((Path(automation_manifest).parent / "automation_spec.json").read_text(encoding="utf-8"))["automation_inputs"]
    spec = build_spec(package_path, **inputs)
    if spec.automation_digest != manifest["automation_digest"]:
        raise ValueError("M7 package changed during preflight")
    return {"schema_version": 1, "scope": "M8A local preflight only",
            "identity": {**{k: audit[k] for k in ("run_id", "case_id", "cfd_case_id")},
                         "automation_digest": manifest["automation_digest"]},
            "pilot_target": pilot, **inspect_environment(local_config),
            **_package_readiness(package, spec.to_dict(), spec.automation_digest, gate, local_config, package_review),
            "automation_static_valid": audit["static_valid"],
            "ready_to_execute": False, "automation_audit": audit,
            "research_gate": gate.to_dict(),
            "blocking_inputs": [asdict(i) for i in gate.blocking_inputs],
            "group_dependency_report": gate.group_dependency_report(),
            "launch_plan": FluentLaunchPlan(local_config).to_dict(),
            "execution_allowed": False, "attempt_created": False}
