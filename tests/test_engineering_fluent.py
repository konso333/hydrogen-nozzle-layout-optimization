"""Synthetic engineering preparation only; no solver, mesh or numerical CFD results."""

import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from cfd import load_cfd_package
from engineering_archive import archive_engineering_candidates
from engineering_cfd import prepare_engineering_cfd
from engineering_config import EngineeringGeometryConfig
import engineering_fluent as preparation
from engineering_search import run_engineering_search
from experiments.spec import canonical_json
from fluent import generate_journal, validate_journal, verify_automation
from scripts import prepare_engineering_fluent as cli


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    root = tmp_path_factory.mktemp("e7")
    report = run_engineering_search(config=EngineeringGeometryConfig("SYNTHETIC TEST ONLY", 14, 60, 2, 3),
        design={"schema_version": 1, "name": "Synthetic engineering preparation",
                "groups": [{"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]}]},
        tolerance=2e-8)
    source = root / "search.json"
    write(source, report)
    return archive_engineering_candidates(source, output_dir=root / "a", selection="all")


def make_handoff(archive, root, extras=None):
    return prepare_engineering_cfd(archive, case_id=read(archive)["cases"][0]["case_id"], output_root=root,
        profile={"schema_version": 1, "operating_condition": {},
                 "simulation_config": {"solver": "Fluent", "dimensionality": "3D",
                     "model_name": "SYNTHETIC TEST ONLY", "extras": {} if extras is None else extras},
                 "required_metrics": ["pressure_loss"]})


@pytest.fixture(scope="module")
def handoff(archive):
    return make_handoff(archive, archive.parents[2] / "c")


def domain(radius=52, length=100):
    return {"coordinate_frame": preparation.FLUID_FRAME,
            "chamber_radius": {"value": radius, "unit": "mm"},
            "axial_length": {"value": length, "unit": "mm"}}


def test_distinct_dimension_meanings_unknown_passages_and_units(handoff):
    before = snapshot(handoff.parent)
    spec = preparation.build_engineering_fluent_spec(handoff)
    data = spec.to_dict()
    geom = data["geometry"]
    for key, value in {"installation_radius": 60, "validation_radius": 57,
                       "nozzle_outer_diameter": 14, "minimum_nozzle_edge_gap": 2,
                       "minimum_wall_clearance": 3, "minimum_center_distance": 16}.items():
        assert geom[key]["value"] == {"value": value, "unit": "mm"}
        assert geom[key]["status"] == "resolved"
    assert "nozzle_diameter" not in geom
    for key in ("chamber_radius", "axial_length", "fluid_domain_frame", "nozzle_passage_geometry"):
        assert geom[key]["status"] == "unresolved" and "value" not in geom[key]
    assert geom["centers_m"]["value"] == [[value * .001 for value in point] for point in geom["centers_mm"]["value"]]
    assert all(point[2] == 0 for point in geom["centers_mm"]["value"])
    assert "simulation_config.extras.engineering_mounting" not in data["physics"]
    assert not data["ready_to_execute"]
    assert snapshot(handoff.parent) == before
    detached = spec.to_dict()
    detached["geometry"]["chamber_radius"] = {"status": "resolved"}
    assert spec.to_dict()["geometry"]["chamber_radius"]["status"] == "unresolved"
    with pytest.raises(TypeError):
        preparation.EngineeringFluentSpec("{}")
    report = validate_journal(spec, generate_journal(spec))
    assert report["static_valid"] and not report["ready_to_execute"]
    assert "geometry.chamber_radius" in report["unresolved"]
    assert "geometry.nozzle_passage_geometry" in report["unresolved"]
    assert not validate_journal(spec, b"/solve/iterate 10\n")["static_valid"]


def test_explicit_physical_dimensions_and_numerical_settings_have_separate_identity_effects(archive, handoff, tmp_path):
    source = make_handoff(archive, tmp_path / "a", {preparation.FLUID_EXTENSION: domain()})
    other = make_handoff(archive, tmp_path / "b", {preparation.FLUID_EXTENSION: domain(radius=54)})
    base = preparation.build_engineering_fluent_spec(source)
    changed = preparation.build_engineering_fluent_spec(other)
    assert base.to_dict()["cfd_case_id"] != changed.to_dict()["cfd_case_id"]
    assert base.to_dict()["case_id"] == changed.to_dict()["case_id"]
    geom = base.to_dict()["geometry"]
    assert geom["chamber_radius"]["value"] == {"value": 52, "unit": "mm"}
    assert geom["axial_length"]["value"] == {"value": 100, "unit": "mm"}
    assert geom["fluid_domain_frame"]["value"] == preparation.FLUID_FRAME
    assert geom["installation_radius"]["value"]["value"] == 60
    assert geom["nozzle_passage_geometry"]["status"] == "unresolved"
    mesh = {"global_size": {"value": 1, "unit": "mm"}}
    numerical = {"max_iterations": {"value": 50, "unit": "1"}}
    prepared = preparation.build_engineering_fluent_spec(source, mesh=mesh, numerical_settings=numerical)
    assert prepared.to_dict()["cfd_case_id"] == base.to_dict()["cfd_case_id"]
    assert prepared.automation_digest != base.automation_digest
    assert prepared.to_dict()["mesh"]["global_size"]["value"] == mesh["global_size"]
    assert prepared.to_dict()["solver"]["max_iterations"]["value"] == numerical["max_iterations"]
    assert preparation.build_engineering_fluent_spec(handoff).to_dict()["geometry"]["chamber_radius"]["status"] == "unresolved"


@pytest.mark.parametrize("value", [
    {}, {"chamber_radius": None}, "cylinder",
    {**domain(), "unknown": None}, {**domain(), "coordinate_frame": "another_frame"},
    {**domain(), "coordinate_frame": None},
    {**domain(), "chamber_radius": {"value": 0, "unit": "mm"}},
    {**domain(), "chamber_radius": {"value": -1, "unit": "mm"}},
    {**domain(), "chamber_radius": {"value": .052, "unit": "m"}},
    {**domain(), "axial_length": {"value": 0, "unit": "mm"}},
    {**domain(), "axial_length": {"value": .1, "unit": "m"}},
    {**domain(), "chamber_radius": {"value": 1, "unit": "mm"}},
])
def test_invalid_or_incompatible_fluid_declarations_create_no_preparation_output(archive, tmp_path, value):
    source = make_handoff(archive, tmp_path / "c", {preparation.FLUID_EXTENSION: value})
    with pytest.raises(ValueError):
        preparation.export_engineering_fluent(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_unknown_partial_legacy_and_conflicting_axial_lengths(archive, tmp_path):
    legacy = make_handoff(archive, tmp_path / "l", {"axial_length": {"value": 20, "unit": "mm"}})
    mapped = preparation.build_engineering_fluent_spec(legacy).to_dict()
    assert mapped["geometry"]["axial_length"]["status"] == "unresolved"
    assert mapped["physics"]["simulation_config.extras.axial_length"]["status"] == "unsupported"
    unknown = make_handoff(archive, tmp_path / "u", {preparation.FLUID_EXTENSION: {
        "coordinate_frame": None, "chamber_radius": None, "axial_length": None}})
    assert preparation.build_engineering_fluent_spec(unknown).to_dict()["geometry"]["fluid_domain_frame"]["status"] == "unresolved"
    partial = make_handoff(archive, tmp_path / "p", {preparation.FLUID_EXTENSION: {**domain(), "axial_length": None}})
    assert preparation.build_engineering_fluent_spec(partial).to_dict()["geometry"]["axial_length"]["status"] == "unresolved"
    conflict = make_handoff(archive, tmp_path / "x", {preparation.FLUID_EXTENSION: domain(),
                                                     "axial_length": {"value": 20, "unit": "mm"}})
    with pytest.raises(ValueError, match="Conflicting"):
        preparation.build_engineering_fluent_spec(conflict)
    alias = make_handoff(archive, tmp_path / "alias", {preparation.FLUID_EXTENSION.upper(): domain()})
    with pytest.raises(ValueError, match="exact physical extension"):
        preparation.build_engineering_fluent_spec(alias)


def test_center_projection_check_preserves_saved_tolerance(archive, tmp_path):
    # This only checks projected centres, never the unknown internal passages.
    radius = math.hypot(8, 8)
    source = make_handoff(archive, tmp_path / "a", {preparation.FLUID_EXTENSION: domain(radius=radius - 1e-8)})
    assert preparation.build_engineering_fluent_spec(source)
    outside = make_handoff(archive, tmp_path / "b", {preparation.FLUID_EXTENSION: domain(radius=radius - 1e-7)})
    with pytest.raises(ValueError, match="nozzle centre"):
        preparation.build_engineering_fluent_spec(outside)


def test_export_read_only_verification_determinism_and_source_preservation(archive, handoff, tmp_path):
    before_archive = snapshot(archive.parent)
    before = snapshot(handoff.parent)
    path = preparation.export_engineering_fluent(handoff, tmp_path / "out")
    report = preparation.verify_engineering_fluent(handoff, path)
    assert report["static_valid"] and not report["ready_to_execute"]
    assert snapshot(archive.parent) == before_archive and snapshot(handoff.parent) == before
    assert not list((handoff.parent / "attempts").iterdir())
    assert {p.name for p in path.parent.iterdir()} == {preparation.MANIFEST_NAME, preparation.SPEC_NAME,
        "engineering_geometry_input.json", "prepare_engineering.jou", "readiness.json", "coordinates_mm.csv"}
    assert not (path.parent / "automation_manifest.json").exists()
    assert (path.parent / "coordinates_mm.csv").read_bytes() == (handoff.parent / "geometry/coordinates.csv").read_bytes()
    assert all(line.startswith(b"; ") for line in (path.parent / "prepare_engineering.jou").read_bytes().splitlines())
    before_output = snapshot(path.parent)
    assert preparation.verify_engineering_fluent(handoff, path)["static_valid"]
    assert snapshot(path.parent) == before_output
    second = preparation.export_engineering_fluent(handoff, tmp_path / "second")
    assert snapshot(second.parent) == before_output
    assert load_cfd_package(handoff.with_name("cfd_case.json"))["status"] == "prepared"


def test_portable_replay(archive, handoff, tmp_path):
    original = preparation.export_engineering_fluent(handoff, tmp_path / "out")
    root = archive.parents[2]
    moved_root = tmp_path / "moved"
    shutil.copytree(root, moved_root)
    moved = moved_root / handoff.relative_to(root)
    assert preparation.build_engineering_fluent_spec(handoff).automation_digest == preparation.build_engineering_fluent_spec(moved).automation_digest
    assert preparation.verify_engineering_fluent(moved, original)["static_valid"]


def test_preparation_and_replay_never_start_external_processes(handoff, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Engineering preparation must not launch processes")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    path = preparation.export_engineering_fluent(handoff, tmp_path / "out")
    assert preparation.verify_engineering_fluent(handoff, path)["static_valid"]
    with pytest.raises((OSError, ValueError, KeyError)):
        verify_automation(handoff.with_name("cfd_case.json"), path)


def test_changed_engineering_source_cannot_be_prepared_or_replayed(archive, handoff, tmp_path):
    root = archive.parents[2]
    destination = tmp_path / "changed_source"
    shutil.copytree(root, destination)
    moved = destination / handoff.relative_to(root)
    path = preparation.export_engineering_fluent(moved, tmp_path / "valid")
    value = read(moved)
    value["configuration"]["nozzle_outer_diameter_mm"] = 4
    write(moved, value)
    before = snapshot(destination)
    with pytest.raises(ValueError, match="handoff verification failed"):
        preparation.export_engineering_fluent(moved, tmp_path / "invalid")
    with pytest.raises(ValueError, match="handoff verification failed"):
        preparation.verify_engineering_fluent(moved, path)
    assert not (tmp_path / "invalid").exists()
    assert snapshot(destination) == before


@pytest.mark.parametrize("filename", [preparation.MANIFEST_NAME, preparation.SPEC_NAME,
    "engineering_geometry_input.json", "prepare_engineering.jou", "readiness.json", "coordinates_mm.csv"])
def test_each_changed_artifact_is_rejected(handoff, tmp_path, filename):
    path = preparation.export_engineering_fluent(handoff, tmp_path / "out")
    target = path.parent / filename
    target.write_bytes(target.read_bytes() + b"\n")
    before = snapshot(path.parent)
    with pytest.raises(ValueError, match="mismatch"):
        preparation.verify_engineering_fluent(handoff, path)
    assert snapshot(path.parent) == before


def test_forged_consistent_geometry_journal_readiness_and_checksums_cannot_override_source(handoff, tmp_path):
    path = preparation.export_engineering_fluent(handoff, tmp_path / "out")
    data = read(path.parent / preparation.SPEC_NAME)
    data["geometry"]["chamber_radius"] = {"status": "resolved", "source": "forged",
                                          "value": {"value": 57, "unit": "mm"}}
    forged = SimpleNamespace(to_dict=lambda: copy.deepcopy(data),
                             automation_digest=preparation._sha256(canonical_json(data).encode("utf-8")))
    raw = (path.parent / "coordinates_mm.csv").read_bytes()
    files, manifest, _ = preparation._artifacts(forged, raw)
    for name, value in files.items():
        (path.parent / name).write_bytes(value)
    preparation._write_manifest(path, manifest)
    with pytest.raises(ValueError, match="manifest mismatch"):
        preparation.verify_engineering_fluent(handoff, path)


def test_existing_outputs_and_source_directories_are_protected(archive, handoff, tmp_path):
    path = preparation.export_engineering_fluent(handoff, tmp_path / "out")
    before = snapshot(path.parent)
    with pytest.raises(FileExistsError):
        preparation.export_engineering_fluent(handoff, path.parent)
    assert snapshot(path.parent) == before
    for root in (handoff.parent, archive.parent):
        before = snapshot(root)
        with pytest.raises(ValueError, match="outside"):
            preparation.export_engineering_fluent(handoff, root / "new_preparation")
        assert snapshot(root) == before


@pytest.mark.parametrize("failure", ["geometry", "manifest", "verification"])
def test_failed_export_is_not_a_completed_preparation(handoff, tmp_path, monkeypatch, failure):
    original_write = Path.write_bytes
    original_manifest = preparation._write_manifest
    def fail_geometry(path, value):
        if path.name == "engineering_geometry_input.json":
            raise OSError("synthetic geometry export failure")
        return original_write(path, value)
    def fail_manifest(path, value):
        if value["status"] == "prepared":
            raise OSError("synthetic final manifest write failure")
        return original_manifest(path, value)
    if failure == "geometry":
        monkeypatch.setattr(Path, "write_bytes", fail_geometry)
    elif failure == "manifest":
        monkeypatch.setattr(preparation, "_write_manifest", fail_manifest)
    else:
        monkeypatch.setattr(preparation, "verify_engineering_fluent", lambda *args: {"static_valid": False})
    with pytest.raises((OSError, ValueError)):
        preparation.export_engineering_fluent(handoff, tmp_path / "out")
    assert read(tmp_path / "out" / preparation.MANIFEST_NAME)["status"] == "failed"
    assert not list((handoff.parent / "attempts").iterdir())


@pytest.mark.parametrize("settings", [
    {"schema_version": True, "mesh": {}, "numerical_settings": {}},
    {"schema_version": 1, "mesh": None, "numerical_settings": {}},
    {"schema_version": 1, "mesh": {"chamber_radius": {"value": 60, "unit": "mm"}}, "numerical_settings": {}},
    {"schema_version": 1, "mesh": {}, "numerical_settings": {"max_iterations": {"value": True, "unit": "1"}}},
    {"schema_version": 1, "mesh": {}, "numerical_settings": {}, "physical_override": {}},
])
def test_invalid_settings_cannot_create_output_or_override_physics(handoff, tmp_path, settings):
    source = tmp_path / "settings.json"
    write(source, settings)
    with pytest.raises(SystemExit) as error:
        cli.main(["--handoff", str(handoff), "--output-dir", str(tmp_path / "out"), "--settings", str(source)])
    assert error.value.code == 2 and not (tmp_path / "out").exists()


def test_cli_default_template_and_verify(handoff, tmp_path, capsys):
    template = ROOT / "examples/engineering_automation_settings.template.json"
    assert read(template) == {"schema_version": 1, "mesh": {}, "numerical_settings": {}}
    assert cli.main(["--handoff", str(handoff), "--output-dir", str(tmp_path / "out"), "--settings", str(template)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ready_to_execute"] is False
    path = Path(result["engineering_automation_manifest"])
    assert cli.main(["--handoff", str(handoff), "--verify", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["static_valid"]


@pytest.mark.parametrize("arguments", [[], ["--handoff", "handoff.json"],
    ["--output-dir", "out"], ["--handoff", "handoff.json", "--verify", "manifest.json", "--settings", "settings.json"],
    ["--handoff", "handoff.json", "--verify", "manifest.json", "--output-dir", "out"]])
def test_cli_argument_errors(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
