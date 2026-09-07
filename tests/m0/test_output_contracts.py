"""Exercise real CSV/PNG exports and script wiring without pixel snapshots."""

from __future__ import annotations

import codecs
import csv
import json

import matplotlib.image as mpimg
import numpy as np
import pytest

import config
from io_utils.export_csv import export_coordinates
from scripts import compare_layouts, generate_baselines, search_variable_n


BASELINES = ("A_Rectangular", "B_Hexagonal", "C_Double_Ring", "D_Triple_Ring")
COMPARISONS = BASELINES + (
    "E_Sector_4x6", "F_Radial_8x3", "G_Staggered_Ring_8_16",
    "H_Nonuniform_3_Ring", "I_Deterministic_Irregular",
)
LEGACY_HEADER = [
    "layout", "nozzle_count", "count_ok", "min_center_distance_mm",
    "mean_nearest_neighbour_mm", "max_center_radius_mm", "boundary_ok",
    "min_spacing_ok", "x_axis_symmetric", "y_axis_symmetric", "origin_symmetric", "overall_ok",
]
LEGACY_METRIC_MAP = {
    "nozzle_count": "nozzle_count", "count_ok": "count_ok",
    "min_center_distance_mm": "min_center_distance",
    "mean_nearest_neighbour_mm": "mean_nearest_neighbor_distance",
    "max_center_radius_mm": "max_center_radius", "boundary_ok": "boundary_ok",
    "min_spacing_ok": "spacing_ok", "x_axis_symmetric": "x_axis_symmetry",
    "y_axis_symmetric": "y_axis_symmetry", "origin_symmetric": "origin_symmetry",
    "overall_ok": "feasible",
}


def _read_csv(path, header):
    assert path.is_file()
    assert path.read_bytes().startswith(codecs.BOM_UTF8), f"Missing UTF-8 BOM: {path}"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == header  # Also excludes an accidental index column.
        rows = list(reader)
    assert all(None not in row and None not in row.values() for row in rows)
    return rows


def _assert_coordinate_csv(path, expected, *, legacy=False):
    header = ["x_mm", "y_mm"] if legacy else ["nozzle_id", "x_mm", "y_mm", "z_mm"]
    rows = _read_csv(path, header)
    assert len(rows) == len(expected)
    xy = [(float(row["x_mm"]), float(row["y_mm"])) for row in rows]
    np.testing.assert_allclose(xy, expected, rtol=0, atol=1e-9)
    if not legacy:
        assert [row["nozzle_id"] for row in rows] == [str(i) for i in range(1, len(expected) + 1)]
        np.testing.assert_allclose([float(row["z_mm"]) for row in rows], 0, rtol=0, atol=1e-12)
    return xy


def _assert_png(path):
    assert path.is_file()
    assert path.stat().st_size > 8
    with path.open("rb") as stream:
        assert stream.read(8) == b"\x89PNG\r\n\x1a\n"
    decoded = mpimg.imread(path)
    assert decoded.ndim == 3 and decoded.shape[2] in (3, 4)
    height, width = decoded.shape[:2]
    assert 500 <= height <= 6000 and 500 <= width <= 6000
    assert np.isfinite(decoded).all()
    return height, width


def _assert_layout_png(run, relative_path, csv_points):
    _assert_png(run["root"] / relative_path)
    axes = run["figures"][relative_path]
    assert len(axes) == 1
    labels = axes[0]["labels"]
    assert [label for label, _ in labels] == [str(i) for i in range(1, len(csv_points) + 1)]
    np.testing.assert_allclose([position for _, position in labels], csv_points, rtol=0, atol=1e-9)
    # Verify labels sit on their nozzle circles, not only on the supplied points.
    circles = axes[0]["circles"]
    assert len(circles) == len(csv_points) + 2
    np.testing.assert_allclose([center for center, _ in circles], [(0, 0), (0, 0), *csv_points],
                               rtol=0, atol=1e-9)
    np.testing.assert_allclose([radius for _, radius in circles], [55, 53, *([2] * len(csv_points))],
                               rtol=0, atol=1e-9)


def _assert_csv_value(value, expected):
    if isinstance(expected, bool):
        assert value == str(expected)
    elif isinstance(expected, list):
        assert json.loads(value) == expected
    elif isinstance(expected, int):
        assert int(value) == expected
    else:
        assert float(value) == pytest.approx(expected, rel=1e-12, abs=1e-9)


@pytest.mark.parametrize("legacy", [True, False], ids=["baseline-xy", "comparison-search-xyz"])
def test_coordinate_export_schema_bom_ids_and_unsorted_order(tmp_path, legacy):
    points = [(3.25, -4.5), (-10, 12), (0, 0), (3.25, 4.5)]
    target = tmp_path / "nested" / "coordinates.csv"
    actual_path = export_coordinates(points, target, legacy_xy_only=legacy)
    assert actual_path == target
    _assert_coordinate_csv(target, points, legacy=legacy)


@pytest.mark.parametrize("name", BASELINES)
def test_baseline_script_csv_png_and_numbering(name, baseline_run, reference_cases):
    expected = reference_cases[name]["points"]
    xy = _assert_coordinate_csv(baseline_run["root"] / f"coordinates/{name}_coordinates.csv",
                                expected, legacy=True)
    _assert_layout_png(baseline_run, f"figures/{name}.png", xy)


@pytest.mark.parametrize("name", COMPARISONS)
def test_comparison_script_csv_png_and_numbering(name, comparison_run, reference_cases):
    expected = reference_cases[name]["points"]
    xy = _assert_coordinate_csv(comparison_run["root"] / f"coordinates/comparison_{name}_coordinates.csv",
                                expected)
    _assert_layout_png(comparison_run, f"figures/comparison_{name}.png", xy)


def test_legacy_layout_summary_schema_order_values_and_bom(baseline_run, reference_cases):
    rows = _read_csv(baseline_run["root"] / "summaries/layout_summary.csv", LEGACY_HEADER)
    assert [row["layout"] for row in rows] == list(BASELINES)
    for row in rows:
        expected = reference_cases[row["layout"]]["metrics"]
        for old_name, metric_name in LEGACY_METRIC_MAP.items():
            _assert_csv_value(row[old_name], expected[metric_name])


def test_modern_baseline_summary_coexists_with_legacy(baseline_run, reference_cases):
    header = ["layout_type", "N", "layout_parameters", *reference_cases[BASELINES[0]]["metrics"]]
    rows = _read_csv(baseline_run["root"] / "summaries/baseline_layout_summary.csv", header)
    assert [row["layout_type"] for row in rows] == list(BASELINES)
    expected_parameters = [
        {"rows": 4, "columns": 6, "spacing": 18},
        {"row_counts": [4, 5, 6, 5, 4], "spacing": 16},
        {"ring_radii": [25, 48], "points_per_ring": [8, 16]},
        {"ring_radii": [25, 48], "points_per_ring": [7, 16], "include_center": True},
    ]
    for row, parameters in zip(rows, expected_parameters):
        assert int(row["N"]) == 24
        actual_parameters = json.loads(row["layout_parameters"])
        assert actual_parameters.keys() == parameters.keys()
        for key, value in parameters.items():
            assert actual_parameters[key] == pytest.approx(value, rel=1e-12, abs=1e-12)
        for key, value in reference_cases[row["layout_type"]]["metrics"].items():
            _assert_csv_value(row[key], value)


def test_comparison_summary_nine_rows_parameters_and_metrics(comparison_run, reference_cases):
    header = ["layout_name", "layout_type", "N", "layout_parameters",
              *reference_cases[BASELINES[0]]["metrics"]]
    rows = _read_csv(comparison_run["root"] / "summaries/layout_comparison.csv", header)
    assert [row["layout_name"] for row in rows] == list(COMPARISONS)
    for row in rows:
        case = reference_cases[row["layout_name"]]
        assert int(row["N"]) == 24
        assert row["layout_type"] == case["layout_type"]
        parameters = json.loads(row["layout_parameters"])
        assert parameters.keys() == case["parameters"].keys()
        for key, value in case["parameters"].items():
            assert parameters[key] == pytest.approx(value, rel=1e-12, abs=1e-12)
        for key, value in case["metrics"].items():
            _assert_csv_value(row[key], value)


def test_search_exports_all_coordinate_csvs_and_summaries(search_run, reference):
    candidates, marked, pareto = search_run["search"]
    root = search_run["root"]
    files = sorted((root / "coordinates/variable_n").glob("*.csv"))
    assert len(files) == 415
    assert {path.stem for path in files} == {candidate.candidate_id for candidate in candidates}
    for candidate in candidates:
        _assert_coordinate_csv(root / f"coordinates/variable_n/{candidate.candidate_id}.csv", candidate.points)
    header = ["candidate_id", "N", "layout_type", "layout_parameters",
              *reference["cases"][0]["metrics"], "pareto_candidate"]
    for filename, expected_rows in (("variable_n_results.csv", marked), ("pareto_candidates.csv", pareto)):
        rows = _read_csv(root / "summaries" / filename, header)
        assert [row["candidate_id"] for row in rows] == [row["candidate_id"] for row in expected_rows]
        for row, expected in zip(rows, expected_rows):
            assert row["layout_type"] == expected["layout_type"]
            parameters = json.loads(row["layout_parameters"])
            expected_parameters = json.loads(expected["layout_parameters"])
            assert parameters.keys() == expected_parameters.keys()
            for key, value in expected_parameters.items():
                assert parameters[key] == pytest.approx(value, rel=1e-12, abs=1e-12)
            for key in ("N", "pareto_candidate", *reference["cases"][0]["metrics"]):
                _assert_csv_value(row[key], expected[key])


def test_search_default_representative_pngs_and_numbering(search_run, reference):
    # Independently frozen selection of 12 evenly spread ranks among 18 sorted IDs.
    ids = sorted(reference["search"]["pareto_ids"])
    selected = [ids[i] for i in (0, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17)]
    directory = search_run["root"] / "figures/variable_n"
    assert {path.stem for path in directory.glob("*.png")} == {*selected, "pareto_tradeoffs"}
    by_id = {candidate.candidate_id: candidate for candidate in search_run["search"][0]}
    for candidate_id in selected:
        candidate = by_id[candidate_id]
        xy = _assert_coordinate_csv(search_run["root"] / f"coordinates/variable_n/{candidate_id}.csv",
                                    candidate.points)
        _assert_layout_png(search_run, f"figures/variable_n/{candidate_id}.png", xy)
    height, width = _assert_png(directory / "pareto_tradeoffs.png")
    assert width > height


def test_default_output_roots_remain_compatible():
    assert config.OUTPUT_ROOT == config.PROJECT_ROOT / "outputs"
    for module in (config, generate_baselines, compare_layouts, search_variable_n):
        assert module.COORDINATES_DIR == config.PROJECT_ROOT / "outputs/coordinates"
        assert module.FIGURES_DIR == config.PROJECT_ROOT / "outputs/figures"
        assert module.SUMMARIES_DIR == config.PROJECT_ROOT / "outputs/summaries"
