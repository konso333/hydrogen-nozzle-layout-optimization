"""Coaxial circular envelopes at explicitly supplied axial sections (mm).

Each identical nozzle assembly uses the same XY centre at every section. The
caller supplies conservative envelopes; this is not a CAD interference engine.
"""

import math

from geometry.constraints import as_point_array, minimum_center_distance
from shape_boundary import MountingBoundary, validate_mounting_layout
from validation import require_finite, require_tolerance


def normalize_assembly(value, boundaries, tolerance):
    if not isinstance(value, dict) or set(value) != {"sections", "review_status", "assumptions"}:
        raise ValueError("assembly requires sections, review_status and assumptions.")
    if value["review_status"] not in {"teacher_confirmation_pending", "reviewed"}:
        raise ValueError("Invalid assembly review_status.")
    if not isinstance(value["assumptions"], str) or not value["assumptions"].strip():
        raise ValueError("Assembly envelope assumptions are required.")
    supplied = value["sections"]
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 32:
        raise ValueError("assembly.sections requires 1..32 explicit sections.")
    sections, names = [], set()
    keys = {"name", "axial_range_mm", "boundary", "radius_offset_mm",
            "outer_diameter_mm", "edge_gap_mm", "wall_clearance_mm", "source"}
    for item in supplied:
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError("Incomplete or unknown assembly section fields.")
        name = item["name"]
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Section names must be nonempty and unique.")
        names.add(name)
        if not isinstance(item["source"], str) or not item["source"].strip():
            raise ValueError("Each section requires an explicit source.")
        interval = item["axial_range_mm"]
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError("axial_range_mm requires [start, end].")
        interval = [require_finite(v, "axial_range_mm") for v in interval]
        if interval[0] > interval[1]:
            raise ValueError("Axial interval must be ordered; equal ends denote a plane.")
        nozzle = {k: require_finite(item[k], k) for k in
                  ("outer_diameter_mm", "edge_gap_mm", "wall_clearance_mm")}
        if nozzle["outer_diameter_mm"] <= 0 or min(nozzle["edge_gap_mm"], nozzle["wall_clearance_mm"]) < 0:
            raise ValueError("Section diameter must be positive and clearances nonnegative.")
        if tolerance >= nozzle["outer_diameter_mm"] / 2:
            raise ValueError("Tolerance must be smaller than every section radius.")
        if not math.isfinite(nozzle["outer_diameter_mm"] + nozzle["edge_gap_mm"]):
            raise ValueError("Section spacing overflows.")
        b = item["boundary"]
        if not isinstance(b, dict) or set(b) != {"kind", "radius_mm", "start_angle_deg", "sweep_angle_deg"}:
            raise ValueError("Section boundary requires an explicit circle or sector.")
        offset = item["radius_offset_mm"]
        if b["radius_mm"] is None:
            offset = require_finite(offset, "radius_offset_mm")
        elif offset is not None:
            raise ValueError("Use a fixed section radius OR a mounting-radius offset, never both.")
        section = {**item, **nozzle, "axial_range_mm": interval, "radius_offset_mm": offset}
        # Validate every radius candidate before any point generation or output.
        for boundary in boundaries:
            resolve_sections([section], boundary)
        sections.append(section)
    return {**value, "sections": sections}


def resolve_sections(sections, mounting_boundary):
    resolved = []
    for section in sections:
        b = section["boundary"]
        radius = b["radius_mm"] if b["radius_mm"] is not None else (
            mounting_boundary.radius_mm + section["radius_offset_mm"])
        boundary = MountingBoundary(**{**b, "radius_mm": radius})
        resolved.append({**section, "boundary": boundary.to_dict(), "radius_offset_mm": None})
    return resolved


def required_center_spacing(nozzle, sections):
    return max(v["outer_diameter_mm"] + v["edge_gap_mm"] for v in [nozzle, *sections])


def section_contains(point, section, tolerance):
    return MountingBoundary(**section["boundary"]).signed_distance_mm(point) >= (
        section["outer_diameter_mm"] / 2 + section["wall_clearance_mm"] - tolerance)


def validate_assembly_layout(points, *, boundary, N, nozzle, sections, tolerance_mm=1e-9):
    """Check ALL supplied sections, reporting real gaps and constraint margins."""
    require_tolerance(tolerance_mm)
    points = as_point_array(points)
    mounting = validate_mounting_layout(points, boundary=boundary, N=N,
                                       **nozzle, tolerance_mm=tolerance_mm)
    rows = []
    minimum = minimum_center_distance(points)
    for section in sections:
        report = validate_mounting_layout(points, boundary=MountingBoundary(**section["boundary"]),
            N=N, **{k: section[k] for k in ("outer_diameter_mm", "edge_gap_mm", "wall_clearance_mm")},
            tolerance_mm=tolerance_mm)
        gap = minimum - section["outer_diameter_mm"] if math.isfinite(minimum) else None
        rows.append({"name": section["name"], "axial_range_mm": section["axial_range_mm"],
            "boundary": section["boundary"], "outer_diameter_mm": section["outer_diameter_mm"],
            "required_edge_gap_mm": section["edge_gap_mm"],
            "required_wall_clearance_mm": section["wall_clearance_mm"],
            **report, "minimum_edge_gap_mm": gap,
            "minimum_spacing_margin_mm": gap - section["edge_gap_mm"] if gap is not None else None})
    reasons = list(mounting["failure_reasons"])
    for row in rows:
        reasons.extend(row["name"] + ": " + reason for reason in row["failure_reasons"])
    return {**mounting, "mounting_validation": mounting, "section_reports": rows,
        "boundary_ok": mounting["boundary_ok"] and all(r["boundary_ok"] for r in rows),
        "spacing_ok": mounting["spacing_ok"] and all(r["spacing_ok"] for r in rows),
        "overlap_ok": mounting["overlap_ok"] and all(r["overlap_ok"] for r in rows),
        "feasible": mounting["feasible"] and all(r["feasible"] for r in rows),
        "failure_reasons": reasons,
        "required_center_spacing_mm": required_center_spacing(nozzle, sections),
        "minimum_assembly_boundary_margin_mm": min(
            [mounting["minimum_boundary_margin_mm"], *[r["minimum_boundary_margin_mm"] for r in rows]],
            key=lambda v: math.inf if v is None else v)}
