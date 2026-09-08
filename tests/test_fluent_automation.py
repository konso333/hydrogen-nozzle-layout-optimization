"""M7 inputs are synthetic/test-only. No Fluent installation or results needed."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from cfd import CFDSpec, export_cfd_package, load_cfd_package
from config import GeometryConfig
from experiments.archive import CaseRequest, archive_run
from experiments.spec import CaseSpec
from fluent import (FluentAutomationSpec, audit_capability, build_spec, export_automation,
                    generate_journal, validate_journal, verify_automation)
from fluent.spec import BOUNDARY_ROLES, MM_TO_M


@pytest.fixture
def package(tmp_path):
    geometry = CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10})
    run = archive_run([CaseRequest(geometry)], name="M7 synthetic/test-only", output_root=tmp_path / "runs")
    spec = CFDSpec.create(geometry.case_id,
        {"equivalence_ratio": {"value": .4, "unit": "1"}},
        {"solver": "Fluent", "dimensionality": "3D"})
    return export_cfd_package(run, spec, output_root=tmp_path / "cfd", required_metrics=["pressure_loss"])


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_end_to_end_identity_coordinates_and_no_mutation(package, tmp_path):
    before = snapshot(tmp_path)
    original = load_cfd_package(package)
    result = export_automation(package, tmp_path / "out")
    report = verify_automation(package, result)
    assert report["static_valid"] and not report["ready_to_execute"]
    for key in ("run_id", "case_id", "cfd_case_id"):
        assert report[key] == original[key]
    assert report["cfd_case_id"] == "cfd_v1_87affd44e3d0060e663010f7e859ec467884c1ec31e163b509d3b25b32afa387"
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())
    assert list((package.parent / "attempts").iterdir()) == []
    raw = (package.parent / "geometry/coordinates.csv").read_bytes()
    assert (result.parent / "coordinates_mm.csv").read_bytes() == raw
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    geom = json.loads((result.parent / "geometry_input.json").read_text())["geometry"]
    expected = [[float(row[k]) for k in ("x_mm", "y_mm", "z_mm")] for row in rows]
    assert geom["centers_mm"]["value"] == expected
    assert geom["centers_m"]["value"] == [[v * .001 for v in point] for point in expected]
    assert MM_TO_M * 1 == .001
    assert geom["chamber_radius"]["value"] == {"value": 55, "unit": "mm"}
    assert geom["nozzle_diameter"]["value"] == {"value": 4, "unit": "mm"}


def test_determinism_snapshot_and_relocation(package, tmp_path):
    a = build_spec(package)
    detached = a.to_dict()
    detached["ready_to_execute"] = True
    assert not a.to_dict()["ready_to_execute"]
    with pytest.raises(TypeError):
        FluentAutomationSpec("{}")
    first = export_automation(package, tmp_path / "first")
    second = export_automation(package, tmp_path / "second")
    assert snapshot(first.parent) == snapshot(second.parent)
    moved = tmp_path / "relocated"
    moved.mkdir()
    shutil.copytree(tmp_path / "runs", moved / "runs")
    shutil.copytree(tmp_path / "cfd", moved / "cfd")
    relocated_package = moved / "cfd" / package.parent.name / package.name
    b = build_spec(relocated_package)
    assert generate_journal(a) == generate_journal(b)
    assert a.automation_digest == b.automation_digest
    assert verify_automation(relocated_package, first)["static_valid"]
    assert all(b"C:\\" not in v and b"D:\\" not in v for v in snapshot(first.parent).values())


def test_unresolved_and_plans(package):
    spec = build_spec(package)
    data = spec.to_dict()
    report = validate_journal(spec, generate_journal(spec))
    for key in ("geometry.axial_length", "geometry.boundary_names", "mesh.global_size", "physics.boundary_setup"):
        assert key in report["unresolved"]
    assert "value" not in data["geometry"]["boundary_names"]
    assert BOUNDARY_ROLES == ("fuel_inlet", "oxidizer_inlet", "outlet", "wall", "symmetry")
    assert data["post_processing"]["NOx"]["reason"] == "pending_contract"
    metrics = {k: v for k, v in data["post_processing"].items() if k != "NOx"}
    assert len(metrics) == 5
    for plan in metrics.values():
        assert plan["status"] == "unsupported"
        assert "required_inputs" in plan["value"] and "sample" in plan["value"]
        assert "value" not in plan["value"]["contract"]
    assert not validate_journal(spec, generate_journal(spec), executable_ready=True)["static_valid"]


def test_physics_mapping_and_unknown_extensions(package, tmp_path):
    old = load_cfd_package(package)
    condition = {"inlet_temperature": {"value": 300, "unit": "K"},
                 "inlet_pressure": {"value": 101325, "unit": "Pa"},
                 "mass_flow_rate": {"value": .01, "unit": "kg/s"},
                 "fuel_mole_fractions": {"H2": 1}, "oxidizer_mole_fractions": {"O2": .21, "N2": .79},
                 "extras": {"swirl_number": {"value": .2, "unit": "1"}}}
    config = {"solver": "Fluent", "turbulence_model": "synthetic model", "combustion_model": "synthetic chemistry",
              "steady_or_transient": "steady", "dimensionality": "3D", "extras": {"radiation": True}}
    physical = CFDSpec.create(old["case_id"], condition, config)
    run = (package.parent / old["geometry"]["run_manifest"]).resolve()
    other = export_cfd_package(run, physical, output_root=tmp_path / "physical", required_metrics=["pressure_loss"])
    data = build_spec(other).to_dict()
    for key, value in condition.items():
        if key != "extras":
            assert data["physics"][key]["value"] == value
            assert data["physics"][key]["status"] == "resolved"
            assert data["physics"][key]["source"].endswith("operating_condition." + key)
    assert data["physics"]["combustion_model"]["value"] == "synthetic chemistry"
    assert data["physics"]["boundary_setup"]["status"] == "unresolved"
    assert data["physics"]["operating_condition.extras.swirl_number"]["status"] == "unsupported"
    assert data["physics"]["simulation_config.extras.radiation"]["value"] is True


def test_explicit_mesh_and_solver(package, tmp_path):
    mesh = {"global_size": {"value": 1, "unit": "mm"}, "local_nozzle_size": {"value": .2, "unit": "mm"},
            "growth_rate": {"value": 1.2, "unit": "1"}, "boundary_layer_request": False, "strategy": "synthetic/test-only"}
    numerical = {"max_iterations": {"value": 10, "unit": "1"}, "time_step": {"value": .01, "unit": "s"},
                 "convergence_criterion": {"value": .001, "unit": "1"}, "discretization": "synthetic/test-only"}
    spec = build_spec(package, mesh=mesh, numerical_settings=numerical)
    assert spec.automation_digest != build_spec(package).automation_digest
    for layer, values in (("mesh", mesh), ("solver", numerical)):
        for key, value in values.items():
            assert spec.to_dict()[layer][key]["status"] == "resolved"
            assert spec.to_dict()[layer][key]["value"] == value
    out = export_automation(package, tmp_path / "explicit", mesh=mesh, numerical_settings=numerical)
    assert verify_automation(package, out)["static_valid"]
    assert spec.to_dict()["cfd_case_id"] == build_spec(package).to_dict()["cfd_case_id"]


@pytest.mark.parametrize("length,status", [(None, "unresolved"),
    ({"value": 100, "unit": "mm"}, "resolved"),
    ({"value": .1, "unit": "m"}, "unsupported"),
    ({"value": -1, "unit": "mm"}, "unsupported")])
def test_explicit_axial_length_is_m6_physical_input(package, tmp_path, length, status):
    old = load_cfd_package(package)
    config = old["normalized_spec"]["simulation_config"]
    config["extras"]["axial_length"] = length
    physical = CFDSpec.create(old["case_id"], old["normalized_spec"]["operating_condition"], config)
    assert physical.cfd_case_id != old["cfd_case_id"]
    run = (package.parent / old["geometry"]["run_manifest"]).resolve()
    other = export_cfd_package(run, physical, output_root=tmp_path / "axial", required_metrics=["pressure_loss"])
    spec = build_spec(other)
    entry = spec.to_dict()["geometry"]["axial_length"]
    assert entry["status"] == status
    assert entry["source"].endswith("simulation_config.extras.axial_length")
    assert validate_journal(spec, generate_journal(spec))["static_valid"]


@pytest.mark.parametrize("layer,values", [
    ("mesh", {"global_size": 1}), ("mesh", {"global_size": {"value": 1, "unit": "m"}}),
    ("mesh", {"global_size": {"value": 0, "unit": "mm"}}),
    ("mesh", {"global_size": {"value": float("nan"), "unit": "mm"}}),
    ("mesh", {"global_size": {"value": float("inf"), "unit": "mm"}}),
    ("mesh", {"global_size": {"value": True, "unit": "mm"}}),
    ("mesh", {"growth_rate": {"value": .9, "unit": "1"}}),
    ("mesh", {"boundary_layer_request": 1}), ("mesh", {"strategy": "C:/local"}),
    ("mesh", {"strategy": "/home/local"}), ("mesh", {"strategy": "{{MISSING}}"}),
    ("mesh", {"strategy": "hello\n/solve"}), ("mesh", {"unknown": True}),
    ("numerical_settings", {"max_iterations": {"value": 1.5, "unit": "1"}}),
    ("numerical_settings", {"convergence_criterion": {"value": -1, "unit": "1"}}),
])
def test_invalid_settings(package, layer, values):
    with pytest.raises((ValueError, TypeError)):
        build_spec(package, **{layer: values})


@pytest.mark.parametrize("mutation", [
    lambda b: b + b"/solve/iterate 10\n", lambda b: b + b'(system "fluent")\n',
    lambda b: b + b"; C:\\Users\\local\n", lambda b: b + b"; /home/local\n",
    lambda b: b + b"; {{missing}}\n", lambda b: b + b"; NaN\n", lambda b: b + b"; Inf\n",
    lambda b: b.replace(b"case_v1_", b"case_v2_"), lambda b: b.replace(b"run_", b"wrong_"),
    lambda b: b.replace(b"cfd_v1_", b"cfd_v2_"), lambda b: b.replace(b"0.001", b"0.01"),
    lambda b: b.replace(b"false", b"true"), lambda b: b.replace(b"\n", b"\r\n"),
    lambda b: b"\xef\xbb\xbf" + b, lambda b: b + b"\xff", lambda b: b.replace(b"boundary_names", b"fuel_inlet"),
])
def test_journal_tampering(package, mutation):
    spec = build_spec(package)
    report = validate_journal(spec, mutation(generate_journal(spec)))
    assert not report["static_valid"] and not report["ready_to_execute"] and report["errors"]


@pytest.mark.parametrize("filename", ["automation_manifest.json", "automation_spec.json", "geometry_input.json",
                                      "prepare.jou", "readiness.json", "coordinates_mm.csv"])
def test_disk_tampering(package, tmp_path, filename):
    out = export_automation(package, tmp_path / "out")
    target = out.parent / filename
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError):
        verify_automation(package, out)


def write_test_json(path, data):
    """Independent serialization, keeping semantic mutations as valid JSON."""
    path.write_bytes((json.dumps(data, ensure_ascii=False, sort_keys=True,
                                 indent=2, allow_nan=False) + "\n").encode("utf-8"))


def refresh_saved_hash(manifest_path, filename):
    """Simulate an attacker updating the saved integrity metadata, not M6."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][filename] = hashlib.sha256(
        (manifest_path.parent / filename).read_bytes()).hexdigest()
    write_test_json(manifest_path, manifest)
    # A verifier that ONLY checks saved hashes would accept all these files.
    assert all(hashlib.sha256((manifest_path.parent / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["files"].items())


def test_coordinate_semantic_tamper_with_matching_hash(package, tmp_path):
    source_before = (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))
    out = export_automation(package, tmp_path / "coordinate_tamper")
    target = out.parent / "coordinates_mm.csv"
    with target.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    original = [row.copy() for row in rows]
    rows[1]["x_mm"] = str(float(rows[1]["x_mm"]) + 1)
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with target.open(encoding="utf-8-sig", newline="") as stream:
        changed = list(csv.DictReader(stream))
    assert len(changed) == len(original)
    assert [row["nozzle_id"] for row in changed] == [row["nozzle_id"] for row in original]
    assert float(changed[1]["x_mm"]) == float(original[1]["x_mm"]) + 1
    refresh_saved_hash(out, target.name)
    # Re-derived expected hashes come from M6, not the internally consistent
    # artifact/manifest pair. The original journal remains untouched and valid.
    with pytest.raises(ValueError, match="Automation manifest identity, digest or file contract mismatch"):
        verify_automation(package, out)
    assert source_before == (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))


@pytest.mark.parametrize("field", ["source", "unit", "run_id", "case_id", "cfd_case_id"])
def test_geometry_semantic_tamper_with_matching_hash(package, tmp_path, field):
    source_before = (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))
    out = export_automation(package, tmp_path / "geometry_tamper")
    target = out.parent / "geometry_input.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    if field == "source":
        data["geometry"]["chamber_radius"]["source"] = "synthetic/test-only wrong source"
    elif field == "unit":
        data["geometry"]["chamber_radius"]["value"]["unit"] = "m"
    else:
        # Preserve ID format while making the selected identity different.
        data[field] = data[field][:-1] + ("0" if data[field][-1] != "0" else "1")
    write_test_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data
    refresh_saved_hash(out, target.name)
    with pytest.raises(ValueError, match="Automation manifest identity, digest or file contract mismatch"):
        verify_automation(package, out)
    assert source_before == (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))


@pytest.mark.parametrize("change", ["executable_ready", "resolve_missing_field"])
def test_readiness_semantic_tamper_with_matching_hash(package, tmp_path, change):
    source_before = (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))
    out = export_automation(package, tmp_path / "readiness_tamper")
    target = out.parent / "readiness.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    if change == "executable_ready":
        assert data["ready_to_execute"] is False
        data["ready_to_execute"] = True
    else:
        data["unresolved"].remove("geometry.axial_length")
        data["ready"].append("geometry.axial_length")
    write_test_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data
    refresh_saved_hash(out, target.name)
    with pytest.raises(ValueError, match="Automation manifest identity, digest or file contract mismatch"):
        verify_automation(package, out)
    assert source_before == (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))


@pytest.mark.parametrize("filename", ["automation_spec.json", "readiness.json"])
def test_artifact_path_escape_with_identical_content(package, tmp_path, monkeypatch, filename):
    source_before = (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))
    out = export_automation(package, tmp_path / "escape")
    target = out.parent / filename
    outside = tmp_path / ("outside_" + filename)
    outside.write_bytes(target.read_bytes())
    assert not outside.resolve().is_relative_to(out.parent.resolve())
    # Identical content/hash isolates containment from all content checks.
    probe = tmp_path / "escape_link"
    try:
        probe.symlink_to(outside)
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 1314}:
            raise
        real_resolve = Path.resolve
        def escaped_resolve(path, *args, **kwargs):
            if path == target:
                return real_resolve(outside)
            return real_resolve(path, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", escaped_resolve)
        mode = "simulated resolve (Windows symlink permission denied)"
    else:
        probe.replace(target)
        assert target.is_symlink()
        mode = "real symlink"
    print(f"{filename}: {mode}")
    assert target.read_bytes() == outside.read_bytes()
    assert target.resolve() == outside.resolve()
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert all(hashlib.sha256((out.parent / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["files"].items())
    expected_error = ("Artifact symlink escapes directory" if filename == "automation_spec.json"
                      else "Automation artifact mismatch: readiness.json")
    with pytest.raises(ValueError, match=expected_error):
        verify_automation(package, out)
    assert source_before == (snapshot(tmp_path / "runs"), snapshot(tmp_path / "cfd"))


@pytest.mark.parametrize("key", ["run_id", "case_id", "cfd_case_id", "coordinates"])
def test_source_tampering(package, key):
    if key == "coordinates":
        path = package.parent / "geometry/coordinates.csv"
        path.write_bytes(path.read_bytes().replace(b"10.0", b"11.0"))
    else:
        data = json.loads(package.read_text())
        data[key] = "wrong"
        package.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        build_spec(package)


def test_no_process_execution(package, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("M7 attempted an external process")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "popen", "startfile", "spawnl", "spawnv", "execv"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    assert audit_capability(environment={})["status"] == "not_detected"
    spec = build_spec(package)
    assert validate_journal(spec, generate_journal(spec))["static_valid"]
    out = export_automation(package, tmp_path / "no_process")
    assert verify_automation(package, out)["static_valid"]


def test_capability_hints_are_not_executed_versions(tmp_path):
    root = tmp_path / "ANSYS"
    exe = root / "fluent/ntbin/win64/fluent.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("synthetic/test-only, not an executable")
    result = audit_capability(environment={"AWP_ROOT252": str(root)})
    assert result["status"] == "detected" and result["installation_version_hint"] == "252"
    assert result["version"] == "unknown" and not result["executed"]
    assert audit_capability(exe, environment={})["source"] == "explicit"
    assert audit_capability(environment={"FLUENT_EXECUTABLE": str(exe)})["source"] == "FLUENT_EXECUTABLE"
    assert audit_capability(tmp_path / "missing", environment={"FLUENT_EXE": str(exe)})["status"] == "not_detected"


def test_export_never_overwrites(package, tmp_path):
    out = export_automation(package, tmp_path / "once")
    before = snapshot(out.parent)
    with pytest.raises(FileExistsError):
        export_automation(package, out.parent)
    assert before == snapshot(out.parent)
