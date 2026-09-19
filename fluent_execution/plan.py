"""Fail-closed shakedown planning; no process or attempt creation."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import string

from cfd import load_cfd_package
from cfd.spec import structured
from fluent import verify_automation
from fluent.spec import read_coordinates
from fluent_pilot import inspect_environment, ResearchInputGate
from fluent_pilot.research import CATALOG, portable_reference
from fluent_execution.baseline import load_baseline


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).resolve().read_text(encoding="utf-8"))


def output_name(value):
    # Flat output contract: no traversal, drive, ADS, aliases or subdirectories.
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(c in value for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in value)
            or value.endswith((".", " "))
            or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", value, re.I)):
        raise ValueError("Expected a portable output filename")
    return value


def _contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _contains(actual[k], v)
                                               for k, v in expected.items())
    return type(actual) is type(expected) and actual == expected or (
        type(actual) in (int, float) and type(expected) in (int, float) and actual == expected)


def _text(value):
    if not isinstance(value, str) or not value.strip() or value.strip().casefold() in {
            "not_required", "ignore", "force", "skip", "skip_validation", "override", "unknown", "pending"}:
        return False
    try:
        portable_reference(value)
        return True
    except ValueError:
        return False


def _quantity(value, unit, *, minimum=0, positive=False):
    return (isinstance(value, dict) and set(value) == {"value", "unit"} and value["unit"] == unit
            and type(value["value"]) in (int, float) and math.isfinite(value["value"])
            and (value["value"] > minimum if positive else value["value"] >= minimum))


def _exact(actual, expected):
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and set(actual) == set(expected)
                and all(_exact(actual[k], v) for k, v in expected.items()))
    if type(expected) in (int, float):
        return type(actual) in (int, float) and actual == expected
    return type(actual) is type(expected) and actual == expected


def _composition(value, expected=None):
    if not isinstance(value, dict) or not value:
        return False
    species = set(expected) if expected is not None else {"H2", "O2", "N2", "H2O"}
    if (set(value) != species if expected is not None else not set(value) <= species):
        return False
    if any(not _quantity(q, "1") or q["value"] > 1 for q in value.values()):
        return False
    if not math.isclose(sum(q["value"] for q in value.values()), 1, rel_tol=0, abs_tol=1e-12):
        return False
    return expected is None or all(math.isclose(value[k]["value"], v, rel_tol=0, abs_tol=1e-12)
                                   for k, v in expected.items())


def _baseline_valid(physical, baseline):
    """Extra M6 extensions are allowed, but each baseline field is exact."""
    for group, fields in baseline.items():
        actual = physical[group]
        for key, expected in fields.items():
            if key != "extras":
                if not _exact(actual.get(key), expected):
                    return False
                continue
            for name, value in expected.items():
                candidate = actual["extras"].get(name)
                if name in {"fuel_inlet", "oxidizer_inlet"}:
                    if not isinstance(candidate, dict) or set(candidate) != set(value):
                        return False
                    fractions = {k: v["value"] for k, v in value["mass_fractions"].items()}
                    if not _composition(candidate["mass_fractions"], fractions):
                        return False
                    if any(not _exact(candidate[k], v) for k, v in value.items() if k != "mass_fractions"):
                        return False
                elif not _exact(candidate, value):
                    return False
    return True


def _physical_mapping(physical):
    extras = physical["simulation_config"]["extras"]
    condition = physical["operating_condition"]["extras"]
    domain = extras.get("computational_domain")
    checks = {"computational_domain": isinstance(domain, dict)
              and set(domain) == {"dimension", "description", "geometry_source"}
              and domain["dimension"] == "3D" and _text(domain["description"]) and _text(domain["geometry_source"]),
              "density_model": _text(extras.get("density_model"))}
    for key, description in (("gravity_decision", "specification"), ("radiation_model_decision", "model")):
        value = extras.get(key)
        checks[key] = (isinstance(value, dict) and set(value) == {"enabled", description}
                       and type(value["enabled"]) is bool and _text(value[description]))
    materials = extras.get("species_material_database")
    checks["species_material_database"] = (isinstance(materials, dict)
        and set(materials) == {"species", "reference"} and isinstance(materials["species"], list)
        and len(materials["species"]) == 4 and all(isinstance(v, str) for v in materials["species"])
        and set(materials["species"]) == {"H2", "O2", "N2", "H2O"} and _text(materials["reference"]))
    backflow = condition.get("outlet_backflow")
    checks["outlet_backflow"] = (isinstance(backflow, dict)
        and set(backflow) == {"temperature", "mass_fractions", "turbulence_specification"}
        and _quantity(backflow["temperature"], "K", positive=True)
        and _composition(backflow["mass_fractions"]) and _text(backflow["turbulence_specification"]))
    checks["hydraulic_diameter_topology"] = _exact(extras.get("inlet_topology"),
        {"fuel_inlet": "circular", "oxidizer_inlet": "circular"})
    return checks


# Fixed M8B applicability, using only existing M8A keys. No caller exemptions.
# Mapping tokens refer to checks below, never to caller-supplied ready booleans.
SHAKEDOWN_MAPPING = {
    "operating_mode": "mode", "real_3d_chamber_construction": "domain",
    "nozzle_physical_length": "baseline", "nozzle_internal_geometry_or_equivalent_inlet": "topology",
    "actual_boundary_face_mapping": "adapter", "h2_inlet_definition": "baseline",
    "oxidizer_inlet_definition": "baseline", "inlet_temperature": "baseline",
    "inlet_turbulence_specification": "topology", "turbulence_model": "baseline",
    "combustion_model": "baseline", "hydrogen_chemical_mechanism": "baseline",
    "physical_model_configuration": "physics", "species_material_database": "materials",
    "radiation_model_decision": "physics", "pressure_reference": "baseline",
    "outlet_definition": "outlet", "wall_thermal_condition": "baseline",
    "global_mesh_strategy": "mesh", "mesh_quality_acceptance_criteria": "mesh",
    "initialization": "numerical", "iteration_or_time_step_strategy": "numerical",
    "pressure_velocity_coupling": "numerical", "discretization": "numerical",
    "equation_specific_residual_criteria": "numerical", "convergence_assessment": "numerical",
    "required_result_subset": "outputs", "inlet_outlet_surfaces": "adapter", "wall_surfaces": "adapter",
}


def _shakedown_readiness(contract, checks):
    gate = ResearchInputGate.initial()
    error = None
    try:
        review = contract.get("research_review", {}) if isinstance(contract, dict) else {}
        if not isinstance(review, dict) or set(review) - set(SHAKEDOWN_MAPPING):
            raise ValueError("Research review accepts only fixed single-nozzle M8A keys")
        for key, record in review.items():
            if not isinstance(record, dict) or set(record) != {"status", "source", "reason", "evidence_reference"}:
                raise ValueError("Use the existing M8A record fields")
            gate = gate.record(key, **record)
    except (ValueError, TypeError) as exc:
        error = str(exc)
    official = gate.to_dict()
    items = {item["key"]: item for item in official["items"]}
    mapping = []
    for key, token in SHAKEDOWN_MAPPING.items():
        status = ("pending_mapping_contract" if key not in CATALOG or token not in checks else
                  "mapped" if checks[token] is True else "package_mapping_unresolved")
        state = items.get(key, {}).get("status", "unresolved")
        mapping.append({"key": key, "gate_status": state, "mapping_status": status,
                        "ready": state == "confirmed" and status == "mapped"})
    # H1-wide scientific readiness remains visible and unchanged. This fixed
    # projection consumes its item statuses; it never declares H1 pilot_ready.
    return {"research_gate": official, "mapping": mapping, "error": error,
            "outside_shakedown_scope": sorted(set(CATALOG) - set(SHAKEDOWN_MAPPING)),
            "research_ready_for_single_nozzle_shakedown": error is None and all(m["ready"] for m in mapping)}


@dataclass(frozen=True)
class ExecutionPlan:
    _json: str

    def to_dict(self):
        return json.loads(self._json)


def prepare(package_path, automation_manifest, local_config, *, input_artifact=None,
            input_mode="existing_mesh", journal=None, contract=None, purpose="shakedown",
            timeout_seconds=600):
    """Return a local-only plan. A supplied plan can never authorize execute().

    contract is an explicit local, human-reviewed launch/adapter configuration,
    not a claim that M7's comment journal became executable. Evidence is an
    attestation bound to exact bytes, not a digital signature or solver test.
    """
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    package = load_cfd_package(package_path)
    audit = verify_automation(package_path, automation_manifest)
    manifest = read_json(automation_manifest)
    identity = {k: package[k] for k in ("run_id", "case_id", "cfd_case_id")}
    identity["automation_digest"] = manifest["automation_digest"]
    environment = inspect_environment(local_config)
    blockers = []
    def require(ok, reason):
        if not ok:
            blockers.append(reason)
    require(purpose == "shakedown", "unsupported_execution_purpose")
    require(environment["environment_ready"], "local_environment_not_ready")
    repository = Path(__file__).resolve().parents[1]
    work = Path(local_config.working_directory).resolve()
    if work.is_relative_to(repository):
        relative = work.relative_to(repository).parts
        ignored = bool(relative) and (relative[0] == ".local" or (
            relative[0] == "outputs" and len(relative) > 1
            and relative[1] not in {"figures", "coordinates", "summaries"}))
        require(ignored, "runtime_directory_not_ignored")
    physical = package["normalized_spec"]
    config = physical["simulation_config"]
    require(config["dimensionality"] == local_config.dimension == "3D", "dimension_mismatch")
    require(config["solver"] == "Fluent", "non_fluent_solver")
    baseline = load_baseline()
    baseline_valid = _baseline_valid(physical, baseline["confirmed_baseline"])
    require(baseline_valid, "baseline_physics_mismatch")
    points = read_coordinates((Path(package_path).parent / "geometry/coordinates.csv").read_bytes())
    case = read_json(Path(package_path).parent / package["geometry"]["case_manifest"])
    require(points == [[0, 0, 0]] and case["normalized_spec"]["N"] == 1
            and case["normalized_spec"]["geometry"]["d"] == 14, "single_nozzle_target_mismatch")
    # These missing choices are deliberately not supplied by the baseline.
    require(config["steady_or_transient"] in {"steady", "transient"}, "operating_mode_unresolved")
    physical_checks = _physical_mapping(physical)
    for key, valid in physical_checks.items():
        require(valid, key + "_unresolved")
    physical_blockers = list(blockers)
    artifact = Path(input_artifact).resolve() if input_artifact is not None else None
    script = Path(journal).resolve() if journal is not None else None
    input_exists = artifact is not None and artifact.is_file() and artifact.stat().st_size > 0
    script_exists = script is not None and script.is_file() and script.stat().st_size > 0
    require(input_exists, "geometry_or_mesh_input_required")
    extensions = {"existing_mesh": (".msh", ".msh.h5"), "existing_case": (".cas", ".cas.h5")}
    require(input_mode in extensions, "unsupported_input_mode")
    if input_exists and input_mode in extensions:
        require(artifact.name.lower().endswith(extensions[input_mode]), "input_extension_mismatch")
    require(script_exists, "executable_journal_required")
    if script_exists:
        raw = script.read_bytes()
        try:
            require(any(line.strip() and not line.lstrip().startswith(";")
                        for line in raw.decode("utf-8-sig").splitlines()), "comment_only_journal")
        except UnicodeError:
            require(False, "invalid_journal_encoding")
    input_hash = sha256(artifact) if input_exists else None
    journal_hash = sha256(script) if script_exists else None
    launch_verified = False
    mesh_checked = False
    outputs, argv, numerical = [], None, {}
    review_error = None
    if contract is None:
        require(False, "unverified_launch_contract")
    else:
        try:
            expected = {"schema_version", "identity", "launch", "adapter_review", "expected_outputs",
                        "numerical_settings", "resource_review", "diagnostics"}
            if (not isinstance(contract, dict) or set(contract) - {"research_review"} != expected
                    or type(contract["schema_version"]) is not int or contract["schema_version"] != 1):
                raise ValueError("Unknown or incomplete local execution contract")
            if contract["identity"] != identity:
                raise ValueError("Exact M6 identity and M7 digest review required")
            launch = contract["launch"]
            if set(launch) != {"arguments", "executable_sha256", "release", "dimension", "precision", "evidence_reference"}:
                raise ValueError("Incomplete launch contract")
            portable_reference(launch["evidence_reference"])
            if launch["release"] != "2025 R2" or local_config.declared_release != launch["release"]:
                raise ValueError("Release contract mismatch")
            if launch["dimension"] != local_config.dimension or launch["precision"] != local_config.precision:
                raise ValueError("Launch dimension or precision mismatch")
            if not Path(local_config.executable).is_file() or sha256(local_config.executable) != launch["executable_sha256"]:
                raise ValueError("Reviewed executable checksum mismatch")
            arguments = launch["arguments"]
            if not isinstance(arguments, list) or not arguments:
                raise ValueError("Structured launch arguments required")
            fields = set()
            for token in arguments:
                if not isinstance(token, str) or not token or any(ord(c) < 32 for c in token):
                    raise ValueError("Invalid launch argument")
                for _, field, fmt, conversion in string.Formatter().parse(token):
                    if field is not None:
                        if field not in {"journal", "processes"} or fmt or conversion:
                            raise ValueError("Only journal and processes substitutions are supported")
                        fields.add(field)
            if fields != {"journal", "processes"}:
                raise ValueError("Launch must bind journal and process count")
            review = contract["adapter_review"]
            keys = {"input_sha256", "journal_sha256", "input_mode", "dimension", "boundary_mapping",
                    "geometry_evidence", "physics_evidence", "mesh_evidence", "numerics_evidence",
                    "output_evidence", "mesh_classification", "mesh_acceptance"}
            if not isinstance(review, dict) or set(review) != keys:
                raise ValueError("Complete adapter review required")
            for key in keys:
                if key.endswith("_evidence"):
                    portable_reference(review[key])
            if not input_hash or review["input_sha256"] != input_hash or not journal_hash or review["journal_sha256"] != journal_hash:
                raise ValueError("Reviewed input or journal checksum mismatch")
            if review["dimension"] != "3D" or review["input_mode"] != input_mode:
                raise ValueError("Reviewed input dimension or mode mismatch")
            if review["mesh_classification"] != "engineering shakedown mesh":
                raise ValueError("Shakedown mesh classification required")
            mesh = review["mesh_acceptance"]
            mesh_checked = (isinstance(mesh, dict) and set(mesh) == {"checked", "criteria"}
                            and mesh["checked"] is True and _text(mesh["criteria"]))
            if not mesh_checked:
                raise ValueError("Explicit engineering mesh acceptance required")
            mapping = review["boundary_mapping"]
            if not isinstance(mapping, dict) or set(mapping) != {"fuel_inlet", "oxidizer_inlet", "outlet", "wall"}:
                raise ValueError("All actual boundary roles required")
            faces = []
            for names in mapping.values():
                if not isinstance(names, list) or not names:
                    raise ValueError("Actual face names must be nonempty lists")
                faces.extend(portable_reference(name) for name in names)
            if len(set(faces)) != len(faces):
                raise ValueError("Boundary roles must not overlap")
            outputs = contract["expected_outputs"]
            if not isinstance(outputs, list) or not outputs:
                raise ValueError("Expected output artifacts required")
            outputs = [output_name(v) for v in outputs]
            reserved = {"input" + suffix for suffix in (".msh", ".msh.h5", ".cas", ".cas.h5")}
            reserved |= {"run.jou", "stdout.log", "stderr.log", "execution.json", "contract.json"}
            if len(set(n.casefold() for n in outputs)) != len(outputs) or any(n.casefold() in reserved for n in outputs):
                raise ValueError("Duplicate or reserved output name")
            numerical = structured(contract["numerical_settings"], physical=False)
            required = {"initialization", "max_iterations", "pressure_velocity_coupling", "discretization",
                        "residuals", "mass_balance_monitor", "temperature_monitor", "species_monitor",
                        "pressure_loss_monitor", "convergence_assessment"}
            if not isinstance(numerical, dict) or not required <= set(numerical):
                raise ValueError("Complete explicit numerical settings and monitors required")
            if numerical["initialization"] != "Hybrid Initialization" or numerical["max_iterations"] != {"value": 500, "unit": "1"}:
                raise ValueError("Engineering initialization and iteration budget mismatch")
            if config["steady_or_transient"] == "transient" and not {"time_step", "time_steps"} <= set(numerical):
                raise ValueError("Transient stepping contract required")
            for key in ("pressure_velocity_coupling", "discretization", "convergence_assessment"):
                if not _text(numerical[key]):
                    raise ValueError("Explicit numerical definition required: " + key)
            residuals = numerical["residuals"]
            if (not isinstance(residuals, dict) or not residuals
                    or any(not _text(k) or not _quantity(v, "1", positive=True) for k, v in residuals.items())):
                raise ValueError("Positive per-equation residual quantities required")
            for key in ("mass_balance_monitor", "temperature_monitor", "species_monitor", "pressure_loss_monitor"):
                monitor = numerical[key]
                if not isinstance(monitor, dict) or set(monitor) != {"definition", "sample"} or not all(_text(v) for v in monitor.values()):
                    raise ValueError("Monitor definition and sample required: " + key)
            if config["steady_or_transient"] == "transient" and (
                    not _quantity(numerical["time_step"], "s", positive=True)
                    or not _quantity(numerical["time_steps"], "1", positive=True)
                    or type(numerical["time_steps"]["value"]) is not int):
                raise ValueError("Positive transient time step and integer step count required")
            if {"purpose", "scientific_eligible", "automation_digest"} & set(numerical):
                raise ValueError("Reserved M8B provenance keys")
            saved = read_json(Path(automation_manifest).parent / "automation_spec.json")["automation_inputs"]["numerical_settings"]
            if not _contains(numerical, saved):
                raise ValueError("M7 numerical preparation conflicts with execution settings")
            resources = contract["resource_review"]
            if set(resources) != {"minimum_free_bytes", "maximum_processes", "license_evidence", "resource_evidence"}:
                raise ValueError("Resource and license review required")
            for key in ("license_evidence", "resource_evidence"):
                portable_reference(resources[key])
            for key in ("minimum_free_bytes", "maximum_processes"):
                if type(resources[key]) is not int or resources[key] <= 0:
                    raise ValueError("Resource limits must be explicit positive integers")
            work = Path(local_config.working_directory)
            while not work.exists() and work != work.parent:
                work = work.parent
            require(shutil.disk_usage(work).free >= resources["minimum_free_bytes"], "insufficient_disk_space")
            require(local_config.processes <= resources["maximum_processes"], "process_resource_limit")
            diagnostics = contract["diagnostics"]
            if diagnostics is not None:
                if set(diagnostics) != {"artifact", "evidence_reference"} or diagnostics["artifact"] not in outputs:
                    raise ValueError("Diagnostic artifact must be a reviewed expected output")
                portable_reference(diagnostics["evidence_reference"])
            argv = [local_config.executable] + [v.format(journal="run.jou", processes=local_config.processes) for v in arguments]
            launch_verified = True
        except (ValueError, TypeError, KeyError, OSError) as exc:
            review_error = str(exc)
            require(False, "unverified_launch_contract")
    research = _shakedown_readiness(contract, {
        "mode": config["dimensionality"] == "3D" and config["steady_or_transient"] in {"steady", "transient"},
        "baseline": baseline_valid, "domain": physical_checks["computational_domain"],
        "topology": baseline_valid and physical_checks["hydraulic_diameter_topology"],
        "physics": baseline_valid and all(physical_checks[k] for k in
            ("density_model", "gravity_decision", "radiation_model_decision")),
        "materials": physical_checks["species_material_database"],
        "outlet": baseline_valid and physical_checks["outlet_backflow"],
        "adapter": launch_verified, "mesh": launch_verified and mesh_checked,
        "numerical": launch_verified, "outputs": launch_verified,
    })
    require(research["research_ready_for_single_nozzle_shakedown"], "single_nozzle_research_not_ready")
    report = {"schema_version": 1, "identity": identity, "purpose": purpose,
              "attempt_intent": "new M6 attempt only after explicit execute and final checks",
              "attempt_created": False, "solver_status": "not_started", "local_config": asdict(local_config),
              "timeout_seconds": timeout_seconds, "input_mode": input_mode,
              "input_artifact": str(artifact) if artifact else None, "input_sha256": input_hash,
              "journal_artifact": str(script) if script else None, "journal_sha256": journal_hash,
              "expected_outputs": outputs, "argv_preview": argv, "numerical_settings": numerical,
              "launch_contract_verified": launch_verified, "contract_error": review_error,
              "m6_readiness": {"package_valid": True, "physical_blockers": physical_blockers},
              "m7_static_valid": audit["static_valid"], "m7_ready_to_execute": False,
              "m8a_readiness": {"scope": "fixed single-nozzle projection of formal M8A item readiness; H1 unchanged",
                                "environment": environment, "execution_allowed": False,
                                **research},
              "research_ready_for_single_nozzle_shakedown": research["research_ready_for_single_nozzle_shakedown"],
              "m8b_ready": not blockers, "execution_allowed": False, "blocking_reasons": blockers,
              "scientific_eligible": False, "convergence_status": "not_assessed"}
    return ExecutionPlan(json.dumps(report, ensure_ascii=False, allow_nan=False))


inspect = prepare
dry_run = prepare
