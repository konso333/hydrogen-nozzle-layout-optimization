"""Selected-search archive tests with synthetic, unapproved mounting inputs."""

import copy
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from matplotlib.figure import Figure
from matplotlib.patches import Circle

import engineering_archive as archive
from engineering_config import EngineeringGeometryConfig
from engineering_search import run_engineering_search
from experiments.archive import verify_run
from experiments.spec import CaseSpec
from scripts import archive_engineering_candidates as cli


@pytest.fixture(scope="module")
def source_data():
    config = EngineeringGeometryConfig("SYNTHETIC ARCHIVE TEST ONLY", 14, 60, 2, 3)
    design = {
        "schema_version": 1, "name": "Synthetic engineering archive test",
        "symmetry_tolerance": 2e-5,
        "groups": [{"N": [4, 24], "layout_type": "rectangular",
                    "parameter_sets": [{"spacing": 16}, {"spacing": 24}]}],
    }
    report = run_engineering_search(config=config, design=design, tolerance=2e-8)
    assert (report["feasible"], report["infeasible"], report["pareto_count"]) == (3, 1, 2)
    report["provenance"] = {"git": {"commit": "historical-source-test"}}
    return report


def write_source(directory, data):
    path = directory / "source.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=3), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def full_archive(tmp_path_factory, source_data):
    directory = tmp_path_factory.mktemp("e5")
    source = write_source(directory, source_data)
    manifest = archive.archive_engineering_candidates(source, output_dir=directory / "out")
    return manifest, source


def test_selected_custom_tolerances_identity_coordinates_and_physical_profile_survive(full_archive, source_data):
    path, source = full_archive
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed" and manifest["case_count"] == 2
    assert manifest["selection"] == "pareto"
    assert (path.parent / "search_report.json").read_bytes() == source.read_bytes()
    expected_ids = [row["case_id"] for row in source_data["rows"] if row["pareto_candidate"]]
    assert [entry["case_id"] for entry in manifest["cases"]] == expected_ids
    for entry in manifest["cases"]:
        root = path.parent / "cases" / entry["case_id"]
        case = json.loads((root / "case.json").read_text(encoding="utf-8"))
        physical = json.loads((root / "engineering.json").read_text(encoding="utf-8"))
        spec = CaseSpec.from_normalized(case["normalized_spec"])
        assert spec.case_id == physical["geometry_case_id"] == entry["case_id"]
        assert spec.normalized_spec["geometry"]["tolerance"] == 2e-8
        assert spec.normalized_spec["symmetry_tolerance"] == 2e-5
        assert spec.normalized_spec == physical["geometry_case_spec"]
        assert physical["configuration"] == source_data["configuration"]
        assert physical["validation"]["feasible"]
        assert physical["measured_clearances_mm"]["minimum_nozzle_edge_gap"] >= 2 - 2e-8
        assert physical["measured_clearances_mm"]["minimum_wall_clearance"] >= 3 - 2e-8
        with (root / "coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames == ["nozzle_id", "x_mm", "y_mm", "z_mm"]
            rows = list(reader)
        assert [int(row["nozzle_id"]) for row in rows] == list(range(1, len(rows) + 1))
        assert all(float(row["z_mm"]) == 0 for row in rows)
        np.testing.assert_allclose([(float(row["x_mm"]), float(row["y_mm"])) for row in rows],
                                   spec.generate(), rtol=0, atol=1e-12)
        for name in ("layout.png", "engineering_layout.png"):
            assert (root / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert case["cfd"]["status"] == "not_started"
    assert verify_run(path.parent / "run.json")["overall_match"]
    assert archive.verify_engineering_archive(path)["overall_match"]


def test_verification_is_read_only_and_archive_is_portable(full_archive, tmp_path, capsys):
    original, _ = full_archive
    moved = tmp_path / "moved"
    shutil.copytree(original.parent, moved)
    before = {file.relative_to(moved): file.read_bytes() for file in moved.rglob("*") if file.is_file()}
    assert cli.main(["--verify", str(moved / "engineering_run.json")]) == 0
    assert json.loads(capsys.readouterr().out)["overall_match"]
    assert before == {file.relative_to(moved): file.read_bytes() for file in moved.rglob("*") if file.is_file()}


@pytest.mark.parametrize("target", ["coordinates.csv", "engineering.json", "engineering_layout.png"])
def test_changed_artifacts_are_detected(full_archive, tmp_path, target):
    original, _ = full_archive
    moved = tmp_path / "changed"
    shutil.copytree(original.parent, moved)
    manifest = json.loads((moved / "engineering_run.json").read_text(encoding="utf-8"))
    file = moved / "cases" / manifest["cases"][0]["case_id"] / target
    if target == "coordinates.csv":
        with file.open("a", encoding="utf-8") as stream:
            stream.write("99,0,0,0\n")
    elif target == "engineering.json":
        value = json.loads(file.read_text(encoding="utf-8"))
        value["configuration"]["installation_radius_mm"] = 65
        file.write_text(json.dumps(value), encoding="utf-8")
    else:
        file.write_bytes(file.read_bytes() + b"changed")
    assert not archive.verify_engineering_archive(moved / "engineering_run.json")["overall_match"]


@pytest.mark.parametrize("mutation", [
    lambda value: value["rows"][0].update(pareto_candidate=True),
    lambda value: value["rows"][0].update(min_center_distance=99),
    lambda value: value.update(feasible=99),
    lambda value: value["configuration"].update(installation_radius_mm=65),
    lambda value: value["configuration"].update(wall_clearance_mm=None),
    lambda value: value["validation_geometry"].update(d=4),
    lambda value: value["specifications"][0]["geometry"].update(R=55),
    lambda value: value["search_space"]["blocks"][0]["geometry_space"].update(R=[55]),
])
def test_inconsistent_report_is_rejected_before_creating_any_output(source_data, tmp_path, mutation):
    data = copy.deepcopy(source_data)
    mutation(data)
    source = write_source(tmp_path, data)
    output = tmp_path / "must_not_exist"
    with pytest.raises(ValueError):
        archive.archive_engineering_candidates(source, output_dir=output)
    assert not output.exists()


@pytest.mark.parametrize("selection", [[], ["missing"], ["duplicate", "duplicate"], "unknown"])
def test_invalid_selection_creates_no_output(source_data, tmp_path, selection):
    source = write_source(tmp_path, source_data)
    output = tmp_path / "must_not_exist"
    with pytest.raises(ValueError, match="selection|Selection"):
        archive.archive_engineering_candidates(source, output_dir=output, selection=selection)
    assert not output.exists()


def test_infeasible_case_id_cannot_be_selected(source_data, tmp_path):
    source = write_source(tmp_path, source_data)
    case_id = source_data["infeasible_cases"][0]["case_id"]
    with pytest.raises(ValueError, match="feasible case IDs"):
        archive.archive_engineering_candidates(source, output_dir=tmp_path / "out", selection=[case_id])
    assert not (tmp_path / "out").exists()


def test_explicit_nonpareto_selection_draws_actual_installation_boundaries(source_data, tmp_path, monkeypatch, capsys):
    source = write_source(tmp_path, source_data)
    case_id = next(row["case_id"] for row in source_data["rows"] if not row["pareto_candidate"])
    captured = []
    original = Figure.savefig

    def capture(fig, path, *args, **kwargs):
        if Path(path).name == "engineering_layout.png":
            captured.extend((patch.get_label(), patch.radius) for patch in fig.axes[0].patches
                            if isinstance(patch, Circle))
        return original(fig, path, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    assert cli.main(["--report", str(source), "--output-dir", str(tmp_path / "out"), "--case-id", case_id]) == 0
    path = Path(json.loads(capsys.readouterr().out)["engineering_manifest"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert [entry["case_id"] for entry in manifest["cases"]] == [case_id]
    assert captured[:3] == [("Installation boundary", 60),
                            ("Nozzle edge limit (wall clearance)", 57),
                            ("Allowed centre boundary", 50)]
    assert [radius for _, radius in captured[3:]] == [7] * 4


def test_all_selection_keeps_independent_placement_records(source_data, tmp_path):
    # Use all three feasible cases; the single source profile stays attached to each.
    source = write_source(tmp_path, source_data)
    path = archive.archive_engineering_candidates(source, output_dir=tmp_path / "out", selection="all")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["case_count"] == source_data["feasible"] == 3
    assert archive.verify_engineering_archive(path)["overall_match"]


def test_no_pareto_candidates_produces_no_empty_archive(tmp_path):
    config = EngineeringGeometryConfig("single nozzle test only", 14, 7, 0, 0)
    report = run_engineering_search(config=config, design={
        "schema_version": 1, "name": "single", "groups": [
            {"N": 1, "layout_type": "rectangular", "parameter_sets": [{"spacing": 14}]}],
    })
    source = write_source(tmp_path, report)
    with pytest.raises(ValueError, match="nonempty"):
        archive.archive_engineering_candidates(source, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()
    path = archive.archive_engineering_candidates(source, output_dir=tmp_path / "out", selection="all")
    assert archive.verify_engineering_archive(path)["overall_match"]


def test_existing_output_is_never_overwritten(full_archive):
    manifest, source = full_archive
    output = manifest.parent.parent
    before = manifest.read_bytes()
    with pytest.raises(FileExistsError):
        archive.archive_engineering_candidates(source, output_dir=output)
    assert manifest.read_bytes() == before


def test_engineering_export_failure_sets_failed_status_but_preserves_completed_m2(source_data, tmp_path, monkeypatch):
    source = write_source(tmp_path, source_data)

    def fail(*args, **kwargs):
        raise OSError("synthetic engineering image write failure")

    monkeypatch.setattr(archive, "plot_engineering_layout", fail)
    output = tmp_path / "out"
    with pytest.raises(OSError, match="synthetic"):
        archive.archive_engineering_candidates(source, output_dir=output)
    path = next(output.glob("*/engineering_run.json"))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed" and manifest["case_count"] == 0
    assert manifest["error_type"] == "OSError"
    assert verify_run(path.parent / "run.json")["overall_match"]
    with pytest.raises(ValueError, match="completed"):
        archive.verify_engineering_archive(path)


def test_completed_manifest_write_failure_is_recorded_as_failed(source_data, tmp_path, monkeypatch):
    source = write_source(tmp_path, source_data)
    original = archive._write_json

    def fail_completion(path, value):
        if path.name == "engineering_run.json" and value["status"] == "completed":
            raise OSError("synthetic final report failure")
        return original(path, value)

    monkeypatch.setattr(archive, "_write_json", fail_completion)
    with pytest.raises(OSError, match="final report"):
        archive.archive_engineering_candidates(source, output_dir=tmp_path / "out")
    path = next((tmp_path / "out").glob("*/engineering_run.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "failed"


def test_source_bytes_and_fixed_engineering_file_paths_are_checked(full_archive, tmp_path):
    original, _ = full_archive
    moved = tmp_path / "changed"
    shutil.copytree(original.parent, moved)
    path = moved / "engineering_run.json"
    source = moved / "search_report.json"
    source.write_bytes(source.read_bytes() + b"\n")
    checked = archive.verify_engineering_archive(path)
    assert not checked["source_report_hash_match"] and not checked["overall_match"]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cases"][0]["files"]["engineering"] = "../../outside.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    checked = archive.verify_engineering_archive(path)
    assert not checked["engineering_cases_match"]


def test_cli_invalid_manifest_shape_is_an_input_error(tmp_path):
    path = tmp_path / "engineering_run.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main(["--verify", str(path)])
    assert error.value.code == 2


@pytest.mark.parametrize("arguments", [[], ["--report", "source.json"],
    ["--verify", "archive.json", "--output-dir", "out"],
    ["--verify", "archive.json", "--select", "all"],
    ["--report", "source.json", "--output-dir", "out", "--select", "all", "--case-id", "id"]])
def test_cli_rejects_incompatible_or_missing_arguments(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
