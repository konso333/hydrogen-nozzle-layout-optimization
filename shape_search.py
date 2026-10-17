"""Opt-in finite shape/capacity searches; no CFD, M2 identity or solver calls."""

import hashlib
import math

from experiments.spec import canonical_json
from geometry.constraints import LayoutConstraintError
from geometry.metrics import evaluate_geometry_metrics
from layouts import generate_layout
from shape_boundary import MountingBoundary, validate_mounting_layout
from validation import require_count, require_finite, require_tolerance
from assembly_constraints import (normalize_assembly, resolve_sections, required_center_spacing,
                                  section_contains, validate_assembly_layout)


ENGINE_VERSION = "shape-search-1"
FAMILIES = {"honeycomb", "sunflower", "rectangular", "ring"}
MAX_POOL_POINTS = 2000
MAX_REQUESTS = 20000


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label} requires exactly {sorted(expected)}.")


def _positive(value, label, *, zero=False):
    value = require_finite(value, label)
    if (zero and value < 0) or (not zero and value <= 0):
        raise ValueError(f"{label} must be {'nonnegative' if zero else 'positive'}.")
    return value


def _sequence(values, label, converter):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a nonempty explicit list.")
    return sorted(set(converter(v, label) for v in values))


def _layout(value):
    _keys(value, {"family", "parameters"}, "layout")
    family, supplied = value["family"], value["parameters"]
    if not isinstance(family, str) or family not in FAMILIES or not isinstance(supplied, dict):
        raise ValueError("Supported families: honeycomb, sunflower, rectangular, ring.")
    defaults = {"rotation_deg": 0.0, "offset_x_mm": 0.0, "offset_y_mm": 0.0}
    if family in {"honeycomb", "rectangular"}:
        defaults["spacing_mm"] = None
    else:
        defaults.update(radius_fraction=1.0, pool_multiplier=1)
    if family == "sunflower":
        defaults.update(angular_increment_deg=math.degrees(math.pi * (3 - math.sqrt(5))),
                        radial_exponent=0.5)
    if set(supplied) - set(defaults):
        raise ValueError("Unknown layout parameters: " + str(sorted(set(supplied) - set(defaults))))
    params = {**defaults, **supplied}
    for key, number in params.items():
        params[key] = (require_count(number, key) if key == "pool_multiplier" else
                       require_finite(number, key))
    if family in {"honeycomb", "rectangular"}:
        _positive(params["spacing_mm"], "spacing_mm")
    else:
        if not 0 < params["radius_fraction"] <= 1:
            raise ValueError("radius_fraction must be in (0, 1].")
    if family == "sunflower":
        _positive(params["radial_exponent"], "radial_exponent")
        params["angular_increment_deg"] %= 360
    params["rotation_deg"] %= 360
    return {"family": family, "parameters": params}


def normalize_design(value):
    """Preflight the entire design before generating or writing any candidate."""
    version = value.get("schema_version") if isinstance(value, dict) else None
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("Expected schema_version=1 or 2.")
    _keys(value, {"schema_version", "name", "mode", "boundary", "nozzle", "counts",
                  "radius_candidates_mm", "layouts", "power_model", "target_power_kW",
                  "tolerance_mm"} | ({"assembly"} if version == 2 else set()), "design")
    if not isinstance(value["name"], str) or not value["name"].strip():
        raise ValueError("name must be nonempty.")
    if value["mode"] not in {"fixed_space", "fixed_power"}:
        raise ValueError("mode must be fixed_space or fixed_power.")
    boundary = value["boundary"]
    _keys(boundary, {"kind", "radius_mm", "start_angle_deg", "sweep_angle_deg"}, "boundary")
    nozzle = value["nozzle"]
    _keys(nozzle, {"outer_diameter_mm", "edge_gap_mm", "wall_clearance_mm"}, "nozzle")
    nozzle = {k: _positive(v, k, zero=k != "outer_diameter_mm") for k, v in nozzle.items()}
    if not math.isfinite(nozzle["outer_diameter_mm"] + nozzle["edge_gap_mm"]):
        raise ValueError("Required spacing overflows.")
    tolerance = require_finite(value["tolerance_mm"], "tolerance_mm")
    require_tolerance(tolerance)
    if tolerance >= nozzle["outer_diameter_mm"] / 2:
        raise ValueError("tolerance_mm must be smaller than the nozzle radius.")
    counts = _sequence(value["counts"], "counts", require_count)
    if max(counts) > MAX_POOL_POINTS:
        raise ValueError(f"N exceeds the {MAX_POOL_POINTS}-point computational limit.")
    power = value["power_model"]
    if power is not None:
        _keys(power, {"per_nozzle_power_kW", "basis", "source", "assumptions"}, "power_model")
        if power["basis"] not in {"fuel_input", "useful_output"}:
            raise ValueError("Power basis must be fuel_input or useful_output.")
        if any(not isinstance(power[k], str) or not power[k].strip() for k in ("source", "assumptions")):
            raise ValueError("Power source and identical-nozzle/additivity assumptions are required.")
        power = {**power, "per_nozzle_power_kW": _positive(power["per_nozzle_power_kW"], "per_nozzle_power_kW")}
        if not math.isfinite(power["per_nozzle_power_kW"] * max(counts)):
            raise ValueError("Estimated power overflows.")
    if value["mode"] == "fixed_space":
        if value["radius_candidates_mm"] != [] or value["target_power_kW"] is not None:
            raise ValueError("fixed_space requires [] radius candidates and null target power.")
        boundaries = [MountingBoundary(**boundary)]
        target = None
    else:
        if boundary["radius_mm"] is not None or power is None:
            raise ValueError("fixed_power requires null boundary radius and an explicit power_model.")
        target = _positive(value["target_power_kW"], "target_power_kW")
        radii = _sequence(value["radius_candidates_mm"], "radius_candidates_mm", _positive)
        boundaries = [MountingBoundary(**{**boundary, "radius_mm": r}) for r in radii]
    if not isinstance(value["layouts"], list) or not value["layouts"]:
        raise ValueError("layouts must be a nonempty list.")
    layouts = [_layout(v) for v in value["layouts"]]
    if len(boundaries) * len(counts) * len(layouts) > MAX_REQUESTS:
        raise ValueError(f"Search exceeds the {MAX_REQUESTS}-request computational limit.")
    for b in boundaries:
        for layout in layouts:
            p = layout["parameters"]
            if layout["family"] in {"honeycomb", "rectangular"}:
                extent = b.radius_mm + math.hypot(p["offset_x_mm"], p["offset_y_mm"])
                ratio = extent / p["spacing_mm"]
                if not math.isfinite(ratio) or ratio > 15:
                    raise ValueError("Lattice pool exceeds the bounded grid budget (extent/spacing <= 15).")
            elif p["pool_multiplier"] * max(counts) > MAX_POOL_POINTS:
                raise ValueError(f"Layout pool exceeds {MAX_POOL_POINTS} points.")
    normalized = {**value, "nozzle": nozzle, "counts": counts, "layouts": layouts,
                  "power_model": power, "target_power_kW": target, "tolerance_mm": tolerance,
                  "boundary": {**boundaries[0].to_dict(), "radius_mm":
                               boundaries[0].radius_mm if value["mode"] == "fixed_space" else None},
                  "radius_candidates_mm": [] if value["mode"] == "fixed_space" else [b.radius_mm for b in boundaries]}
    if version == 2:
        normalized["assembly"] = normalize_assembly(value["assembly"], boundaries, tolerance)
    return normalized, boundaries


def _points(N, boundary, nozzle, layout, tolerance, sections=()):
    family, p = layout["family"], layout["parameters"]
    diameter = nozzle["outer_diameter_mm"]
    spacing = required_center_spacing(nozzle, sections)
    usable = boundary.radius_mm - diameter / 2 - nozzle["wall_clearance_mm"]
    for section in sections:
        usable = min(usable, section["boundary"]["radius_mm"] -
                     section["outer_diameter_mm"] / 2 - section["wall_clearance_mm"])
    if usable < 0:
        raise LayoutConstraintError("region too small for one nozzle and wall clearance")
    if family in {"honeycomb", "rectangular"}:
        # A covering square pool from the existing generators. Rotation and
        # translation are explicit; only the final shape-valid points survive.
        pitch = p["spacing_mm"]
        extent = boundary.radius_mm + math.hypot(p["offset_x_mm"], p["offset_y_mm"])
        half = math.ceil(extent / (pitch * (math.sqrt(3) / 2 if family == "honeycomb" else 1))) + 1
        side = 2 * half + 1
        radius = math.hypot(side * pitch, side * pitch) + diameter
        if family == "honeycomb":
            rows = [side if i % 2 == 0 else side - 1 for i in range(side)]
            raw = generate_layout("hexagonal", sum(rows), radius, diameter, spacing,
                                  spacing=pitch, row_counts=rows, tolerance=tolerance)
        else:
            raw = generate_layout("rectangular", side**2, radius, diameter, spacing,
                                  spacing=pitch, rows=side, columns=side, tolerance=tolerance)
    else:
        count = N * p["pool_multiplier"]
        outer = usable * p["radius_fraction"]
        common = {"N": count, "R": outer + diameter / 2, "d": diameter,
                  "s_min": spacing, "tolerance": tolerance}
        if family == "sunflower":
            raw = generate_layout("deterministic_irregular", **common, inner_radius=0,
                outer_radius=outer, angular_increment=math.radians(p["angular_increment_deg"]),
                radial_exponent=p["radial_exponent"])
        else:
            raw = generate_layout("ring", **common, ring_radii=[outer], points_per_ring=[count])
    angle = math.radians(p["rotation_deg"])
    c, s = math.cos(angle), math.sin(angle)
    transformed = [(c*x-s*y+p["offset_x_mm"], s*x+c*y+p["offset_y_mm"]) for x, y in raw]
    valid = [v for v in transformed if boundary.signed_distance_mm(v) >=
             diameter / 2 + nozzle["wall_clearance_mm"] - tolerance and
             all(section_contains(v, section, tolerance) for section in sections)]
    valid.sort(key=lambda v: (round(math.hypot(*v), 12), math.atan2(v[1], v[0]), v))
    if len(valid) < N:
        raise LayoutConstraintError(f"only {len(valid)} shape-valid pool points for N={N}")
    return valid[:N]


def run_shape_search(design):
    """Return all finite-design results, including infeasibility and power shortfall."""
    config, boundaries = normalize_design(design)
    multi = config["schema_version"] == 2
    engine = "shape-search-2" if multi else ENGINE_VERSION
    candidates, failures, seen = [], [], set()
    for boundary in boundaries:
        sections = resolve_sections(config["assembly"]["sections"], boundary) if multi else []
        for N in config["counts"]:
            for layout in config["layouts"]:
                spec = {"engine_version": engine, "boundary": boundary.to_dict(),
                        "nozzle": config["nozzle"], "N": N, "layout": layout,
                        "tolerance_mm": config["tolerance_mm"]}
                if multi:
                    spec["sections"] = sections
                identity = ("shape_layout_v2_" if multi else "shape_layout_v1_") + hashlib.sha256(canonical_json(spec).encode()).hexdigest()
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    points = _points(N, boundary, config["nozzle"], layout, config["tolerance_mm"], sections)
                    validation = (validate_assembly_layout(points, boundary=boundary, N=N,
                        nozzle=config["nozzle"], sections=sections, tolerance_mm=config["tolerance_mm"])
                        if multi else validate_mounting_layout(points, boundary=boundary, N=N,
                            **config["nozzle"], tolerance_mm=config["tolerance_mm"]))
                    if not validation["feasible"]:
                        raise LayoutConstraintError("; ".join(validation["failure_reasons"]))
                except LayoutConstraintError as exc:
                    failures.append({"layout_id": identity, "specification": spec, "reason": str(exc)})
                    continue
                legacy = evaluate_geometry_metrics(points, R=boundary.radius_mm,
                    d=config["nozzle"]["outer_diameter_mm"],
                    s_min=config["nozzle"]["outer_diameter_mm"] + config["nozzle"]["edge_gap_mm"])
                # Keep M4 descriptors with their existing meanings; sector wall
                # margins are separately named, never overwrite a circular M4 metric.
                metrics = {"min_center_distance_mm": legacy["min_center_distance"],
                    "uniformity_score": legacy["uniformity_score"],
                    "minimum_boundary_margin_mm": validation["minimum_boundary_margin_mm"],
                    "minimum_wall_clearance_mm": validation["minimum_wall_clearance_mm"],
                    "boundary_area_mm2": boundary.area_mm2,
                    "projection_occupancy_fraction": N * math.pi * (config["nozzle"]["outer_diameter_mm"] / 2)**2 / boundary.area_mm2}
                if multi:
                    metrics["minimum_assembly_boundary_margin_mm"] = validation["minimum_assembly_boundary_margin_mm"]
                    metrics["required_center_spacing_mm"] = validation["required_center_spacing_mm"]
                estimate = N * config["power_model"]["per_nozzle_power_kW"] if config["power_model"] else None
                meets = estimate >= config["target_power_kW"] if config["mode"] == "fixed_power" else None
                candidates.append({"layout_id": identity, "specification": spec,
                    "coordinates_mm": [list(v) for v in points], "validation": validation,
                    "metrics": metrics, "estimated_power_kW": estimate,
                    "target_met_by_model": meets, "selected": False})
    eligible = [c for c in candidates if config["mode"] == "fixed_space" or c["target_met_by_model"]]
    def objective(c):
        return (c["metrics"]["boundary_area_mm2"] if config["mode"] == "fixed_power" else
                -(c["estimated_power_kW"] if config["power_model"] else c["specification"]["N"]))
    optimum = min(map(objective, eligible), default=None)
    for c in eligible:
        c["selected"] = objective(c) == optimum
    comparison = []
    for family in sorted({v["family"] for v in config["layouts"]}):
        rows = [c for c in eligible if c["specification"]["layout"]["family"] == family]
        best = min(rows, key=lambda c: (objective(c), c["layout_id"]), default=None)
        comparison.append({"family": family, "feasible_candidates": sum(
            c["specification"]["layout"]["family"] == family for c in candidates),
            "eligible_candidates": len(rows), "best_layout_id": best["layout_id"] if best else None})
    result = {"schema_version": config["schema_version"], "kind": "shape_layout_search", "engine_version": engine,
        "configuration": config, "mode": config["mode"], "unique_requests": len(seen),
        "feasible_count": len(candidates), "infeasible_count": len(failures),
        "eligible_count": len(eligible), "selected_layout_ids": [c["layout_id"] for c in eligible if c["selected"]],
        "comparison": comparison, "candidates": candidates, "infeasible_candidates": failures,
        "scope": "provided_counts_radii_and_layout_parameters_only; not a global optimum",
        "power_status": "explicit_additive_model_estimate" if config["power_model"] else "unresolved_geometry_only",
        "objective": "minimum_mounting_area_mm2" if config["mode"] == "fixed_power" else
                     "maximum_estimated_power_kW" if config["power_model"] else "maximum_feasible_N",
        "efficiency": None, "combustion_chamber_volume_mm3": None,
        "scientific_eligible": False, "solver_started": False, "attempt_created": False,
        "identity_scope": "independent mounting layout specification; not M2 case_id or M6 cfd_case_id"}
    if multi:
        result.update(assembly_review_status=config["assembly"]["review_status"],
            manufacturing_eligible=False,
            assembly_scope="supplied coaxial section envelopes only; no fixed-hole, seal, supply or full CAD clearance certification")
    return result
