"""Engineering N search acceptance tests with synthetic mounting dimensions."""

import copy
import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from engineering_config import EngineeringGeometryConfig
from engineering_geometry import validate_engineering_layout
from engineering_search import make_engineering_search_space, run_engineering_search
from experiments.spec import CaseSpec
from scripts import search_engineering_layouts as cli
from validation import InputValidationError


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile():
    # Installation radius and clearances are test inputs, not project decisions.
    return EngineeringGeometryConfig("SYNTHETIC SEARCH TEST ONLY", 14, 60, 2, 3)


def design():
    return {
        "schema_version": 1, "name": "Synthetic variable-N acceptance search",
        "groups": [{"N": [1, 2, 4, 24], "layout_type": "rectangular",
                    "parameter_sets": [{"spacing": 16}, {"spacing": 15}, {"spacing": 102}]}],
    }


def test_engineering_design_preflight_uses_one_effective_geometry_without_generation(profile, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("planning must not generate coordinates")

    monkeypatch.setattr(CaseSpec, "generate", forbidden)
    space = make_engineering_search_space(config=profile, design=design())
    plan = space.plan()
    assert (plan.planned, plan.unique, plan.duplicates) == (12, 12, 0)
    assert space.to_dict()["blocks"][0]["geometry_space"] == {
        "R": [57], "d": [14], "s_min": [16], "tolerance": [1e-9],
    }
    assert {request.spec.normalized_spec["N"] for request in plan.requests} == {1, 2, 4, 24}


def test_variable_n_results_clearances_failures_and_pareto_are_reproducible(profile):
    report = run_engineering_search(config=profile, design=design())
    assert (report["planned"], report["unique"], report["duplicates"], report["feasible"],
            report["infeasible"], report["pareto_count"]) == (12, 12, 0, 6, 6, 1)
    assert report["largest_feasible_N"] == 24
    assert report["search_scope"] == "provided_N_and_parameter_sets_only"
    assert report["case_id_scope"] == "effective_geometry_specification_only"
    assert report["configuration"] == profile.to_dict()
    assert report["validation_geometry"] == {"R": 57, "d": 14, "s_min": 16, "tolerance": 1e-9}
    assert report["by_N"] == [
        {"N": 1, "unique": 3, "feasible": 3, "infeasible": 0, "pareto_count": 0},
        {"N": 2, "unique": 3, "feasible": 1, "infeasible": 2, "pareto_count": 0},
        {"N": 4, "unique": 3, "feasible": 1, "infeasible": 2, "pareto_count": 0},
        {"N": 24, "unique": 3, "feasible": 1, "infeasible": 2, "pareto_count": 1},
    ]
    terms = report["objective_profile"]["objectives"]
    assert [(term["key"], term["direction"]) for term in terms] == [
        ("N", "maximize"), ("uniformity_score", "maximize"), ("min_center_distance", "maximize")]
    assert report["pareto_scope"] == "all_unique_feasible_cases"
    assert all("reason" in failure for failure in report["infeasible_cases"])
    assert report["archive_status"] == "not_requested" and report["run_id"] is None
    specs = {CaseSpec.from_normalized(value).case_id: value for value in report["specifications"]}
    for row in report["rows"]:
        spec = CaseSpec.from_normalized(specs[row["case_id"]])
        check = validate_engineering_layout(spec.generate(), config=profile, N=row["N"])
        assert check["validation"]["feasible"]
        measured = check["measured_clearances_mm"]
        assert row["minimum_wall_clearance_mm"] == pytest.approx(measured["minimum_wall_clearance"])
        if row["N"] == 1:
            assert row["minimum_nozzle_edge_gap_mm"] is None
            assert row["pareto_candidate"] is False
        else:
            assert row["minimum_nozzle_edge_gap_mm"] == pytest.approx(measured["minimum_nozzle_edge_gap"])
            assert row["minimum_spacing_margin"] == pytest.approx(measured["nozzle_edge_gap_margin"])
        assert row["minimum_boundary_margin"] == pytest.approx(measured["wall_clearance_margin"])
    assert run_engineering_search(config=profile, design=design()) == report


@pytest.mark.parametrize("counts,expected", [(2, [2]), ([4, 2], [2, 4]),
    ({"start": 2, "stop": 6, "step": 2}, [2, 4, 6])])
def test_explicit_count_forms(profile, counts, expected):
    value = design()
    value["groups"][0].update(N=counts, parameter_sets=[{"spacing": 16}])
    report = run_engineering_search(config=profile, design=value)
    assert [row["N"] for row in report["by_N"]] == expected
    assert report["feasible"] == len(expected)


def test_duplicate_requests_are_counted_once_with_all_sources_preserved(profile):
    value = design()
    value["groups"].append(copy.deepcopy(value["groups"][0]))
    report = run_engineering_search(config=profile, design=value)
    assert (report["planned"], report["unique"], report["duplicates"], report["feasible"]) == (24, 12, 12, 6)
    assert all(len(paths) == 2 for paths in report["sources"].values())
    assert sum(row["unique"] for row in report["by_N"]) == 12


def test_same_effective_geometry_keeps_actual_installation_dimensions(profile):
    shifted = replace(profile, installation_radius_mm=65, wall_clearance_mm=8)
    first = run_engineering_search(config=profile, design=design())
    second = run_engineering_search(config=shifted, design=design())
    assert first["case_ids"] == second["case_ids"]
    assert first["configuration"] != second["configuration"]
    assert first["by_N"] == second["by_N"]
    for a, b in zip(first["rows"], second["rows"]):
        assert b["minimum_wall_clearance_mm"] == pytest.approx(a["minimum_wall_clearance_mm"] + 5)


@pytest.mark.parametrize("field", ["installation_radius_mm", "nozzle_edge_gap_mm", "wall_clearance_mm"])
def test_pending_engineering_dimensions_block_both_plan_and_search(profile, monkeypatch, field):
    monkeypatch.setattr(CaseSpec, "generate", lambda *_: pytest.fail("must not generate"))
    incomplete = replace(profile, **{field: None})
    for entry in (make_engineering_search_space, run_engineering_search):
        with pytest.raises(InputValidationError, match=field):
            entry(config=incomplete, design=design())


@pytest.mark.parametrize("extra", ["geometry_space", "blocks", "N", "tolerance", "nozzle_outer_diameter_mm"])
def test_design_cannot_override_engineering_geometry_or_accept_unknown_fields(profile, extra):
    value = design()
    value[extra] = {}
    with pytest.raises(InputValidationError, match="Geometry belongs"):
        make_engineering_search_space(config=profile, design=value)


@pytest.mark.parametrize("bad_group", [
    {"N": True, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]},
    {"N": 4.0, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]},
    {"N": {"start": 2, "stop": 4, "step": 0}, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]},
    {"N": 4, "layout_type": "typo", "parameter_sets": [{"spacing": 16}]},
    {"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16, "R": 55}]},
    {"N": 4, "layout_type": "rectangular", "parameter_sets": [{}]},
    {"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16, "rows": 3, "columns": 2}]},
    {"N": [4, 6], "layout_type": "ring", "parameter_sets": [{"ring_radii": [40], "points_per_ring": [4]}]},
    {"N": 4, "layout_type": "hexagonal", "parameter_sets": [{"spacing": float("inf")}]},
])
def test_all_groups_are_preflighted_before_any_generation(profile, monkeypatch, bad_group):
    monkeypatch.setattr(CaseSpec, "generate", lambda *_: pytest.fail("must preflight before generation"))
    value = design()
    value["groups"].append(bad_group)
    with pytest.raises((InputValidationError, ValueError)):
        run_engineering_search(config=profile, design=value)


@pytest.mark.parametrize("error", [TypeError("runtime bug"), RuntimeError("runtime bug"), ValueError("runtime bug")])
def test_runtime_errors_are_not_silently_classified_as_infeasible(profile, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(CaseSpec, "generate", fail)
    with pytest.raises(type(error)) as caught:
        run_engineering_search(config=profile, design=design())
    assert caught.value is error


def write_inputs(tmp_path, profile, value=None):
    config_path = tmp_path / "engineering.json"
    config_path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
    search_path = tmp_path / "search.json"
    search_path.write_text(json.dumps(design() if value is None else value), encoding="utf-8")
    output = tmp_path / "new_search"
    return ["--config", str(config_path), "--search", str(search_path), "--output-dir", str(output)], output


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def test_cli_exports_reproducible_report_physical_csv_values_and_png(profile, tmp_path, capsys):
    args, output = write_inputs(tmp_path, profile)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert cli.main(args) == 0
    report = json.loads((output / "search_report.json").read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["feasible"] == report["feasible"] == 6
    assert printed["output_directory"] == str(output.resolve())
    assert report["kind"] == "engineering_layout_search"
    assert "git" in report["provenance"]
    assert (output / "pareto_tradeoffs.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    headers, rows = read_csv(output / "feasible_candidates.csv")
    assert headers == list(cli.SUMMARY_COLUMNS)
    assert len(rows) == report["feasible"]
    assert [row["case_id"] for row in rows] == [row["case_id"] for row in report["rows"]]
    for row in rows:
        assert float(row["nozzle_outer_diameter_mm"]) == 14
        assert float(row["installation_radius_mm"]) == 60
        assert float(row["nozzle_edge_gap_mm"]) == 2
        assert float(row["wall_clearance_mm"]) == 3
        assert float(row["minimum_wall_clearance_mm"]) >= 3 - 1e-9
        if int(row["N"]) == 1:
            assert row["minimum_nozzle_edge_gap_mm"] == ""
        else:
            assert float(row["minimum_nozzle_edge_gap_mm"]) >= 2 - 1e-9
    _, pareto = read_csv(output / "pareto_candidates.csv")
    assert len(pareto) == report["pareto_count"] == 1 and int(pareto[0]["N"]) == 24
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())


def test_zero_feasible_search_keeps_diagnostics_and_empty_csv_headers(profile, tmp_path, capsys):
    value = design()
    value["groups"] = [{"N": 2, "layout_type": "rectangular",
                        "parameter_sets": [{"spacing": 102, "rows": 1, "columns": 2}]}]
    args, output = write_inputs(tmp_path, profile, value)
    assert cli.main(args) == 1
    report = json.loads((output / "search_report.json").read_text(encoding="utf-8"))
    assert report["feasible"] == 0 and report["infeasible"] == 1
    assert report["largest_feasible_N"] is None
    assert "boundary" in report["infeasible_cases"][0]["reason"]
    assert report["pareto_count"] == 0
    for name in ("feasible_candidates.csv", "pareto_candidates.csv"):
        headers, rows = read_csv(output / name)
        assert headers == list(cli.SUMMARY_COLUMNS) and not rows
    assert not (output / "pareto_tradeoffs.png").exists()
    assert json.loads(capsys.readouterr().out)["largest_feasible_N"] is None


def test_single_nozzle_has_no_fabricated_objectives_or_pareto_plot(profile, tmp_path, capsys):
    value = design()
    value["groups"][0].update(N=1, parameter_sets=[{"spacing": 16}])
    args, output = write_inputs(tmp_path, profile, value)
    assert cli.main(args) == 0
    report = json.loads((output / "search_report.json").read_text(encoding="utf-8"))
    assert report["feasible"] == 1 and report["pareto_count"] == 0
    assert report["rows"][0]["uniformity_score"] is None
    assert not (output / "pareto_tradeoffs.png").exists()
    assert read_csv(output / "pareto_candidates.csv")[1] == []
    capsys.readouterr()


def test_cli_blocks_current_project_template_before_search_read_or_output_creation(tmp_path, capsys):
    output = tmp_path / "must_not_exist"
    with pytest.raises(SystemExit) as error:
        cli.main(["--config", str(ROOT / "examples/engineering_nozzle_14mm.json"),
                  "--search", str(tmp_path / "absent.json"), "--output-dir", str(output)])
    assert error.value.code == 2
    assert "Unspecified engineering parameters" in capsys.readouterr().err
    assert not output.exists()


def test_cli_bad_group_creates_no_output(profile, tmp_path):
    value = design()
    value["groups"][0]["N"] = False
    args, output = write_inputs(tmp_path, profile, value)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("directory", [True, False])
def test_cli_never_overwrites_existing_results(profile, tmp_path, directory, capsys):
    args, output = write_inputs(tmp_path, profile)
    if directory:
        output.mkdir()
        sentinel = output / "search_report.json"
    else:
        sentinel = output
    sentinel.write_bytes(b"saved earlier")
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert "already exists" in capsys.readouterr().err
    assert sentinel.read_bytes() == b"saved earlier"


def test_failed_export_cannot_leave_a_completed_search_report(profile, tmp_path, monkeypatch):
    args, output = write_inputs(tmp_path, profile)

    def fail(*args, **kwargs):
        raise OSError("synthetic plot write failure")

    monkeypatch.setattr(cli, "plot_pareto_tradeoffs", fail)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert not (output / "search_report.json").exists()


def test_checked_in_example_is_an_explicit_geometry_only_design(profile):
    value = json.loads((ROOT / "examples/engineering_search_groups.json").read_text(encoding="utf-8"))
    report = run_engineering_search(config=profile, design=value)
    assert report["planned"] == report["unique"] == 34
    assert report["feasible"] > 0 and report["infeasible"] > 0
    assert {spec["layout_type"] for spec in report["specifications"]} == {"rectangular", "hexagonal", "sector"}
    assert report["feasible"] + report["infeasible"] == report["unique"]
