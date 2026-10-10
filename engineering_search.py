"""Adapt one physical installation profile to the existing M3 search engine."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict

from engineering_config import EngineeringGeometryConfig
from engineering_geometry import to_validation_geometry
from experiments.batch import run_batch
from experiments.search_space import ExperimentSearchSpace
from optimization.objectives import DEFAULT_OBJECTIVE_PROFILE
from validation import InputValidationError


def make_engineering_search_space(
    *, config: EngineeringGeometryConfig, design: dict, tolerance: float = 1e-9,
) -> ExperimentSearchSpace:
    """Preflight explicit N/parameter groups without generating coordinates.

    All candidates share one effective geometry. User designs cannot override
    the installation profile or silently request the legacy preset search.
    """
    geometry = to_validation_geometry(config, tolerance=tolerance)
    required = {"schema_version", "name", "groups"}
    optional = {"description", "symmetry_tolerance"}
    if (not isinstance(design, dict) or not required <= set(design)
            or set(design) - required - optional):
        raise InputValidationError(
            "Engineering search requires schema_version, name, groups; "
            "optional description and symmetry_tolerance. Geometry belongs in the engineering config."
        )
    return ExperimentSearchSpace.from_dict({
        **{key: value for key, value in design.items() if key != "groups"},
        "blocks": [{
            "geometry_space": {key: [value] for key, value in asdict(geometry).items()},
            "groups": design["groups"],
        }],
    })


def run_engineering_search(
    *, config: EngineeringGeometryConfig, design: dict, tolerance: float = 1e-9,
) -> dict[str, object]:
    """Search the supplied finite design; do not archive cases or invoke CFD.

    M3 handles preflight, deterministic generation, constraint failures, metric
    calculation, deduplication and Pareto marking. Its case IDs describe only
    the effective geometry; the full physical configuration stays in this report.
    """
    space = make_engineering_search_space(config=config, design=design, tolerance=tolerance)
    report = run_batch(space)
    planned = Counter(spec["N"] for spec in report["specifications"])
    feasible = Counter(row["N"] for row in report["rows"])
    pareto = Counter(row["N"] for row in report["rows"] if row["pareto_candidate"])
    for row in report["rows"]:
        distance = row["min_center_distance"]
        radius = row["max_center_radius"]
        row["minimum_nozzle_edge_gap_mm"] = (
            distance - config.nozzle_outer_diameter_mm if distance is not None else None
        )
        row["minimum_wall_clearance_mm"] = (
            config.installation_radius_mm - config.nozzle_outer_diameter_mm / 2 - radius
            if radius is not None else None
        )
    return {
        **report,
        **config.inspect(),
        "kind": "engineering_layout_search",
        "validation_geometry": asdict(to_validation_geometry(config, tolerance=tolerance)),
        "case_id_scope": "effective_geometry_specification_only",
        "search_space": space.to_dict(),
        "objective_profile": DEFAULT_OBJECTIVE_PROFILE.to_dict(),
        "search_scope": "provided_N_and_parameter_sets_only",
        "largest_feasible_N": max(feasible, default=None),
        "by_N": [{
            "N": n, "unique": planned[n], "feasible": feasible[n],
            "infeasible": planned[n] - feasible[n], "pareto_count": pareto[n],
        } for n in sorted(planned)],
    }
