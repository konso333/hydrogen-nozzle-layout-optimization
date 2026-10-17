"""Analytic containment, search objectives, replay and legacy-isolation checks."""

import copy
import csv
import hashlib
import json
import math
from pathlib import Path

import pytest

from layouts import generate_layout
from shape_boundary import MountingBoundary, validate_mounting_layout
from shape_search import normalize_design, run_shape_search
from shape_export import export_shape_search, verify_shape_search
from scripts.search_shape_layouts import main


def design(mode="fixed_space", kind="circle"):
    return {"schema_version": 1, "name": "SYNTHETIC TEST ONLY", "mode": mode,
        "boundary": {"kind": kind, "radius_mm": 60 if mode == "fixed_space" else None,
                     "start_angle_deg": 0, "sweep_angle_deg": 360 if kind == "circle" else 120},
        "nozzle": {"outer_diameter_mm": 14, "edge_gap_mm": 2, "wall_clearance_mm": 3},
        "counts": [1, 4, 10, 19], "radius_candidates_mm": [] if mode == "fixed_space" else [40, 60, 80],
        "layouts": [{"family": "honeycomb", "parameters": {"spacing_mm": 16}},
                    {"family": "sunflower", "parameters": {"pool_multiplier": 1 if kind == "circle" else 4}},
                    {"family": "rectangular", "parameters": {"spacing_mm": 16}},
                    {"family": "ring", "parameters": {"pool_multiplier": 1 if kind == "circle" else 4}}],
        "power_model": {"per_nozzle_power_kW": 2, "basis": "fuel_input", "source": "synthetic:test",
                        "assumptions": "SYNTHETIC ONLY; identical independent nozzle rating"},
        "target_power_kW": None if mode == "fixed_space" else 20, "tolerance_mm": 1e-9}


def test_circle_exact_outer_edge_and_wall_margin():
    boundary = MountingBoundary("circle", 55)
    kw = dict(boundary=boundary, N=1, outer_diameter_mm=14, edge_gap_mm=2, wall_clearance_mm=3)
    assert validate_mounting_layout([(45, 0)], **kw)["feasible"]
    bad = validate_mounting_layout([(45.01, 0)], **kw)
    assert not bad["boundary_ok"] and bad["minimum_wall_clearance_mm"] == pytest.approx(2.99)
    assert boundary.area_mm2 == pytest.approx(math.pi * 55**2)


def test_sector_checks_full_nozzle_against_radial_edges_not_only_angle():
    boundary = MountingBoundary("sector", 60, 0, 90)
    kw = dict(boundary=boundary, N=1, outer_diameter_mm=14, edge_gap_mm=2, wall_clearance_mm=3)
    assert validate_mounting_layout([(10, 10)], **kw)["feasible"]
    for point in [(5, 20), (20, 5), (0, 0), (-10, 10), (40, 40)]:
        assert not validate_mounting_layout([point], **kw)["feasible"]
    assert boundary.signed_distance_mm((10, 10)) == pytest.approx(10)
    assert boundary.area_mm2 == pytest.approx(math.pi * 60**2 / 4)


def test_reflex_and_wrapped_sectors_use_finite_segments():
    reflex = MountingBoundary("sector", 60, 0, 270)
    assert reflex.signed_distance_mm((-10, 0)) == pytest.approx(10)
    assert reflex.signed_distance_mm((10, -10)) < 0
    wrapped = MountingBoundary("sector", 60, 330, 60)
    assert wrapped.signed_distance_mm((20, 0)) == pytest.approx(10)
    assert wrapped.signed_distance_mm((-20, 0)) < 0
    kw = dict(boundary=reflex, N=1, outer_diameter_mm=4, edge_gap_mm=0, wall_clearance_mm=0)
    assert validate_mounting_layout([(-10, 0)], **kw)["feasible"]


def test_analytic_sector_incircle_and_rotational_invariance():
    angle = math.radians(30)
    R = 10
    disc_radius = R * math.sin(angle / 2) / (1 + math.sin(angle / 2))
    point = (R - disc_radius, 0)
    boundary = MountingBoundary("sector", R, -15, 30)
    assert boundary.signed_distance_mm(point) == pytest.approx(disc_radius)
    kw = dict(N=1, outer_diameter_mm=2*disc_radius, edge_gap_mm=0, wall_clearance_mm=0)
    assert validate_mounting_layout([point], boundary=boundary, **kw)["feasible"]
    rotated = MountingBoundary("sector", R, 75, 30)
    assert validate_mounting_layout([(0, point[0])], boundary=rotated, **kw)["feasible"]
    assert not validate_mounting_layout([point], boundary=boundary, **{**kw, "wall_clearance_mm": .01})["feasible"]


def test_pairwise_overlap_spacing_and_count_remain_independent():
    kw = dict(boundary=MountingBoundary("circle", 60), N=2,
              outer_diameter_mm=14, edge_gap_mm=2, wall_clearance_mm=3)
    report = validate_mounting_layout([(0, 0), (15, 0)], **kw)
    assert report["overlap_ok"] and not report["spacing_ok"]
    assert not validate_mounting_layout([(0, 0), (13, 0)], **kw)["overlap_ok"]
    assert not validate_mounting_layout([], **kw)["feasible"]
    assert not validate_mounting_layout([(0, 0)], **kw)["count_ok"]


@pytest.mark.parametrize("mode,kind", [("fixed_space", "circle"), ("fixed_space", "sector"),
                                      ("fixed_power", "circle"), ("fixed_power", "sector")])
def test_search_geometry_and_objective_against_independent_calculations(mode, kind):
    request = design(mode, kind)
    before = copy.deepcopy(request)
    report = run_shape_search(request)
    assert request == before and run_shape_search(request) == report
    assert report["eligible_count"] > 0
    assert report["feasible_count"] + report["infeasible_count"] == report["unique_requests"]
    for c in report["candidates"]:
        points = c["coordinates_mm"]
        assert len(points) == c["specification"]["N"]
        R = c["specification"]["boundary"]["radius_mm"]
        assert all(math.hypot(*v) <= R - 10 + 1e-9 for v in points)
        if kind == "sector":
            # 120-degree convex wedge: independent line-distance checks.
            assert all(y >= 10 - 1e-9 and math.sqrt(3)/2*x + .5*y >= 10-1e-9 for x, y in points)
        assert all(math.dist(a, b) >= 16-1e-9 for i, a in enumerate(points) for b in points[i+1:])
        assert c["estimated_power_kW"] == len(points)*2
        assert c["metrics"]["projection_occupancy_fraction"] <= 1 + 1e-9
    eligible = [c for c in report["candidates"] if mode == "fixed_space" or c["estimated_power_kW"] >= 20]
    best = [c for c in eligible if c["selected"]]
    assert best
    if mode == "fixed_space":
        assert {c["estimated_power_kW"] for c in best} == {max(c["estimated_power_kW"] for c in eligible)}
    else:
        assert {c["metrics"]["boundary_area_mm2"] for c in best} == {min(c["metrics"]["boundary_area_mm2"] for c in eligible)}
    assert not report["scientific_eligible"] and not report["solver_started"] and not report["attempt_created"]
    assert report["efficiency"] is None and report["combustion_chamber_volume_mm3"] is None


def test_sunflower_reuses_existing_golden_angle_radial_law():
    request = design()
    request.update(counts=[7], layouts=[{"family": "sunflower", "parameters": {}}])
    report = run_shape_search(request)
    points = report["candidates"][0]["coordinates_mm"]
    expected = generate_layout("deterministic_irregular", 7, 57, 14, 16, inner_radius=0, outer_radius=50)
    assert sorted(map(tuple, points)) == pytest.approx(sorted(expected))
    params = report["configuration"]["layouts"][0]["parameters"]
    assert params["angular_increment_deg"] == pytest.approx(137.50776405003785)


def test_missing_power_is_geometry_only_and_cannot_satisfy_fixed_power():
    request = design()
    request["power_model"] = None
    report = run_shape_search(request)
    assert report["objective"] == "maximum_feasible_N"
    assert all(c["estimated_power_kW"] is None for c in report["candidates"])
    assert report["power_status"] == "unresolved_geometry_only"
    request = design("fixed_power")
    request["power_model"] = None
    with pytest.raises(ValueError, match="power_model"):
        run_shape_search(request)


def test_target_shortfall_zero_feasible_and_duplicate_requests():
    request = design("fixed_power")
    request["target_power_kW"] = 1000
    report = run_shape_search(request)
    assert report["feasible_count"] and report["eligible_count"] == 0
    assert report["selected_layout_ids"] == []
    request = design()
    request["boundary"]["radius_mm"] = 3
    report = run_shape_search(request)
    assert report["feasible_count"] == 0 and report["infeasible_count"] > 0
    request = design()
    report = run_shape_search(request)
    request["layouts"] *= 2
    duplicated = run_shape_search(request)
    assert duplicated["unique_requests"] == report["unique_requests"]
    assert duplicated["candidates"] == report["candidates"]
    assert duplicated["infeasible_candidates"] == report["infeasible_candidates"]
    assert len(duplicated["configuration"]["layouts"]) == 2 * len(report["configuration"]["layouts"])


def test_power_metadata_changes_do_not_change_layout_identity_but_boundary_does():
    request = design()
    first = run_shape_search(request)
    request["power_model"].update(per_nozzle_power_kW=3, source="another:declaration")
    second = run_shape_search(request)
    assert [c["layout_id"] for c in first["candidates"]] == [c["layout_id"] for c in second["candidates"]]
    assert first["candidates"][0]["estimated_power_kW"] != second["candidates"][0]["estimated_power_kW"]
    request["boundary"]["radius_mm"] = 65
    third = run_shape_search(request)
    assert not {c["layout_id"] for c in first["candidates"]} & {c["layout_id"] for c in third["candidates"]}


@pytest.mark.parametrize("mutate", [
    lambda d: d["nozzle"].update(edge_gap_mm=None),
    lambda d: d["nozzle"].update(wall_clearance_mm=-1),
    lambda d: d["nozzle"].update(outer_diameter_mm=True),
    lambda d: d.update(counts=[4.0]),
    lambda d: d.update(counts=[]),
    lambda d: d.update(counts=[10001]),
    lambda d: d["boundary"].update(radius_mm=float("nan")),
    lambda d: d["boundary"].update(kind="sector", sweep_angle_deg=360),
    lambda d: d["boundary"].update(sweep_angle_deg=120),
    lambda d: d.update(tolerance_mm=7),
    lambda d: d.update(unreviewed_physics={}),
    lambda d: d["power_model"].update(source=""),
    lambda d: d["power_model"].update(per_nozzle_power_kW=0),
    lambda d: d["power_model"].update(basis="efficiency"),
    lambda d: d["layouts"].append({"family": "honeycomb", "parameters": {"spacing_mm": .01}}),
    lambda d: d["layouts"].append({"family": "sunflower", "parameters": {"pool_multiplier": True}}),
    lambda d: d["layouts"].append({"family": "sunflower", "parameters": {"radius_fraction": 1.1}}),
    lambda d: d["layouts"].append({"family": "missing", "parameters": {}}),
])
def test_all_inputs_preflight_before_generation_or_output(tmp_path, monkeypatch, mutate):
    request = design()
    mutate(request)
    monkeypatch.setattr("shape_search.generate_layout", lambda *a, **k: pytest.fail("preflight required"))
    with pytest.raises((ValueError, TypeError)):
        export_shape_search(request, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_runtime_bugs_are_not_classified_as_geometry_failures(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("unrelated implementation bug")
    monkeypatch.setattr("shape_search.generate_layout", broken)
    with pytest.raises(RuntimeError, match="implementation bug"):
        run_shape_search(design())


def test_exports_csv_png_replay_and_no_overwrite(tmp_path):
    path = export_shape_search(design(), tmp_path / "out")
    before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    assert verify_shape_search(path)["static_valid"]
    assert all(p.read_bytes() == raw for p, raw in before.items())
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    for row in report["comparison"]:
        if row["best_layout_id"]:
            directory = path.parent / row["best_layout_id"]
            assert (directory / "layout.png").read_bytes().startswith(b"\x89PNG")
            with (directory / "coordinates.csv").open(encoding="utf-8-sig", newline="") as f:
                assert next(csv.reader(f)) == ["nozzle_id", "x_mm", "y_mm", "z_mm"]
    with pytest.raises(FileExistsError):
        export_shape_search(design(), path.parent)
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_recomputed_hash_cannot_hide_forged_coordinate_export(tmp_path):
    path = export_shape_search(design(), tmp_path / "out")
    report = json.loads(path.read_text(encoding="utf-8"))
    relative = next(name for name in report["files"] if name.endswith("coordinates.csv"))
    target = path.parent / relative
    raw = target.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    values = lines[1].split(",")
    values[1] = str(float(values[1]) + 1)
    lines[1] = ",".join(values)
    target.write_text("\n".join(lines), encoding="utf-8-sig")
    report["files"][relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="Coordinate CSV"):
        verify_shape_search(path)


def test_failed_image_export_never_publishes_completed_report(tmp_path, monkeypatch):
    def broken(*args):
        raise OSError("synthetic image failure")
    monkeypatch.setattr("shape_export.plot_candidate", broken)
    with pytest.raises(OSError):
        export_shape_search(design(), tmp_path / "out")
    assert not (tmp_path / "out/search_report.json").exists()


def test_cli_modes_and_zero_eligible_reports(tmp_path, capsys):
    source = tmp_path / "request.json"
    request = design("fixed_power")
    request["target_power_kW"] = 1000
    source.write_text(json.dumps(request), encoding="utf-8")
    assert main(["--config", str(source), "--output-dir", str(tmp_path / "out")]) == 1
    capsys.readouterr()
    assert main(["--verify", str(tmp_path / "out/search_report.json")]) == 0
    assert json.loads(capsys.readouterr().out)["static_valid"]
