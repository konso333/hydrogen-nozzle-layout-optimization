"""Engineering generation tests use synthetic, unapproved mounting dimensions."""

import csv
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from matplotlib.figure import Figure
from matplotlib.patches import Circle

from engineering_config import EngineeringGeometryConfig
from engineering_layout import generate_engineering_layout
from experiments.spec import CaseSpec
from geometry.constraints import LayoutConstraintError
from scripts import generate_engineering_layout as cli
from validation import InputValidationError


ROOT = Path(__file__).resolve().parents[1]
RECTANGULAR = {"spacing": 16, "rows": 4, "columns": 6}


@pytest.fixture
def profile():
    # These mounting dimensions are test inputs, not confirmed project inputs.
    return EngineeringGeometryConfig("SYNTHETIC TEST ONLY", 14, 60, 2, 3)


@pytest.mark.parametrize("layout,N,parameters", [
    ("rectangular", 24, RECTANGULAR),
    ("hexagonal", 24, {"spacing": 16, "row_counts": [4, 5, 6, 5, 4]}),
    ("ring", 12, {"ring_radii": [40], "points_per_ring": [12]}),
    ("sector", 6, {"num_sectors": 2, "points_per_sector": 3, "inner_radius": 30,
                   "outer_radius": 30, "sector_angle": 1.2}),
    ("rectangular", 1, {"spacing": 16}),
])
def test_generates_requested_count_with_real_clearances_and_reproducible_spec(profile, layout, N, parameters):
    points, report = generate_engineering_layout(config=profile, layout_type=layout, N=N, parameters=parameters)
    assert points.shape == (N, 2)
    assert all(report["validation"][key] for key in
               ("count_ok", "boundary_ok", "overlap_ok", "spacing_ok", "feasible"))
    assert report["configuration"] == profile.to_dict()
    assert report["validation_geometry"] == {"R": 57, "d": 14, "s_min": 16, "tolerance": 1e-9}
    clearances = report["measured_clearances_mm"]
    assert clearances["minimum_wall_clearance"] >= 3 - 1e-9
    if N > 1:
        assert clearances["minimum_nozzle_edge_gap"] >= 2 - 1e-9
    else:
        assert clearances["minimum_nozzle_edge_gap"] is None
    restored = CaseSpec.from_normalized(report["geometry_case_spec"])
    assert restored.case_id == report["geometry_case_id"]
    np.testing.assert_array_equal(points, restored.generate())


def test_same_effective_geometry_does_not_lose_distinct_physical_dimensions(profile):
    shifted = replace(profile, installation_radius_mm=65, wall_clearance_mm=8)
    first, original = generate_engineering_layout(config=profile, layout_type="rectangular", N=24,
                                                 parameters=RECTANGULAR)
    second, changed = generate_engineering_layout(config=shifted, layout_type="rectangular", N=24,
                                                 parameters=RECTANGULAR)
    np.testing.assert_array_equal(first, second)
    assert original["geometry_case_id"] == changed["geometry_case_id"]
    assert original["configuration"] != changed["configuration"]
    assert changed["measured_clearances_mm"]["minimum_wall_clearance"] == pytest.approx(
        original["measured_clearances_mm"]["minimum_wall_clearance"] + 5)


@pytest.mark.parametrize("field", ["installation_radius_mm", "nozzle_edge_gap_mm", "wall_clearance_mm"])
def test_pending_dimensions_block_generation(profile, field):
    with pytest.raises(InputValidationError, match=field):
        generate_engineering_layout(config=replace(profile, **{field: None}),
                                    layout_type="rectangular", N=24, parameters=RECTANGULAR)


@pytest.mark.parametrize("spacing,reason", [(15, "centre distance"), (13.5, "overlap"), (102, "boundary")])
def test_finite_geometry_failures_cannot_be_exported_as_valid(profile, spacing, reason):
    with pytest.raises(LayoutConstraintError, match=reason):
        generate_engineering_layout(config=profile, layout_type="rectangular", N=2,
                                    parameters={"rows": 1, "columns": 2, "spacing": spacing})


def write_inputs(tmp_path, profile, *, N=24, parameters=None):
    config_path = tmp_path / "engineering.json"
    config_path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
    parameters_path = tmp_path / "parameters.json"
    parameters_path.write_text(json.dumps(RECTANGULAR if parameters is None else parameters), encoding="utf-8")
    output = tmp_path / "new_run"
    args = ["--config", str(config_path), "--layout", "rectangular", "--N", str(N),
            "--parameters", str(parameters_path), "--output-dir", str(output)]
    return args, output


def test_cli_exports_matching_csv_png_snapshot_and_correct_plot_boundaries(profile, tmp_path, monkeypatch, capsys):
    args, output = write_inputs(tmp_path, profile)
    inputs_before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    captured = []
    savefig = Figure.savefig

    def capture(fig, *args, **kwargs):
        captured.extend((patch.get_label(), patch.radius) for patch in fig.axes[0].patches
                        if isinstance(patch, Circle))
        return savefig(fig, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    assert cli.main(args) == 0
    report = json.loads((output / "generation.json").read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert printed.pop("output_directory") == str(output.resolve())
    assert printed == report
    assert report["kind"] == "engineering_layout_preview"
    assert "git" in report["provenance"]
    assert set(path.name for path in output.iterdir()) == {"coordinates.csv", "layout.png", "generation.json"}
    assert (output / "layout.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with (output / "coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == ["nozzle_id", "x_mm", "y_mm", "z_mm"]
        rows = list(reader)
    assert [int(row["nozzle_id"]) for row in rows] == list(range(1, 25))
    assert all(float(row["z_mm"]) == 0 for row in rows)
    points = [(float(row["x_mm"]), float(row["y_mm"])) for row in rows]
    restored = CaseSpec.from_normalized(report["geometry_case_spec"])
    np.testing.assert_allclose(points, restored.generate(), rtol=0, atol=1e-12)
    assert captured[:3] == [("Installation boundary", 60),
                            ("Nozzle edge limit (wall clearance)", 57),
                            ("Allowed centre boundary", 50)]
    assert [radius for _, radius in captured[3:]] == [7] * 24
    assert all((tmp_path / name).read_bytes() == value for name, value in inputs_before.items())


def test_cli_blocks_current_project_template_before_reading_parameters_or_writing(tmp_path, capsys):
    output = tmp_path / "must_not_exist"
    with pytest.raises(SystemExit) as error:
        cli.main(["--config", str(ROOT / "examples/engineering_nozzle_14mm.json"),
                  "--layout", "rectangular", "--N", "24", "--parameters", str(tmp_path / "absent.json"),
                  "--output-dir", str(output)])
    assert error.value.code == 2
    assert "Unspecified engineering parameters" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("spacing", [15, 13.5, 102])
def test_cli_infeasible_request_returns_one_without_outputs(profile, tmp_path, capsys, spacing):
    args, output = write_inputs(tmp_path, profile, N=2,
                                parameters={"rows": 1, "columns": 2, "spacing": spacing})
    assert cli.main(args) == 1
    assert "infeasible" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("parameters", [{}, [], {"spacing": 16, "R": 55},
                                       {"spacing": 16, "rows": 3, "columns": 6},
                                       {"spacing": float("nan")}])
def test_cli_bad_parameters_return_two_without_outputs(profile, tmp_path, parameters):
    args, output = write_inputs(tmp_path, profile, parameters=parameters)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("directory", [True, False])
def test_cli_existing_output_is_never_overwritten(profile, tmp_path, directory, capsys):
    args, output = write_inputs(tmp_path, profile)
    if directory:
        output.mkdir()
        sentinel = output / "coordinates.csv"
    else:
        sentinel = output
    sentinel.write_bytes(b"existing saved data")
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert "already exists" in capsys.readouterr().err
    assert sentinel.read_bytes() == b"existing saved data"


def test_failed_png_export_has_no_completed_report(profile, tmp_path, monkeypatch):
    args, output = write_inputs(tmp_path, profile)

    def fail_save(*args, **kwargs):
        raise OSError("synthetic image write failure")

    monkeypatch.setattr(Figure, "savefig", fail_save)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert not (output / "generation.json").exists()
    assert cli.plt.get_fignums() == []
