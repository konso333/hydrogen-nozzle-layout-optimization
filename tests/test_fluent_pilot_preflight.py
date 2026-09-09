"""M8A contracts; every executable is inert synthetic test data."""

from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest

from cfd import CFDSpec, export_cfd_package
from experiments.archive import archive_run, verify_run
from experiments.search_space import ExperimentSearchSpace
from fluent import build_spec, export_automation, verify_automation
from fluent_pilot import (FluentLaunchPlan, LocalFluentConfig, ResearchInputGate,
                          inspect_environment, preflight, select_pilot_request)
from fluent_pilot.research import CATALOG, NOX, STAGING
from fluent_pilot.target import validate_pilot_points


@pytest.fixture
def prepared(tmp_path):
    request = select_pilot_request()
    run = archive_run([request], name="M8A synthetic/test-only", output_root=tmp_path / "runs")
    spec = CFDSpec.create(request.spec.case_id, {}, {})
    package = export_cfd_package(run, spec, output_root=tmp_path / "cfd",
                                 required_metrics=["pressure_loss"])
    manifest = export_automation(package, tmp_path / "m7")
    return run, package, manifest


def local(tmp_path):
    exe = tmp_path / "fluent.exe"
    exe.write_text("synthetic/test-only: deliberately not executable", encoding="utf-8")
    return LocalFluentConfig(exe, tmp_path)


def snapshot(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_pilot_reuses_m5_and_independent_geometry():
    request = select_pilot_request()
    source = Path(__file__).resolve().parents[1] / "examples/m5_hex7_spacing_study.json"
    original = ExperimentSearchSpace.load(source).plan().requests[1]
    assert request.spec.case_id == original.spec.case_id
    assert request.spec.normalized_spec == original.spec.normalized_spec
    points = np.asarray(request.spec.generate())
    assert points.shape == (7, 2)
    radii = np.linalg.norm(points, axis=1)
    assert sum(np.isclose(radii, 0)) == 1
    np.testing.assert_allclose(sorted(radii), [0] + [10] * 6, atol=1e-12)
    ring = points[radii > 1]
    angles = np.sort(np.arctan2(ring[:, 1], ring[:, 0]) % (2 * math.pi))
    np.testing.assert_allclose(np.diff(np.r_[angles, angles[0] + 2 * math.pi]), math.pi / 3)
    distances = [np.linalg.norm(a - b) for i, a in enumerate(points) for b in points[i + 1:]]
    assert min(distances) == pytest.approx(10)
    assert request.spec.normalized_spec["geometry"]["d"] == 4
    assert min(distances) / 4 == pytest.approx(2.5)


@pytest.mark.parametrize("change", ["count", "radius", "angles", "nan", "diameter"])
def test_pilot_rejects_invalid_geometry(change):
    request = select_pilot_request()
    points = np.asarray(request.spec.generate()).copy()
    spec = request.spec.normalized_spec
    if change == "count":
        points = points[:-1]
    elif change == "radius":
        points[1] *= 2
    elif change == "angles":
        points[1] = points[2]
    elif change == "nan":
        points[0, 0] = np.nan
    else:
        spec["geometry"]["d"] = 5
    with pytest.raises(ValueError):
        validate_pilot_points(points, spec)


def test_no_process_and_read_only_full_preflight(prepared, tmp_path, monkeypatch):
    run, package, manifest = prepared
    config = local(tmp_path)
    before = snapshot(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("External process forbidden in M8A preflight")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    report = preflight(package, manifest, config)
    assert report["environment_ready"] is True
    assert report["automation_static_valid"] is True
    assert report["research_gate"]["pilot_ready"] is False
    assert report["ready_to_execute"] is report["execution_allowed"] is False
    assert report["attempt_created"] is False
    assert report["launch_plan"]["command_preview"] is None
    assert all(line.startswith("; ") for line in (manifest.parent / "prepare.jou").read_text().splitlines())
    assert snapshot(tmp_path) == before
    assert not list((package.parent / "attempts").iterdir())
    assert verify_run(run)["overall_match"]


def test_local_environment_never_changes_scientific_or_automation_identity(prepared, tmp_path):
    _, package, manifest = prepared
    first = local(tmp_path)
    second_dir = tmp_path / "another machine"
    second_dir.mkdir()
    configs = [first, replace(local(second_dir), processes=9),
               LocalFluentConfig(r"G:\SyntheticFluent\fluent.exe", r"G:\SyntheticRuns", processes=3),
               LocalFluentConfig(r"C:\SyntheticFluent\fluent.exe", r"C:\SyntheticRuns", processes=4)]
    before = snapshot(package.parent)
    identity = preflight(package, manifest, first)["identity"]
    for config in configs:
        assert preflight(package, manifest, config)["identity"] == identity
    assert build_spec(package).automation_digest == identity["automation_digest"]
    assert CFDSpec.from_normalized(json.loads(package.read_text())["normalized_spec"]).cfd_case_id == identity["cfd_case_id"]
    assert snapshot(package.parent) == before


@pytest.mark.parametrize("field,value,error", [
    ("executable", "fluent.exe", "executable_absolute_local"),
    ("executable", "missing.exe", "executable_exists"),
    ("dimension", "2D", "dimension"), ("dimension", 3, "dimension"),
    ("precision", "single", "precision"),
    ("processes", 0, "processes"), ("processes", -1, "processes"),
    ("processes", True, "processes"), ("processes", 2.0, "processes"),
    ("processes", "2", "processes"),
    ("working_directory", "relative", "working_directory_absolute_local"),
])
def test_invalid_local_checks(tmp_path, field, value, error):
    config = replace(local(tmp_path), **{field: value})
    report = inspect_environment(config)
    assert report["environment_ready"] is False
    assert error in report["errors"]


def test_missing_wrong_name_unc_and_directory_executable(tmp_path):
    config = local(tmp_path)
    for path in (tmp_path / "missing" / "fluent.exe", tmp_path,
                 r"\\server\share\fluent.exe"):
        assert inspect_environment(replace(config, executable=path))["fluent_installation"]["status"] == "not_detected"
    wrong = tmp_path / "other.exe"
    wrong.write_text("inert")
    assert inspect_environment(replace(config, executable=wrong))["environment_ready"] is False


def test_directory_creation_is_explicit_and_never_performed(tmp_path):
    config = replace(local(tmp_path), working_directory=tmp_path / "future" / "pilot")
    assert not inspect_environment(config)["environment_ready"]
    planned = inspect_environment(replace(config, plan_create_directory=True))
    assert planned["environment_ready"]
    assert planned["working_directory_status"] == "planned_creation"
    assert not Path(config.working_directory).exists()
    assert not inspect_environment(replace(config, working_directory=config.executable,
                                          plan_create_directory=True))["environment_ready"]


def test_unwritable_directory_fails_closed(tmp_path, monkeypatch):
    config = local(tmp_path)
    original_open = Path.open
    calls = []
    def denied(path, mode="r", *args, **kwargs):
        if mode == "xb":
            calls.append(path)
            raise PermissionError("synthetic denied write")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    report = inspect_environment(config)
    assert not report["environment_ready"]
    assert report["working_directory_status"] == "not_writable"
    assert len(calls) == 1


def test_environment_precedence_and_declared_version(tmp_path):
    config = local(tmp_path)
    env = {"FLUENT_EXECUTABLE": config.executable, "FLUENT_WORKING_DIRECTORY": config.working_directory,
           "FLUENT_PROCESSES": "6"}
    assert LocalFluentConfig.from_environment(environment=env).processes == 6
    assert LocalFluentConfig.from_environment(environment={**env, "FLUENT_PROCESSES": "invalid"},
                                               processes=2).processes == 2
    explicit = LocalFluentConfig.from_environment(environment=env, processes=2,
        declared_product="ANSYS Fluent", declared_release="2025 R2", installation_hint="v252")
    installation = inspect_environment(explicit)["fluent_installation"]
    assert installation["release"] == "user_declared:2025 R2"
    assert installation["version_verified"] is installation["license_verified"] is False
    assert inspect_environment(config)["fluent_installation"]["release"] == "unknown"
    with pytest.raises(ValueError):
        LocalFluentConfig.from_environment(environment={})


def test_initial_research_categories_sources_and_nox():
    gate = ResearchInputGate.initial()
    assert set(i.category for i in gate.items) == {"geometry", "boundary_conditions", "physics", "mesh",
                                                 "numerical_solver", "post_processing", "experiment_alignment"}
    assert not any(gate.readiness().values())
    assert all(i.status == ("pending_contract" if i.key in NOX else "unresolved") for i in gate.items)
    groups = gate.group_dependency_report()
    assert "hydrogen_chemical_mechanism" in {i["key"] for i in groups["group_1_single_nozzle"]}
    assert "nox_unit" in {i["key"] for i in groups["group_3_experiment"]}
    assert "combustor_axial_length" in {i["key"] for i in groups["researcher_decision"]}


def test_partial_geometry_readiness_requires_all_items():
    gate = ResearchInputGate.initial()
    original = gate.to_dict()
    geometry = [i for i in gate.items if i.category == "geometry"]
    for index, item in enumerate(geometry):
        gate = gate.record(item.key, status="confirmed", source=item.source,
                           reason="Synthetic test-only formal review", evidence_reference="test-review-1")
        assert gate.readiness()["geometry_ready"] == (index == len(geometry) - 1)
        assert not gate.readiness()["pilot_ready"]
    assert ResearchInputGate.initial().to_dict() == original


def test_staging_requires_explicit_baseline_provenance():
    initial = ResearchInputGate.initial()
    assert all(i.status == "unresolved" for i in initial.items if i.key in STAGING)
    gate = initial.nonstaged_baseline(reason="Synthetic nonstaged test baseline", evidence_reference="test-scope-1")
    for item in gate.items:
        if item.key in STAGING:
            assert item.status == "not_required"
            assert item.reason.startswith("not_required_for_baseline:")
            assert item.source == "researcher_decision"
    assert not gate.readiness()["pilot_ready"]
    assert all(i.status == "pending_contract" for i in gate.items if i.key in NOX)


def test_all_required_attestations_can_complete_research_but_never_authorize_execution(tmp_path):
    gate = ResearchInputGate.initial()
    for item in gate.items:
        if item.key not in NOX:
            gate = gate.record(item.key, status="confirmed", source=item.source,
                               reason="Synthetic test-only evidence", evidence_reference="test-review")
    assert all(gate.readiness().values())
    assert not gate.blocking_inputs
    assert {i.key for i in gate.missing_inputs} == NOX
    assert all(not i.required_for_pilot for i in gate.missing_inputs)
    assert gate.group_dependency_report()["group_3_experiment"]
    assert FluentLaunchPlan(local(tmp_path)).execution_allowed is False


@pytest.mark.parametrize("change", ["empty", "missing", "duplicate", "stage", "status", "source", "evidence", "nox", "staging"])
def test_gate_rejects_bypasses(change):
    gate = ResearchInputGate.initial()
    with pytest.raises((ValueError, TypeError)):
        if change == "empty":
            ResearchInputGate(())
        elif change == "missing":
            ResearchInputGate(gate.items[:-1])
        elif change == "duplicate":
            ResearchInputGate(gate.items[:-1] + (gate.items[0],))
        elif change == "stage":
            replace(gate.items[0], blocking_stage="pilot")
        else:
            key = "nox_unit" if change == "nox" else "stage_definition" if change == "staging" else gate.items[0].key
            gate.record(key, status="invalid" if change == "status" else "not_required" if change == "staging" else "confirmed",
                        source="invalid" if change == "source" else "researcher_decision",
                        reason="test", evidence_reference=None if change == "evidence" else "test-evidence")


@pytest.mark.parametrize("flag", ["force", "ignore_missing", "skip_validation", "execution_allowed"])
def test_no_override_interface(flag, tmp_path):
    with pytest.raises(TypeError):
        FluentLaunchPlan(local(tmp_path), **{flag: True})


def test_m7_tampering_rejected(prepared, tmp_path):
    _, package, manifest = prepared
    (manifest.parent / "readiness.json").write_text('{"ready_to_execute": true}')
    with pytest.raises(ValueError):
        preflight(package, manifest, local(tmp_path))


def test_wrong_pilot_rejected(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples/m5_hex7_spacing_study.json"
    request = ExperimentSearchSpace.load(source).plan().requests[0]
    run = archive_run([request], name="M8A wrong target test", output_root=tmp_path / "runs")
    package = export_cfd_package(run, CFDSpec.create(request.spec.case_id, {}, {}),
                                 output_root=tmp_path / "cfd", required_metrics=["pressure_loss"])
    manifest = export_automation(package, tmp_path / "m7")
    with pytest.raises(ValueError, match="not the selected"):
        preflight(package, manifest, local(tmp_path))


def test_cli_existing_package_no_process(prepared, tmp_path, monkeypatch, capsys):
    from scripts.fluent_pilot_preflight import main
    _, package, manifest = prepared
    config = local(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("No process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    assert main(["--package", str(package), "--automation-manifest", str(manifest),
                 "--fluent-executable", config.executable, "--working-directory", config.working_directory]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    assert report["environment_ready"] and not report["research_gate"]["pilot_ready"]


def test_archive_csv_png_and_empty_physics(prepared):
    run, package, manifest = prepared
    files = list(run.parent.rglob("*"))
    png = next(p for p in files if p.suffix == ".png")
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (package.parent / "geometry/coordinates.csv").read_bytes() == (manifest.parent / "coordinates_mm.csv").read_bytes()
    physical = json.loads(package.read_text())["normalized_spec"]
    assert all(v is None for k, v in physical["operating_condition"].items() if k != "extras")
    assert all(v is None for k, v in physical["simulation_config"].items() if k != "extras")
    assert verify_automation(package, manifest)["ready_to_execute"] is False


# Literal expected contract, deliberately independent of the production catalog.
EXPECTED_KEYS = set("""
combustor_axial_length real_3d_chamber_construction actual_boundary_face_mapping
array_to_chamber_integration nozzle_physical_length nozzle_internal_geometry_or_equivalent_inlet
h2_inlet_definition oxidizer_inlet_definition individual_stream_mass_flow inlet_temperature
inlet_pressure outlet_definition wall_thermal_condition pressure_reference turbulence_model
combustion_model hydrogen_chemical_mechanism species_material_database stable_operating_envelope
radiation_model_decision pilot_baseline_scope stage_definition pilot_main_stage_assignment
stage_flow_split stage_equivalence_ratio stage_activation_logic global_mesh_strategy nozzle_refinement
boundary_layer_strategy grid_independence_plan mesh_reference pressure_velocity_coupling
discretization equation_specific_residual_criteria initialization iteration_or_time_step_strategy
convergence_assessment convergence_reference inlet_outlet_surfaces wall_surfaces
steady_sample_definition transient_time_window benchmark_operating_condition h2_flow air_flow
equivalence_ratio thermal_power_or_load staging_ratio flame_measurement_locations
nox_measurement_definition nox_unit dry_wet_basis oxygen_correction sampling_location instrument_information
physical_model_configuration operating_mode inlet_turbulence_specification
mesh_quality_acceptance_criteria required_result_subset
""".split())
CORE_REQUIRED = {
    "combustor_axial_length", "nozzle_physical_length", "nozzle_internal_geometry_or_equivalent_inlet",
    "real_3d_chamber_construction", "array_to_chamber_integration", "actual_boundary_face_mapping",
    "h2_inlet_definition", "oxidizer_inlet_definition", "outlet_definition", "pressure_reference",
    "wall_thermal_condition", "turbulence_model", "combustion_model", "hydrogen_chemical_mechanism",
    "physical_model_configuration", "global_mesh_strategy", "mesh_quality_acceptance_criteria",
    "pressure_velocity_coupling", "convergence_assessment", "operating_mode", "required_result_subset",
}


def confirmed_gate():
    gate = ResearchInputGate.initial()
    for item in gate.items:
        if item.status != "pending_contract":
            gate = gate.record(item.key, status="confirmed", source=item.source,
                               reason="Synthetic test-only review", evidence_reference="meeting:test-review")
    return gate


def alternate_package(prepared, tmp_path, config, condition=None):
    run, _, _ = prepared
    spec = CFDSpec.create(select_pilot_request().spec.case_id, condition or {}, config)
    package = export_cfd_package(run, spec, output_root=tmp_path / "alternate_cfd",
                                 required_metrics=["pressure_loss"])
    manifest = export_automation(package, tmp_path / "alternate_m7")
    return package, manifest


def test_literal_gate_contract_and_applicability_policy():
    gate = ResearchInputGate.initial()
    assert len(EXPECTED_KEYS) == 60
    assert {i.key for i in gate.items} == EXPECTED_KEYS
    assert len(gate.blocking_inputs) == 54
    policies = {i.key: i.applicability_policy for i in gate.items}
    assert all(policies[k] == "always_required" for k in CORE_REQUIRED)
    assert {i.key for i in gate.items if not i.required_for_pilot} == {
        "nox_measurement_definition", "nox_unit", "dry_wet_basis", "oxygen_correction",
        "sampling_location", "instrument_information"}


@pytest.mark.parametrize("key", sorted(CORE_REQUIRED))
def test_core_required_cannot_be_waived_even_with_unknown_reason(key):
    gate = ResearchInputGate.initial()
    with pytest.raises(ValueError):
        gate.record(key, status="not_required", source="researcher_decision",
                    reason="unknown", evidence_reference="meeting:test")
    item = next(i for i in gate.items if i.key == key)
    with pytest.raises(ValueError):
        replace(item, status="not_required", source="researcher_decision",
                reason="unknown", evidence_reference="meeting:test")


def test_direct_staging_waiver_and_forged_baseline_prefix_rejected():
    gate = ResearchInputGate.initial()
    with pytest.raises(ValueError):
        gate.record("stage_definition", status="not_required", source="researcher_decision",
                    reason="not_required_for_baseline: test", evidence_reference="meeting:test")
    changed = tuple(replace(i, status="not_required", source="researcher_decision",
                            reason="not_required_for_baseline: test", evidence_reference="meeting:test")
                    if i.key == "stage_definition" else i for i in gate.items)
    with pytest.raises(ValueError, match="linked"):
        ResearchInputGate(changed)


def test_baseline_decision_is_atomic_and_cannot_drift():
    gate = ResearchInputGate.initial().nonstaged_baseline(reason="test scope", evidence_reference="docs/scope.md")
    assert {i.key for i in gate.items if i.status == "not_required"} == {
        "stage_definition", "pilot_main_stage_assignment", "stage_flow_split", "stage_equivalence_ratio",
        "stage_activation_logic", "staging_ratio"}
    assert next(i for i in gate.items if i.key == "combustor_axial_length").status == "unresolved"
    with pytest.raises(ValueError):
        gate.record("pilot_baseline_scope", status="confirmed", source="researcher_decision",
                    reason="different scope", evidence_reference="docs/other.md")
    with pytest.raises(ValueError):
        ResearchInputGate(gate.items)  # Cannot detach the decision from its exemptions.


@pytest.mark.parametrize("reference", ["docs/x.md", "meeting:2026-09-09-group-sync", "commit:abc123", "H1", "experiment-sheet:003"])
def test_portable_evidence_accepted(reference):
    gate = ResearchInputGate.initial().record("combustor_axial_length", status="confirmed",
        source="researcher_decision", reason="test", evidence_reference=reference)
    assert gate.items[0].evidence_reference == reference


@pytest.mark.parametrize("reference", [r"C:\Synthetic\x.md", r"D:\Synthetic\x.md", r"G:\Synthetic\x.md",
                                      r"\\server\share\x.md", "/home/user/x.md", "/Users/user/x.md",
                                      "review at G:/Synthetic/x.md", "", "   "])
def test_absolute_or_empty_evidence_rejected(reference):
    with pytest.raises(ValueError):
        ResearchInputGate.initial().record("combustor_axial_length", status="confirmed",
            source="researcher_decision", reason="test", evidence_reference=reference)


def test_all_research_confirmed_cannot_green_empty_package(prepared, tmp_path):
    _, package, manifest = prepared
    report = preflight(package, manifest, local(tmp_path), research_gate=confirmed_gate())
    assert report["environment_ready"] is report["research_ready"] is True
    assert report["physical_spec_status"] == "preparation_placeholder"
    assert report["package_physics_ready"] is report["package_ready_for_pilot"] is False
    assert report["execution_allowed"] is False
    mapping = {i["key"]: i for i in report["package_input_mapping"]}
    assert mapping["inlet_temperature"]["reason"] == "evidence confirmed but package mapping unresolved"
    assert mapping["required_result_subset"]["package_mapping_status"] == "mapping_unresolved"
    assert mapping["mesh_quality_acceptance_criteria"]["package_mapping_status"] == "pending_mapping_contract"


@pytest.mark.parametrize("dimension,expected", [("3D", "compatible"), ("2D", "conflict"),
                                               ("axisymmetric", "conflict"), (None, "unresolved")])
def test_physical_dimension_cross_check(prepared, tmp_path, dimension, expected):
    package, manifest = alternate_package(prepared, tmp_path, {"solver": "Fluent", "dimensionality": dimension})
    before = package.read_bytes()
    report = preflight(package, manifest, local(tmp_path))
    assert report["compatibility"]["dimensionality"] == expected
    assert not report["package_ready_for_pilot"]
    assert package.read_bytes() == before


@pytest.mark.parametrize("solver,expected", [("Fluent", "compatible"), ("fluent", "compatible"),
                                            ("OtherSolver", "conflict"), (None, "unresolved")])
def test_solver_family_cross_check(prepared, tmp_path, solver, expected):
    package, manifest = alternate_package(prepared, tmp_path, {"solver": solver, "dimensionality": "3D"})
    report = preflight(package, manifest, local(tmp_path))
    assert report["compatibility"]["solver_family"] == expected
    assert report["compatibility"]["adapter_verified"] is False


def test_partial_m6_mapping_uses_actual_values_and_exact_review_identity(prepared, tmp_path):
    package, manifest = alternate_package(prepared, tmp_path,
        {"solver": "Fluent", "dimensionality": "3D", "steady_or_transient": "steady"},
        {"inlet_temperature": {"value": 321, "unit": "K"}})
    saved = json.loads(manifest.read_text())
    review = {"cfd_case_id": saved["cfd_case_id"], "automation_digest": saved["automation_digest"],
              "evidence_reference": "meeting:synthetic-package-review"}
    report = preflight(package, manifest, local(tmp_path), research_gate=confirmed_gate(), package_review=review)
    mapping = {i["key"]: i for i in report["package_input_mapping"]}
    assert mapping["inlet_temperature"]["package_mapping_status"] == "mapped"
    assert mapping["inlet_pressure"]["package_mapping_status"] == "mapping_unresolved"
    assert mapping["operating_mode"]["package_mapping_status"] == "mapped"
    assert mapping["required_result_subset"]["package_mapping_status"] == "mapped"
    assert report["package_review_status"] == "matched"
    assert not report["package_ready_for_pilot"]
    for key in ("cfd_case_id", "automation_digest"):
        wrong = preflight(package, manifest, local(tmp_path), research_gate=confirmed_gate(),
                          package_review={**review, key: "wrong"})
        assert wrong["package_review_status"] == "conflict"
        assert not wrong["package_ready_for_pilot"]
    unconfirmed = preflight(package, manifest, local(tmp_path), package_review=review)
    subset = next(i for i in unconfirmed["package_input_mapping"] if i["key"] == "required_result_subset")
    assert subset["package_mapping_status"] == "mapping_unresolved"


def test_baseline_and_sampling_exemptions_must_match_m6(prepared, tmp_path):
    package, manifest = alternate_package(prepared, tmp_path,
        {"solver": "Fluent", "dimensionality": "3D", "steady_or_transient": "transient",
         "extras": {"pilot_baseline_scope": "staged"}})
    gate = ResearchInputGate.initial().nonstaged_baseline(reason="test", evidence_reference="docs/test.md")
    gate = gate.declare_applicability("steady_sampling", reason="test", evidence_reference="docs/test.md")
    report = preflight(package, manifest, local(tmp_path), research_gate=gate)
    mapping = {i["key"]: i for i in report["package_input_mapping"]}
    assert mapping["stage_definition"]["package_mapping_status"] == "conflict"
    assert mapping["transient_time_window"]["package_mapping_status"] == "conflict"
    assert not report["package_ready_for_pilot"]
    with pytest.raises(ValueError):
        gate.declare_applicability("transient_sampling", reason="test", evidence_reference="docs/test.md")


def test_probe_cleanup_failure_reports_residual_without_retry_or_other_delete(tmp_path, monkeypatch):
    config = local(tmp_path)
    sentinel = tmp_path / "user_data.txt"
    sentinel.write_text("keep")
    calls = []
    original = Path.unlink
    def denied(path, *args, **kwargs):
        calls.append(path)
        raise PermissionError("synthetic cleanup failure")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        report = inspect_environment(config)
    assert report["writable_probe"]["status"] == report["working_directory_status"] == "cleanup_failed"
    residual = Path(report["writable_probe"]["residual_probe_path"])
    assert calls == [residual]
    assert residual.parent == tmp_path and residual.name.startswith(".m8a-write-probe-")
    assert residual.exists() and not report["environment_ready"]
    assert sentinel.read_text() == "keep"
    original(residual)  # Test cleanup after restoring the production failure simulation.


def test_declared_release_is_local_and_legacy_identity_unchanged(prepared, tmp_path):
    _, package, manifest = prepared
    before = snapshot(manifest.parent)
    first = preflight(package, manifest, local(tmp_path))
    other = preflight(package, manifest, replace(local(tmp_path), processes=7, declared_release="synthetic-release"))
    assert other["identity"] == first["identity"]
    assert first["identity"]["case_id"] == "case_v1_5e073b9a0a01cdc830700a471f2f254d700798732281c14341ddcf12b2c85413"
    assert first["identity"]["cfd_case_id"] == "cfd_v1_df00d347c70f801e93af1d37449a03189441dba78addcded5eed327200696008"
    assert snapshot(manifest.parent) == before


@pytest.mark.parametrize("complete", [False, True])
def test_physical_model_and_outlet_acceptance_require_explicit_components(prepared, tmp_path, complete):
    model = {"energy_equation": True, "species_reaction_enablement": True}
    outlet = {"boundary_type": "synthetic-test-boundary"}
    if complete:
        model.update(density_compressibility="synthetic-test-model", gravity_decision=False)
        outlet["backflow_specification"] = {"applicability": "not_applicable_to_test_boundary", "reason": "test"}
    package, manifest = alternate_package(prepared, tmp_path,
        {"solver": "Fluent", "dimensionality": "3D", "extras": {"physical_model_configuration": model}},
        {"extras": {"outlet_definition": outlet}})
    report = preflight(package, manifest, local(tmp_path), research_gate=confirmed_gate())
    mapping = {i["key"]: i for i in report["package_input_mapping"]}
    expected = "mapped" if complete else "mapping_unresolved"
    assert mapping["physical_model_configuration"]["package_mapping_status"] == expected
    assert mapping["outlet_definition"]["package_mapping_status"] == expected
    assert not report["package_ready_for_pilot"]


@pytest.mark.parametrize("kind,config,waived_key", [
    ("steady_sampling", {"steady_or_transient": "steady"}, "transient_time_window"),
    ("transient_sampling", {"steady_or_transient": "transient"}, "steady_sample_definition"),
    ("laminar_inlet", {"turbulence_model": "laminar"}, "inlet_turbulence_specification"),
    ("nonstaged_baseline", {"extras": {"pilot_baseline_scope": "non_staged"}}, "stage_definition"),
])
def test_conditional_exemptions_require_and_match_explicit_m6_scope(prepared, tmp_path, kind, config, waived_key):
    package, manifest = alternate_package(prepared, tmp_path, {"solver": "Fluent", "dimensionality": "3D", **config})
    gate = ResearchInputGate.initial().declare_applicability(kind, reason="test scope", evidence_reference="docs/test.md")
    report = preflight(package, manifest, local(tmp_path), research_gate=gate)
    entry = next(i for i in report["package_input_mapping"] if i["key"] == waived_key)
    assert entry["package_mapping_status"] == "not_required"
    assert not report["package_ready_for_pilot"]


def test_changed_required_subset_invalidates_review_without_changing_cfd_identity(prepared, tmp_path):
    run, old_package, old_manifest = prepared
    old = json.loads(old_manifest.read_text())
    spec = CFDSpec.from_normalized(json.loads(old_package.read_text())["normalized_spec"])
    package = export_cfd_package(run, spec, output_root=tmp_path / "other_subset",
                                 required_metrics=["hydrogen_conversion"])
    manifest = export_automation(package, tmp_path / "other_subset_m7")
    review = {"cfd_case_id": old["cfd_case_id"], "automation_digest": old["automation_digest"],
              "evidence_reference": "meeting:old-subset"}
    report = preflight(package, manifest, local(tmp_path), research_gate=confirmed_gate(), package_review=review)
    assert report["identity"]["cfd_case_id"] == old["cfd_case_id"]
    assert report["identity"]["automation_digest"] != old["automation_digest"]
    assert report["package_review_status"] == "conflict"
    assert not report["package_ready_for_pilot"]


def test_probe_name_collision_preserves_existing_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import fluent_pilot.local as module
    config = local(tmp_path)
    existing = tmp_path / ".m8a-write-probe-testcollision"
    existing.write_text("user-owned")
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex="testcollision"))
    report = inspect_environment(config)
    assert report["writable_probe"]["status"] == "create_failed"
    assert report["writable_probe"]["residual_probe_path"] is None
    assert existing.read_text() == "user-owned"


def test_package_mapping_does_not_store_physics_in_gate(prepared, tmp_path):
    gate = confirmed_gate()
    before = gate.to_dict()
    _, package, manifest = prepared
    report = preflight(package, manifest, local(tmp_path), research_gate=gate)
    assert gate.to_dict() == before
    assert report["research_ready"]
    assert not report["package_ready_for_pilot"]
    assert all("value" not in item for item in report["research_gate"]["items"])
    assert not list((package.parent / "attempts").iterdir())
