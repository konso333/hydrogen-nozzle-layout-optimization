"""Synthetic mounting/condition inputs only; no Fluent execution or CFD values."""

import copy
import json
from pathlib import Path
import shutil

import pytest

from cfd import create_attempt, load_attempt, load_cfd_package
from engineering_archive import archive_engineering_candidates, verify_engineering_archive
from engineering_config import EngineeringGeometryConfig
import engineering_cfd as handoff
from engineering_search import run_engineering_search
from experiments.archive import verify_run
from fluent.spec import build_spec
from scripts import prepare_engineering_cfd as cli


ROOT = Path(__file__).resolve().parents[1]


def profile():
    return {
        "schema_version": 1,
        "operating_condition": {
            "inlet_temperature": {"value": 300, "unit": "K"},
            "extras": {"fuel_inlet": {"velocity": {"value": 10, "unit": "m/s"}}},
        },
        "simulation_config": {"solver": "Fluent", "dimensionality": "3D",
                              "turbulence_model": "laminar", "combustion_model": "disabled",
                              "extras": {"axial_length": {"value": 20, "unit": "mm"}}},
        "required_metrics": ["pressure_loss", "outlet_temperature_mean"],
    }


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def make_archive(directory, config):
    directory.mkdir()
    report = run_engineering_search(config=config, design={
        "schema_version": 1, "name": "SYNTHETIC CFD HANDOFF TEST ONLY",
        "symmetry_tolerance": 2e-5,
        "groups": [{"N": 4, "layout_type": "rectangular",
                    "parameter_sets": [{"spacing": 16}, {"spacing": 24}]}],
    }, tolerance=2e-8)
    source = directory / "search.json"
    write(source, report)
    return archive_engineering_candidates(source, output_dir=directory / "a", selection="all")


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    root = tmp_path_factory.mktemp("e6")
    return make_archive(root / "src", EngineeringGeometryConfig("SYNTHETIC TEST ONLY", 14, 60, 2, 3))


def selected(archive):
    return read(archive)["cases"][0]["case_id"]


def prepare(archive, output_root, value=None):
    return handoff.prepare_engineering_cfd(archive, case_id=selected(archive),
                                           profile=profile() if value is None else value,
                                           output_root=output_root)


@pytest.fixture(scope="module")
def prepared(archive):
    return prepare(archive, archive.parents[2] / "cfd")


def copy_package(prepared, tmp_path):
    # Keeping the source in place means M6 relative links must be rebased when
    # copying only a package; instead export a fresh package for mutation tests.
    package = load_cfd_package(prepared.with_name("cfd_case.json"))
    source = (prepared.parent / package["geometry"]["run_manifest"]).resolve().with_name("engineering_run.json")
    return prepare(source, tmp_path / "cfd")


def test_preparation_preserves_source_coordinates_specs_and_m6_contract(archive, tmp_path):
    before = snapshot(archive.parent)
    source_profile = profile()
    profile_before = copy.deepcopy(source_profile)
    path = prepare(archive, tmp_path / "cfd", source_profile)
    saved = read(path)
    package_path = path.with_name("cfd_case.json")
    package = load_cfd_package(package_path)
    assert saved["status"] == package["status"] == "prepared"
    assert saved["ready_to_execute"] is False
    assert source_profile == profile_before
    assert saved["configuration"] == read(archive)["configuration"]
    case = archive.parent / "cases" / selected(archive)
    assert saved["engineering_case"] == read(case / "engineering.json")
    assert saved["engineering_case"]["geometry_case_spec"]["geometry"]["tolerance"] == 2e-8
    assert saved["engineering_case"]["geometry_case_spec"]["symmetry_tolerance"] == 2e-5
    assert (path.parent / "geometry/coordinates.csv").read_bytes() == (case / "coordinates.csv").read_bytes()
    assert package["geometry"]["units"]["length"] == "mm"
    assert not list((path.parent / "attempts").iterdir())
    physical = package["normalized_spec"]
    assert physical["operating_condition"]["extras"] == profile()["operating_condition"]["extras"]
    assert physical["simulation_config"]["extras"]["axial_length"] == {"value": 20, "unit": "mm"}
    mounting = physical["simulation_config"]["extras"][handoff.MOUNTING_EXTENSION]
    assert mounting == {key: {"value": value, "unit": "mm"}
                        for key, value in saved["configuration"].items() if key not in {"name", "schema_version"}}
    assert package["result_contract"]["metrics"]["pressure_loss"]["required"]
    assert not package["result_contract"]["metrics"]["hydrogen_conversion"]["required"]
    assert snapshot(archive.parent) == before
    assert verify_engineering_archive(archive)["overall_match"]
    assert verify_run(archive.with_name("run.json"))["overall_match"]
    assert handoff.verify_engineering_cfd(path)["overall_match"]


def test_different_installations_with_same_geometry_get_distinct_cfd_ids(archive, tmp_path):
    other = make_archive(tmp_path / "src", EngineeringGeometryConfig("Other synthetic installation", 14, 65, 2, 8))
    assert selected(other) == selected(archive)  # Both effective radii are 57 mm.
    a = prepare(archive, tmp_path / "cfd")
    b = prepare(other, tmp_path / "cfd")
    first, second = read(a), read(b)
    assert first["case_id"] == second["case_id"]
    assert first["operating_condition_id"] == second["operating_condition_id"]
    assert first["cfd_case_id"] != second["cfd_case_id"]
    assert handoff.verify_engineering_cfd(a)["overall_match"]
    assert handoff.verify_engineering_cfd(b)["overall_match"]


@pytest.mark.parametrize("field,value", [("nozzle_outer_diameter_mm", 15),
    ("installation_radius_mm", 61), ("nozzle_edge_gap_mm", 3), ("wall_clearance_mm", 4)])
def test_each_mounting_quantity_participates_in_cfd_identity(archive, field, value):
    config = EngineeringGeometryConfig("Synthetic", 14, 60, 2, 3).to_dict()
    def spec(data):
        return handoff.make_engineering_cfd_spec(selected(archive), EngineeringGeometryConfig.from_dict(data),
            operating_condition=profile()["operating_condition"], simulation_config=profile()["simulation_config"])
    a = spec(config)
    config[field] = value
    b = spec(config)
    assert a.cfd_case_id != b.cfd_case_id
    assert a.operating_condition_id == b.operating_condition_id


def test_labels_and_normalization_do_not_change_identity(archive):
    a = EngineeringGeometryConfig("Original name", 14, 60, 2, 3)
    b = EngineeringGeometryConfig("Renamed", 14., 60., 2., 3.)
    kwargs = {key: profile()[key] for key in ("operating_condition", "simulation_config")}
    first = handoff.make_engineering_cfd_spec(selected(archive), a, **kwargs)
    second = handoff.make_engineering_cfd_spec(selected(archive), b, **kwargs)
    assert first == second
    with pytest.raises(ValueError, match="Unspecified"):
        handoff.make_engineering_cfd_spec(selected(archive), EngineeringGeometryConfig("pending", 14), **kwargs)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema_version=True), lambda p: p.update(schema_version=2),
    lambda p: p.update(unknown="ignored"), lambda p: p.pop("simulation_config"),
    lambda p: p.update(required_metrics=None), lambda p: p.update(required_metrics=[]),
    lambda p: p.update(required_metrics="pressure_loss"), lambda p: p.update(required_metrics=[{}]),
    lambda p: p.update(required_metrics=["pressure_loss", "pressure_loss"]),
    lambda p: p.update(required_metrics=["NOx"]),
    lambda p: p["operating_condition"].update(inlet_temperature={"value": True, "unit": "K"}),
    lambda p: p["simulation_config"].update(dimensionality="4D"),
    lambda p: p["simulation_config"]["extras"].update(engineering_mounting={}),
    lambda p: p["simulation_config"]["extras"].update(ENGINEERING_MOUNTING=None),
    lambda p: p["operating_condition"]["extras"].update(engineering_mounting={}),
])
def test_invalid_profiles_create_no_output(archive, tmp_path, mutation):
    value = profile()
    mutation(value)
    with pytest.raises(ValueError):
        prepare(archive, tmp_path / "out", value)
    assert not (tmp_path / "out").exists()


def test_explicit_selection_template_and_source_fail_closed(archive, tmp_path):
    template = read(ROOT / "examples/engineering_cfd_profile.template.json")
    assert template["required_metrics"] is None
    with pytest.raises(ValueError, match="required_metrics"):
        prepare(archive, tmp_path / "out", template)
    with pytest.raises(ValueError, match="explicit case_id"):
        handoff.prepare_engineering_cfd(archive, case_id="missing", profile=profile(), output_root=tmp_path / "out")
    with pytest.raises(ValueError, match="engineering_run.json"):
        handoff.prepare_engineering_cfd(archive.with_name("run.json"), case_id=selected(archive),
                                        profile=profile(), output_root=tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert read(ROOT / "examples/engineering_nozzle_14mm.json") == {
        "schema_version": 1, "name": "14 mm nozzle array engineering preparation",
        "nozzle_outer_diameter_mm": 14, "installation_radius_mm": None,
        "nozzle_edge_gap_mm": None, "wall_clearance_mm": None,
    }


def test_unresolved_conditions_are_retained_without_execution_readiness(archive, tmp_path):
    value = read(ROOT / "examples/engineering_cfd_profile.template.json")
    value["required_metrics"] = ["pressure_loss"]
    path = prepare(archive, tmp_path / "out", value)
    saved = read(path)
    assert saved["profile"]["operating_condition"]["inlet_temperature"] is None
    assert saved["ready_to_execute"] is False
    mapped = build_spec(path.with_name("cfd_case.json")).to_dict()
    assert mapped["ready_to_execute"] is False
    assert mapped["physics"]["simulation_config.extras.engineering_mounting"]["status"] == "unsupported"


def test_repeats_and_output_inside_source_never_overwrite(archive, prepared):
    before = snapshot(prepared.parent)
    with pytest.raises(FileExistsError):
        prepare(archive, prepared.parent.parent)
    assert snapshot(prepared.parent) == before
    before = snapshot(archive.parent)
    with pytest.raises(ValueError, match="outside"):
        prepare(archive, archive.parent / "cfd")
    assert snapshot(archive.parent) == before


def test_read_only_verification_portability_and_attempt_independence(archive, prepared, tmp_path, capsys):
    # Move source + CFD together, keeping their relative directory structure.
    root = archive.parents[2]
    destination = tmp_path / "moved"
    shutil.copytree(root, destination)
    moved = destination / prepared.relative_to(root)
    before = snapshot(destination)
    assert cli.main(["--verify", str(moved)]) == 0
    assert json.loads(capsys.readouterr().out)["overall_match"]
    assert snapshot(destination) == before
    package = moved.with_name("cfd_case.json")
    attempt = create_attempt(package, data_kind="synthetic/test-only")
    record = load_attempt(package, attempt.stem)
    assert record["status"] == "not_started"
    assert all(value is None for value in record["metrics"].values())
    assert handoff.verify_engineering_cfd(moved)["overall_match"]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(ready_to_execute=True), lambda p: p.update(case_id="wrong"),
    lambda p: p["configuration"].update(installation_radius_mm=99),
    lambda p: p["engineering_case"]["validation"].update(feasible=False),
    lambda p: p["source"].update(engineering_manifest="../../unrelated.json"),
    lambda p: p.update(m6_package_sha256="wrong"), lambda p: p.update(unexpected=True),
])
def test_edited_sidecars_are_detected_without_following_paths(prepared, tmp_path, mutation):
    path = copy_package(prepared, tmp_path)
    value = read(path)
    mutation(value)
    write(path, value)
    before = snapshot(path.parent)
    assert not handoff.verify_engineering_cfd(path)["overall_match"]
    assert snapshot(path.parent) == before


def test_changed_profile_package_and_source_cannot_pass_verification(archive, prepared, tmp_path):
    path = copy_package(prepared, tmp_path)
    value = read(path)
    value["profile"]["operating_condition"]["inlet_temperature"]["value"] = 301
    write(path, value)
    with pytest.raises(ValueError, match="physical specification"):
        handoff.verify_engineering_cfd(path)
    other = copy_package(prepared, tmp_path / "other")
    coordinates = other.parent / "geometry/coordinates.csv"
    coordinates.write_bytes(coordinates.read_bytes() + b"99,0,0,0\n")
    with pytest.raises(ValueError, match="checksum"):
        handoff.verify_engineering_cfd(other)
    moved = tmp_path / "source"
    shutil.copytree(archive.parent, moved)
    value = read(moved / "engineering_run.json")
    value["configuration"]["installation_radius_mm"] = 99
    write(moved / "engineering_run.json", value)
    with pytest.raises(ValueError, match="archive verification"):
        prepare(moved / "engineering_run.json", tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_valid_but_changed_m6_result_contract_is_detected(prepared, tmp_path):
    path = copy_package(prepared, tmp_path)
    package_path = path.with_name("cfd_case.json")
    package = read(package_path)
    package["result_contract"]["metrics"]["pressure_loss"]["required"] = False
    write(package_path, package)
    assert load_cfd_package(package_path)  # Still a valid ordinary M6 package.
    value = read(path)
    value["m6_package_sha256"] = handoff._sha256(package_path)
    write(path, value)
    checked = handoff.verify_engineering_cfd(path)
    assert checked["engineering_handoff_match"]
    assert not checked["result_contract_match"] and not checked["overall_match"]


def test_sidecar_write_failure_records_failed_and_keeps_m6_prepared(archive, tmp_path, monkeypatch):
    original = handoff._write_json
    def fail(path, value):
        if value["status"] == "prepared":
            raise OSError("synthetic final handoff failure")
        original(path, value)
    monkeypatch.setattr(handoff, "_write_json", fail)
    with pytest.raises(OSError, match="synthetic"):
        prepare(archive, tmp_path / "cfd")
    path = next((tmp_path / "cfd").glob("*/engineering_handoff.json"))
    assert read(path)["status"] == "failed"
    assert load_cfd_package(path.with_name("cfd_case.json"))["status"] == "prepared"
    with pytest.raises(ValueError, match="prepared v1"):
        handoff.verify_engineering_cfd(path)


def test_final_verification_failure_cannot_leave_prepared_marker(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "verify_engineering_cfd", lambda path: {"overall_match": False})
    with pytest.raises(ValueError, match="handoff verification failed"):
        prepare(archive, tmp_path / "cfd")
    path = next((tmp_path / "cfd").glob("*/engineering_handoff.json"))
    assert read(path)["status"] == "failed"
    assert not list((path.parent / "attempts").iterdir())


def test_cli_preparation_and_mismatch_exit_codes(archive, tmp_path, capsys):
    source = tmp_path / "profile.json"
    write(source, profile())
    assert cli.main(["--archive", str(archive), "--case-id", selected(archive),
                     "--profile", str(source), "--output-root", str(tmp_path / "cfd")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prepared" and result["ready_to_execute"] is False
    path = Path(result["engineering_handoff"])
    value = read(path)
    value["ready_to_execute"] = True
    write(path, value)
    assert cli.main(["--verify", str(path)]) == 1


@pytest.mark.parametrize("arguments", [[], ["--archive", "archive.json"],
    ["--verify", "handoff.json", "--profile", "profile.json"],
    ["--verify", "handoff.json", "--case-id", "case_v1_abc"],
    ["--verify", "handoff.json", "--output-root", "out"],
    ["--verify", "handoff.json", "--archive", "archive.json"]])
def test_cli_argument_errors(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
