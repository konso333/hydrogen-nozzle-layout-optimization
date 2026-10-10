"""Physical clearance checks using synthetic dimensions, not project decisions."""

import json
import math
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from engineering_config import EngineeringGeometryConfig
from engineering_geometry import to_validation_geometry, validate_engineering_layout
from layouts import generate_layout
from scripts.inspect_engineering_config import main
from validation import InputValidationError


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile():
    # R=60, edge gap=2, wall clearance=3 are test inputs only.
    return EngineeringGeometryConfig("synthetic clearance test", 14, 60, 2, 3)


def test_exact_clearances_pass_and_actual_installation_dimensions_remain_visible(profile):
    report = validate_engineering_layout([(34, 0), (50, 0)], config=profile, N=2)
    assert report["validation"]["feasible"] is True
    assert report["configuration"] == profile.to_dict()
    assert report["configuration"]["installation_radius_mm"] == 60
    assert report["validation_geometry"] == {"R": 57, "d": 14, "s_min": 16, "tolerance": 1e-9}
    assert report["measured_clearances_mm"] == {
        "minimum_nozzle_edge_gap": 2, "minimum_wall_clearance": 3,
        "nozzle_edge_gap_margin": 0, "wall_clearance_margin": 0,
    }


def test_nozzle_inside_actual_boundary_still_fails_requested_wall_clearance(profile):
    report = validate_engineering_layout([(51, 0)], config=profile, N=1)
    assert report["validation"]["boundary_ok"] is False
    assert report["validation"]["feasible"] is False
    assert report["measured_clearances_mm"]["minimum_wall_clearance"] == 2
    assert report["measured_clearances_mm"]["wall_clearance_margin"] == -1
    assert report["measured_clearances_mm"]["minimum_nozzle_edge_gap"] is None


@pytest.mark.parametrize("distance,overlap_ok,gap", [(15, True, 1), (13.5, False, -0.5)])
def test_physical_overlap_and_insufficient_assembly_gap_remain_distinct(profile, distance, overlap_ok, gap):
    report = validate_engineering_layout([(0, 0), (distance, 0)], config=profile, N=2)
    assert report["validation"]["overlap_ok"] is overlap_ok
    assert report["validation"]["spacing_ok"] is False
    assert report["validation"]["feasible"] is False
    assert report["measured_clearances_mm"]["minimum_nozzle_edge_gap"] == gap


def test_explicit_zero_gap_allows_touching_without_inflating_nozzle_diameter():
    profile = EngineeringGeometryConfig("touching test", 14, 21, 0, 0)
    report = validate_engineering_layout([(-14, 0), (0, 0), (14, 0)], config=profile, N=3)
    assert report["validation"]["feasible"] is True
    assert report["measured_clearances_mm"]["minimum_nozzle_edge_gap"] == 0
    assert report["measured_clearances_mm"]["minimum_wall_clearance"] == 0


@pytest.mark.parametrize("tolerance,passes", [(1e-9, True), (0, False)])
def test_raw_margin_does_not_hide_numerical_tolerance(profile, tolerance, passes):
    report = validate_engineering_layout([(50 + 5e-10, 0)], config=profile, N=1, tolerance=tolerance)
    assert report["validation"]["feasible"] is passes
    assert report["validation_geometry"]["tolerance"] == tolerance
    assert report["measured_clearances_mm"]["wall_clearance_margin"] < 0


@pytest.mark.parametrize("field", ["installation_radius_mm", "nozzle_edge_gap_mm", "wall_clearance_mm"])
def test_any_unspecified_parameter_blocks_the_adapter_and_layout_validation(profile, field):
    incomplete = replace(profile, **{field: None})
    with pytest.raises(InputValidationError, match=field):
        to_validation_geometry(incomplete)
    with pytest.raises(InputValidationError, match=field):
        validate_engineering_layout([(0, 0)], config=incomplete, N=1)


@pytest.mark.parametrize("count", [0, None, True, 2.0])
def test_engineering_check_requires_an_explicit_positive_integral_count(profile, count):
    with pytest.raises(InputValidationError, match="N"):
        validate_engineering_layout([(0, 0)], config=profile, N=count)


def test_missing_nozzles_cannot_pass_and_empty_clearances_are_undefined(profile):
    report = validate_engineering_layout([], config=profile, N=1)
    assert report["validation"]["count_ok"] is False
    assert report["validation"]["feasible"] is False
    assert all(value is None for value in report["measured_clearances_mm"].values())


def test_adapter_reuses_existing_generator_for_a_24_nozzle_layout(profile):
    geometry = to_validation_geometry(profile)
    points = generate_layout("rectangular", 24, **asdict(geometry), spacing=16, rows=4, columns=6)
    report = validate_engineering_layout(points, config=profile, N=24)
    assert report["validation"]["feasible"] is True
    assert report["measured_clearances_mm"]["minimum_nozzle_edge_gap"] == pytest.approx(2)
    assert report["measured_clearances_mm"]["minimum_wall_clearance"] >= 3


def test_nonfinite_coordinates_are_rejected(profile):
    with pytest.raises(ValueError, match="finite"):
        validate_engineering_layout([(math.nan, 0)], config=profile, N=1)


def write_inputs(tmp_path, profile, coordinates):
    config_path = tmp_path / "engineering.json"
    config_path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
    csv_path = tmp_path / "coordinates.csv"
    csv_path.write_text(coordinates, encoding="utf-8-sig")
    return ["--config", str(config_path), "--coordinates", str(csv_path), "--N", "2"]


@pytest.mark.parametrize("header,rows", [
    ("x_mm,y_mm", "34,0\n{last},0\n"),
    ("nozzle_id,x_mm,y_mm,z_mm", "1,34,0,0\n2,{last},0,0\n"),
])
@pytest.mark.parametrize("last,exit_code", [(50, 0), (51, 1)])
def test_cli_checks_existing_csv_formats_without_modifying_files(profile, tmp_path, capsys, header, rows, last, exit_code):
    args = write_inputs(tmp_path, profile, header + "\n" + rows.format(last=last))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert main(args) == exit_code
    report = json.loads(capsys.readouterr().out)
    assert report["validation"]["feasible"] is (exit_code == 0)
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_cli_rejects_pending_profile_before_reading_coordinates(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["--config", str(ROOT / "examples/engineering_nozzle_14mm.json"),
              "--coordinates", str(tmp_path / "does_not_exist.csv"), "--N", "24"])
    assert error.value.code == 2
    assert "Unspecified engineering parameters" in capsys.readouterr().err


@pytest.mark.parametrize("csv_text", [
    "nozzle_id,x_mm,y_mm,z_mm\n1,0,0,1\n2,16,0,0\n",
    "x_mm,y_mm\n0,0,extra\n16,0\n",
    "x_mm,y_mm\n0\n16,0\n",
    "x,y\n0,0\n16,0\n",
])
def test_cli_rejects_nonplanar_or_malformed_coordinates(profile, tmp_path, capsys, csv_text):
    with pytest.raises(SystemExit) as error:
        main(write_inputs(tmp_path, profile, csv_text))
    assert error.value.code == 2
    assert capsys.readouterr().err


@pytest.mark.parametrize("arguments", [["--N", "2"], ["--coordinates", "unused.csv"]])
def test_cli_requires_coordinates_and_expected_count_together(arguments):
    with pytest.raises(SystemExit) as error:
        main(["--config", str(ROOT / "examples/engineering_nozzle_14mm.json"), *arguments])
    assert error.value.code == 2
