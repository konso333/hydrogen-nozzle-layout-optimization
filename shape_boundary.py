"""Full-disc containment in circular or sector mounting regions (mm/degrees)."""

from dataclasses import asdict, dataclass
import math

from geometry.constraints import as_point_array, validate_layout_constraints
from validation import require_count, require_finite, require_tolerance


@dataclass(frozen=True)
class MountingBoundary:
    kind: str
    radius_mm: float
    start_angle_deg: float = 0.0
    sweep_angle_deg: float = 360.0

    def __post_init__(self):
        if self.kind not in {"circle", "sector"}:
            raise ValueError("Boundary kind must be circle or sector.")
        for key in ("radius_mm", "start_angle_deg", "sweep_angle_deg"):
            object.__setattr__(self, key, require_finite(getattr(self, key), key))
        if self.radius_mm <= 0:
            raise ValueError("radius_mm must be positive.")
        if self.kind == "circle":
            if self.start_angle_deg != 0 or self.sweep_angle_deg != 360:
                raise ValueError("A circle requires start=0 and sweep=360 degrees.")
        elif not 0 < self.sweep_angle_deg < 360:
            raise ValueError("A sector requires 0 < sweep_angle_deg < 360.")
        object.__setattr__(self, "start_angle_deg", self.start_angle_deg % 360)
        if not math.isfinite(self.area_mm2) or self.area_mm2 <= 0:
            raise ValueError("Boundary area must be finite and positive in mm2.")

    @property
    def area_mm2(self):
        return math.pi * self.radius_mm**2 * self.sweep_angle_deg / 360

    def to_dict(self):
        return asdict(self)

    def signed_distance_mm(self, point):
        """Exact distance to the arc/finite radial edges; negative outside.

        Finite segments, rather than half-plane intersections, also handle
        reflex sectors (>180 degrees), the apex, and the angular wrap at 0.
        """
        x, y = point
        radius = math.hypot(x, y)
        if self.kind == "circle":
            return self.radius_mm - radius
        start = math.radians(self.start_angle_deg)
        sweep = math.radians(self.sweep_angle_deg)
        relative = (math.atan2(y, x) - start) % (2 * math.pi)
        angular_inside = radius == 0 or relative <= sweep
        distances = []
        endpoints = []
        for angle in (start, start + sweep):
            ux, uy = math.cos(angle), math.sin(angle)
            projection = min(self.radius_mm, max(0.0, x * ux + y * uy))
            distances.append(math.hypot(x - projection * ux, y - projection * uy))
            endpoints.append((self.radius_mm * ux, self.radius_mm * uy))
        arc_distance = (abs(self.radius_mm - radius) if angular_inside else
                        min(math.dist((x, y), end) for end in endpoints))
        distance = min(*distances, arc_distance)
        return distance if angular_inside and radius <= self.radius_mm else -distance


def validate_mounting_layout(points, *, boundary, N, outer_diameter_mm,
                             edge_gap_mm, wall_clearance_mm, tolerance_mm=1e-9):
    """Reuse legacy count/overlap/spacing checks and replace circular containment."""
    require_count(N, "N")
    require_tolerance(tolerance_mm, "tolerance_mm")
    diameter = require_finite(outer_diameter_mm, "outer_diameter_mm")
    gap = require_finite(edge_gap_mm, "edge_gap_mm")
    wall = require_finite(wall_clearance_mm, "wall_clearance_mm")
    if diameter <= 0 or gap < 0 or wall < 0:
        raise ValueError("Diameter must be positive; clearances must be nonnegative.")
    array = as_point_array(points)
    report = validate_layout_constraints(array, N=N, R=boundary.radius_mm, d=diameter,
                                        s_min=diameter + gap, tolerance=tolerance_mm)
    clearances = [boundary.signed_distance_mm(p) - diameter / 2 for p in array]
    report["boundary_ok"] = all(v >= wall - tolerance_mm for v in clearances)
    report["failure_reasons"] = [r for r in report["failure_reasons"] if "circular boundary" not in r]
    if not report["boundary_ok"]:
        report["failure_reasons"].append("nozzle edge violates the arc or radial-edge wall clearance")
    report["feasible"] = all(report[k] for k in ("count_ok", "boundary_ok", "overlap_ok", "spacing_ok"))
    report["minimum_wall_clearance_mm"] = min(clearances, default=None)
    report["minimum_boundary_margin_mm"] = min((v - wall for v in clearances), default=None)
    return report
