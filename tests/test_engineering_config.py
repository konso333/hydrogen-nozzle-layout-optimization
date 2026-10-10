"""Engineering inputs preserve unknown dimensions and physical edge clearances."""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_config import EngineeringGeometryConfig
from experiments.search_space import ExperimentSearchSpace
from validation import InputValidationError


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/engineering_nozzle_14mm.json"


def test_latest_nozzle_template_keeps_all_unconfirmed_inputs_unknown():
    profile = EngineeringGeometryConfig.load(EXAMPLE)
    report = profile.inspect()
    assert profile.nozzle_outer_diameter_mm == 14
    assert report["parameters_complete"] is False
    assert set(report["missing_parameters"]) == {
        "installation_radius_mm", "nozzle_edge_gap_mm", "wall_clearance_mm",
    }
    assert report["minimum_center_distance_mm"] is None
    assert report["maximum_center_radius_mm"] is None
    with pytest.raises(InputValidationError, match="Unspecified engineering parameters"):
        profile.require_complete()
    assert EngineeringGeometryConfig.from_dict(profile.to_dict()) == profile


def test_edge_clearances_are_not_center_distances_or_diameter_inflation():
    # Synthetic test dimensions, not confirmed project installation requirements.
    profile = EngineeringGeometryConfig("test", 14, 60, 2, 3)
    profile.require_complete()
    report = profile.inspect()
    assert report["minimum_center_distance_mm"] == 16
    assert report["maximum_center_radius_mm"] == 50
    assert profile.to_dict()["nozzle_outer_diameter_mm"] == 14


def test_explicit_zero_clearances_differ_from_unspecified_clearances():
    profile = EngineeringGeometryConfig("test", 14, 7, 0, 0)
    assert profile.inspect()["parameters_complete"] is True
    assert profile.inspect()["minimum_center_distance_mm"] == 14
    assert profile.inspect()["maximum_center_radius_mm"] == 0


@pytest.mark.parametrize("field,value", [
    ("nozzle_outer_diameter_mm", 0),
    ("nozzle_outer_diameter_mm", None),
    ("installation_radius_mm", 0),
    ("installation_radius_mm", 6),
    ("nozzle_edge_gap_mm", -1),
    ("wall_clearance_mm", -1),
    ("wall_clearance_mm", 54),
])
def test_invalid_sizes_cannot_be_used_even_if_other_inputs_are_pending(field, value):
    data = EngineeringGeometryConfig("test", 14, 60).to_dict()
    data[field] = value
    with pytest.raises(InputValidationError):
        EngineeringGeometryConfig.from_dict(data)


@pytest.mark.parametrize("field", [
    "nozzle_outer_diameter_mm", "installation_radius_mm",
    "nozzle_edge_gap_mm", "wall_clearance_mm",
])
@pytest.mark.parametrize("value", [True, "14", math.nan, math.inf])
def test_invalid_numeric_inputs_cannot_silently_supply_dimensions(field, value):
    data = EngineeringGeometryConfig.load(EXAMPLE).to_dict()
    data[field] = value
    with pytest.raises(InputValidationError, match=field):
        EngineeringGeometryConfig.from_dict(data)


@pytest.mark.parametrize("mutation", [
    lambda data: data.pop("wall_clearance_mm"),
    lambda data: data.update(s_min=8),
    lambda data: data.update(schema_version=True),
    lambda data: data.update(schema_version=1.0),
    lambda data: data.update(name=""),
])
def test_malformed_configuration_does_not_fall_back_to_legacy_defaults(mutation):
    data = EngineeringGeometryConfig.load(EXAMPLE).to_dict()
    mutation(data)
    with pytest.raises(InputValidationError):
        EngineeringGeometryConfig.from_dict(data)


def test_engineering_profile_is_not_an_executable_m3_search_design():
    with pytest.raises(InputValidationError):
        ExperimentSearchSpace.load(EXAMPLE)


@pytest.mark.parametrize("strict,exit_code", [(False, 0), (True, 2)])
def test_cli_reads_template_from_another_directory_without_writing_files(tmp_path, strict, exit_code):
    command = [sys.executable, str(ROOT / "scripts/inspect_engineering_config.py"),
               "--config", str(EXAMPLE)]
    if strict:
        command.append("--require-complete")
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == exit_code, result.stderr
    assert json.loads(result.stdout)["parameters_complete"] is False
    assert list(tmp_path.iterdir()) == []
