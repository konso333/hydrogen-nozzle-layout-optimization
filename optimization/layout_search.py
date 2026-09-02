"""Generation and evaluation of candidate specifications."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from config import GeometryConfig
from geometry.metrics import evaluate_geometry
from layouts import generate_layout


@dataclass(frozen=True)
class LayoutSpec:
    layout_type: str
    parameters: dict[str, object]


@dataclass
class LayoutCandidate:
    candidate_id: str
    N: int
    layout_type: str
    layout_parameters: dict[str, object]
    points: list[tuple[float, float]]
    metrics: dict[str, object]

    def summary_row(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "N": self.N,
            "layout_type": self.layout_type,
            "layout_parameters": json.dumps(
                self.layout_parameters,
                ensure_ascii=False,
                sort_keys=True,
            ),
            **self.metrics,
        }


def default_layout_specs(N: int, config: GeometryConfig) -> list[LayoutSpec]:
    """Build a deterministic, bounded geometry search space for one N."""

    required = config.required_center_distance
    allowed = config.allowed_center_radius
    specs: list[LayoutSpec] = []

    # Grid families at several physically interpretable centre spacings.
    for spacing in sorted({required, 1.25 * required, 1.75 * required, 2.25 * required}):
        specs.append(LayoutSpec("rectangular", {"spacing": spacing}))
    for spacing in sorted({required, 1.25 * required, 1.5 * required, 2.0 * required}):
        specs.append(LayoutSpec("hexagonal", {"spacing": spacing}))

    outer = 0.98 * allowed
    specs.append(
        LayoutSpec(
            "ring",
            {"ring_radii": [outer], "points_per_ring": [N]},
        )
    )

    # Two-ring staggered allocation.  Counts are discrete search parameters.
    inner_count = max(3, int(round(N / 3.0)))
    outer_count = N - inner_count
    if outer_count > 0:
        specs.append(
            LayoutSpec(
                "staggered_ring",
                {
                    "ring_radii": [28.0, outer],
                    "points_per_ring": [inner_count, outer_count],
                    "delta_theta": math.pi / outer_count,
                },
            )
        )

    # Three rings with deliberately unequal radial gaps and explicit phases.
    first_count = max(3, int(round(0.18 * N)))
    second_count = max(4, int(round(0.32 * N)))
    third_count = N - first_count - second_count
    if third_count > 0:
        specs.append(
            LayoutSpec(
                "nonuniform_ring",
                {
                    "ring_radii": [20.0, 36.0, outer],
                    "points_per_ring": [first_count, second_count, third_count],
                    "ring_offsets": [
                        0.0,
                        math.pi / second_count,
                        math.pi / third_count,
                    ],
                },
            )
        )

    # Factor-compatible sector and spoke arrangements.
    for division in range(3, min(N, 16) + 1):
        if N % division:
            continue
        points_per_group = N // division

        if points_per_group <= 6:
            sector_width = math.pi / division
            inner_needed = required / (2.0 * math.sin(sector_width / 2.0)) * 1.05
            inner = max(24.0, inner_needed)
            radial_levels = min(3, points_per_group)
            if radial_levels == 1:
                inner = outer
            if radial_levels == 1 or (outer - inner) / (radial_levels - 1) >= required:
                specs.append(
                    LayoutSpec(
                        "sector",
                        {
                            "num_sectors": division,
                            "points_per_sector": points_per_group,
                            "inner_radius": inner,
                            "outer_radius": outer,
                            "sector_angle": sector_width,
                            "radial_levels": radial_levels,
                            "angular_offset": math.pi / (4.0 * division),
                        },
                    )
                )

        if points_per_group <= 4:
            inner_needed = required / (2.0 * math.sin(math.pi / division)) * 1.05
            inner = max(16.0, inner_needed)
            if points_per_group == 1:
                inner = outer
            radial_gap = (
                math.inf
                if points_per_group == 1
                else (outer - inner) / (points_per_group - 1)
            )
            if inner <= outer and radial_gap >= required:
                specs.append(
                    LayoutSpec(
                        "radial_spoke",
                        {
                            "num_spokes": division,
                            "points_per_spoke": points_per_group,
                            "inner_radius": inner,
                            "outer_radius": outer,
                            "angular_offset": math.pi / (2.0 * division),
                        },
                    )
                )

    specs.append(
        LayoutSpec(
            "deterministic_irregular",
            {"inner_radius": 0.0, "outer_radius": outer},
        )
    )
    return specs


def evaluate_spec(
    N: int,
    config: GeometryConfig,
    spec: LayoutSpec,
    candidate_id: str,
) -> LayoutCandidate | None:
    """Return a feasible candidate, or None for any invalid specification."""

    try:
        points = generate_layout(
            spec.layout_type,
            N,
            config.R,
            config.d,
            config.s_min,
            tolerance=config.tolerance,
            **spec.parameters,
        )
        metrics = evaluate_geometry(
            points,
            R=config.R,
            d=config.d,
            s_min=config.s_min,
            expected_N=N,
            tolerance=config.tolerance,
        )
    except ValueError:
        return None
    if not metrics["feasible"]:
        return None
    return LayoutCandidate(
        candidate_id=candidate_id,
        N=N,
        layout_type=spec.layout_type,
        layout_parameters=dict(spec.parameters),
        points=points,
        metrics=metrics,
    )


def search_layouts_for_n(
    N: int,
    config: GeometryConfig,
    specs: list[LayoutSpec] | None = None,
) -> list[LayoutCandidate]:
    """Generate only feasible candidates for one nozzle count."""

    if N <= 0:
        raise ValueError("N must be positive.")
    selected_specs = default_layout_specs(N, config) if specs is None else specs
    candidates: list[LayoutCandidate] = []
    for index, spec in enumerate(selected_specs, start=1):
        candidate_id = f"N{N:03d}_{spec.layout_type}_{index:03d}"
        candidate = evaluate_spec(N, config, spec, candidate_id)
        if candidate is not None:
            candidates.append(candidate)
    return candidates
