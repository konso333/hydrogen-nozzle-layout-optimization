"""Independent physical counterexamples and multi-section search integration."""

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from assembly_constraints import (normalize_assembly, resolve_sections,
                                  validate_assembly_layout)
from shape_boundary import MountingBoundary, validate_mounting_layout
from shape_export import export_shape_search, verify_shape_search
from shape_search import normalize_design, run_shape_search


def design():
    data = json.loads((Path(__file__).parents[1] / "examples/shape_fixed_space_circle.synthetic.json").read_text(encoding="utf-8"))
    data.update(schema_version=2, counts=[1, 4, 7, 9, 12], power_model=None)
    data["boundary"]["radius_mm"] = 38
    data["nozzle"].update(edge_gap_mm=0, wall_clearance_mm=0)
    data["layouts"] = [{"family": f, "parameters": {"spacing_mm": 21} if f in
                        {"honeycomb", "rectangular"} else {}} for f in
                        ["honeycomb", "rectangular", "sunflower", "ring"]]
    data["assembly"] = {"review_status": "teacher_confirmation_pending",
        "assumptions": "Synthetic unit test; identical parallel coaxial envelopes only.",
        "sections": [section("front", 38, 14, 0), section("rear", 40, 20, -30)]}
    return data


def section(name, radius, diameter, axial):
    return {"name": name, "axial_range_mm": [axial, axial],
        "boundary": {"kind": "circle", "radius_mm": radius, "start_angle_deg": 0, "sweep_angle_deg": 360},
        "radius_offset_mm": None, "outer_diameter_mm": diameter,
        "edge_gap_mm": 0, "wall_clearance_mm": 0, "source": "synthetic:test"}


def validate(points, request=None):
    data = request or design()
    config, boundaries = normalize_design(data)
    return validate_assembly_layout(points, boundary=boundaries[0], N=len(points),
        nozzle=config["nozzle"], sections=resolve_sections(config["assembly"]["sections"], boundaries[0]))


def test_front_clearance_alone_misses_rear_collision():
    data = design()
    points = [(-8, 0), (8, 0)]
    assert validate_mounting_layout(points, boundary=MountingBoundary(**data["boundary"]),
                                   N=2, **data["nozzle"])["feasible"]
    result = validate(points)
    assert not result["feasible"] and not result["overlap_ok"]
    rear = result["section_reports"][1]
    assert rear["minimum_edge_gap_mm"] == -4
    assert any("rear" in r and "overlap" in r for r in result["failure_reasons"])


def test_rear_installation_edge_can_bind_when_front_fits():
    result = validate([(31, 0)])
    assert result["mounting_validation"]["feasible"]
    assert not result["boundary_ok"]
    assert result["section_reports"][1]["minimum_wall_clearance_mm"] == -1


def test_observed_nine_nozzle_baseline_against_hand_calculation():
    points = [(x, y) for x in [-21, 0, 21] for y in [-21, 0, 21]]
    result = validate(points)
    assert result["feasible"]
    front, rear = result["section_reports"]
    assert front["minimum_edge_gap_mm"] == pytest.approx(7)
    assert rear["minimum_edge_gap_mm"] == pytest.approx(1)
    assert front["minimum_wall_clearance_mm"] == pytest.approx(38-21*math.sqrt(2)-7)
    assert rear["minimum_wall_clearance_mm"] == pytest.approx(40-21*math.sqrt(2)-10)
    assert result["required_center_spacing_mm"] == 20


def test_tangent_rear_and_positive_clearance_are_distinct():
    points = [(-10, 0), (10, 0)]
    assert validate(points)["feasible"]
    data = design()
    data["assembly"]["sections"][1]["edge_gap_mm"] = 0.1
    result = validate(points, data)
    assert result["overlap_ok"] and not result["spacing_ok"]
    assert result["section_reports"][1]["minimum_spacing_margin_mm"] == pytest.approx(-.1)


def test_independently_constructed_ten_disc_packing_requires_relaxed_clearances():
    h = 10*math.sqrt(3)
    points = [(x, 0) for x in [-30, -10, 10, 30]] + [(x, y) for x in [-20, 0, 20] for y in [-h, h]]
    result = validate(points)
    assert result["feasible"]
    rear = result["section_reports"][1]
    assert rear["minimum_edge_gap_mm"] == pytest.approx(0)
    assert rear["minimum_wall_clearance_mm"] == pytest.approx(0)
    data = design()
    data["assembly"]["sections"][1].update(edge_gap_mm=1, wall_clearance_mm=40-21*math.sqrt(2)-10)
    assert not validate(points, data)["feasible"]


@pytest.mark.parametrize("kind,sweep", [("circle", 360), ("sector", 120), ("sector", 270)])
def test_search_obeys_every_section_with_independent_distances(kind, sweep):
    data = design()
    data["boundary"].update(kind=kind, sweep_angle_deg=sweep)
    data["assembly"]["sections"][1]["boundary"].update(kind=kind, sweep_angle_deg=sweep)
    if kind == "sector":
        data["layouts"][2]["parameters"]["pool_multiplier"] = 4
        data["layouts"][3]["parameters"]["pool_multiplier"] = 4
    before = copy.deepcopy(data)
    result = run_shape_search(data)
    assert result == run_shape_search(data) and before == data
    assert result["eligible_count"] > 0
    assert result["manufacturing_eligible"] is False
    for candidate in result["candidates"]:
        points = candidate["coordinates_mm"]
        assert candidate["layout_id"].startswith("shape_layout_v2_")
        assert all(math.hypot(*p) <= 30+1e-9 for p in points)
        assert all(math.dist(a, b) >= 20-1e-9 for i,a in enumerate(points) for b in points[i+1:])
        if sweep == 120:
            assert all(y >= 10-1e-9 and math.sqrt(3)/2*x + .5*y >= 10-1e-9 for x,y in points)
        assert all(r["feasible"] for r in candidate["validation"]["section_reports"])
        assert candidate["estimated_power_kW"] is None


def test_fixed_power_resolves_all_radius_offsets_and_rechecks_geometry():
    data = design()
    data.update(mode="fixed_power", target_power_kW=8, radius_candidates_mm=[25, 38, 50])
    data["boundary"]["radius_mm"] = None
    data["power_model"] = {"basis": "fuel_input", "per_nozzle_power_kW": 2,
                          "source": "synthetic:test", "assumptions": "Independent synthetic rating."}
    for s, offset in zip(data["assembly"]["sections"], [0, 2]):
        s["boundary"]["radius_mm"] = None
        s["radius_offset_mm"] = offset
    report = run_shape_search(data)
    assert report["eligible_count"] > 0
    for c in report["candidates"]:
        spec = c["specification"]
        assert spec["sections"][1]["boundary"]["radius_mm"] == spec["boundary"]["radius_mm"]+2
    best = [c for c in report["candidates"] if c["selected"]]
    assert all(c["estimated_power_kW"] >= 8 for c in best)
    assert {c["metrics"]["boundary_area_mm2"] for c in best} == {
        min(c["metrics"]["boundary_area_mm2"] for c in report["candidates"] if c["target_met_by_model"])}


@pytest.mark.parametrize("mutate", [
    lambda d: d["assembly"].update(sections=[]),
    lambda d: d["assembly"].update(review_status="approved_by_old_chat"),
    lambda d: d["assembly"].update(assumptions=""),
    lambda d: d["assembly"]["sections"].append(copy.deepcopy(d["assembly"]["sections"][0])),
    lambda d: d["assembly"]["sections"][0].update(outer_diameter_mm=None),
    lambda d: d["assembly"]["sections"][0].update(outer_diameter_mm=True),
    lambda d: d["assembly"]["sections"][0].update(edge_gap_mm=-1),
    lambda d: d["assembly"]["sections"][0].update(wall_clearance_mm=None),
    lambda d: d["assembly"]["sections"][0].update(outer_diameter_mm=1e-10),
    lambda d: d["assembly"]["sections"][0].update(source=""),
    lambda d: d["assembly"]["sections"][0].update(axial_range_mm=[1, -1]),
    lambda d: d["assembly"]["sections"][0].update(axial_range_mm=[float("nan"), 0]),
    lambda d: d["assembly"]["sections"][0].update(radius_offset_mm=2),
    lambda d: d["assembly"]["sections"][0]["boundary"].update(radius_mm=None),
    lambda d: d["assembly"]["sections"][0]["boundary"].update(radius_mm=-1),
    lambda d: d["assembly"]["sections"][0].update(unreviewed_physics={}),
])
def test_invalid_sections_fail_before_generation_or_writes(tmp_path, monkeypatch, mutate):
    data = design()
    mutate(data)
    monkeypatch.setattr("shape_search.generate_layout", lambda *a, **k: pytest.fail("not preflighted"))
    with pytest.raises((ValueError, TypeError)):
        export_shape_search(data, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_bad_offset_for_any_radius_is_rejected_before_generation():
    data = design()
    data["assembly"]["sections"][0]["boundary"]["radius_mm"] = None
    data["assembly"]["sections"][0]["radius_offset_mm"] = -40
    with pytest.raises(ValueError, match="radius_mm"):
        normalize_design(data)


def test_section_geometry_changes_identity_but_review_and_power_do_not():
    data = design()
    data.update(counts=[4], layouts=[data["layouts"][1]])
    original = run_shape_search(data)["candidates"][0]["layout_id"]
    data["assembly"]["review_status"] = "reviewed"
    assert run_shape_search(data)["candidates"][0]["layout_id"] == original
    data["assembly"]["sections"][1]["edge_gap_mm"] = .1
    assert run_shape_search(data)["candidates"][0]["layout_id"] != original


def test_section_export_and_forged_csv_hash_rejected(tmp_path):
    report_path = export_shape_search(design(), tmp_path / "out")
    assert verify_shape_search(report_path)["static_valid"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    target = report_path.parent / "section_clearances.csv"
    target.write_bytes(target.read_bytes().replace(b"rear", b"forged"))
    report["files"][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="Section clearance CSV"):
        verify_shape_search(report_path)
