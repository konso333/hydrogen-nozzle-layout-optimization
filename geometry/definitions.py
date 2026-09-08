"""Machine-readable contracts for geometry constraints and metrics.

The definitions in this module describe values; they do not calculate them and
they do not assert a relationship with combustion performance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class MetricDefinition:
    """Metadata for one value returned by ``evaluate_geometry_metrics``."""

    key: str
    display_name: str
    description: str
    unit: str
    direction: str
    category: str
    defined_for: str
    optimization_eligible: bool
    scale_behavior: str
    rotation_invariant: bool
    permutation_invariant: bool = True
    undefined_value: None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _metric(
    key: str,
    display_name: str,
    description: str,
    unit: str,
    direction: str,
    category: str,
    defined_for: str,
    optimization_eligible: bool,
    scale_behavior: str,
    rotation_invariant: bool,
) -> MetricDefinition:
    if direction not in {"minimize", "maximize", "descriptive"}:
        raise ValueError(f"Invalid metric direction: {direction}")
    if scale_behavior not in {"length-dependent", "scale-normalized", "dimensionless"}:
        raise ValueError(f"Invalid scale behavior: {scale_behavior}")
    return MetricDefinition(
        key=key,
        display_name=display_name,
        description=description,
        unit=unit,
        direction=direction,
        category=category,
        defined_for=defined_for,
        optimization_eligible=optimization_eligible,
        scale_behavior=scale_behavior,
        rotation_invariant=rotation_invariant,
    )


_DEFINITIONS = (
    _metric(
        "min_center_distance", "Minimum centre distance",
        "Minimum Euclidean distance between any two nozzle centres.",
        "mm", "maximize", "spacing", "N >= 2", True,
        "length-dependent", True,
    ),
    _metric(
        "mean_nearest_neighbor_distance", "Mean nearest-neighbour distance",
        "Population mean of the nearest-neighbour distance of every nozzle.",
        "mm", "descriptive", "spacing", "N >= 2", False,
        "length-dependent", True,
    ),
    _metric(
        "std_nearest_neighbor_distance", "Nearest-neighbour distance standard deviation",
        "Population standard deviation (ddof=0) of nearest-neighbour distances.",
        "mm", "descriptive", "spacing", "N >= 2", False,
        "length-dependent", True,
    ),
    _metric(
        "nearest_neighbor_cv", "Nearest-neighbour coefficient of variation",
        "Population standard deviation divided by mean nearest-neighbour distance.",
        "dimensionless", "minimize", "spacing", "N >= 2 and mean > 0", True,
        "scale-normalized", True,
    ),
    _metric(
        "uniformity_score", "Nearest-neighbour uniformity score",
        "Monotone transform 1 / (1 + nearest_neighbor_cv).",
        "dimensionless", "maximize", "spacing", "nearest_neighbor_cv is defined", True,
        "scale-normalized", True,
    ),
    _metric(
        "minimum_spacing_margin", "Minimum spacing margin",
        "Minimum centre distance minus max(d, s_min).",
        "mm", "descriptive", "constraint_margin", "N >= 2", False,
        "length-dependent", True,
    ),
    _metric(
        "minimum_boundary_margin", "Minimum boundary margin",
        "Allowed centre radius (R - d/2) minus the largest centre radius.",
        "mm", "descriptive", "constraint_margin", "N >= 1", False,
        "length-dependent", True,
    ),
    _metric(
        "max_center_radius", "Maximum centre radius",
        "Maximum distance of a nozzle centre from the chamber origin.",
        "mm", "descriptive", "radial_distribution", "N >= 1", False,
        "length-dependent", True,
    ),
    _metric(
        "mean_center_radius", "Mean centre radius",
        "Population mean distance of nozzle centres from the chamber origin.",
        "mm", "descriptive", "radial_distribution", "N >= 1", False,
        "length-dependent", True,
    ),
    _metric(
        "radial_std", "Radial standard deviation",
        "Population standard deviation (ddof=0) of centre radii.",
        "mm", "descriptive", "radial_distribution", "N >= 1", False,
        "length-dependent", True,
    ),
    _metric(
        "normalized_max_center_radius", "Normalized maximum centre radius",
        "Maximum centre radius divided by the positive allowed centre radius R - d/2.",
        "dimensionless", "descriptive", "radial_distribution",
        "N >= 1 and R - d/2 > 0", False, "scale-normalized", True,
    ),
    _metric(
        "normalized_mean_center_radius", "Normalized mean centre radius",
        "Mean centre radius divided by the positive allowed centre radius R - d/2.",
        "dimensionless", "descriptive", "radial_distribution",
        "N >= 1 and R - d/2 > 0", False, "scale-normalized", True,
    ),
    _metric(
        "normalized_radial_std", "Normalized radial standard deviation",
        "Radial population standard deviation divided by the positive allowed centre radius.",
        "dimensionless", "descriptive", "radial_distribution",
        "N >= 1 and R - d/2 > 0", False, "scale-normalized", True,
    ),
    _metric(
        "centroid_offset", "Centroid offset",
        "Euclidean distance from the arithmetic centroid of nozzle centres to the chamber origin.",
        "mm", "minimize", "centroid", "N >= 1", True,
        "length-dependent", True,
    ),
    _metric(
        "normalized_centroid_offset", "Normalized centroid offset",
        "Centroid offset divided by the positive allowed centre radius R - d/2.",
        "dimensionless", "minimize", "centroid",
        "N >= 1 and R - d/2 > 0", True, "scale-normalized", True,
    ),
    _metric(
        "second_moment_anisotropy", "Second-moment anisotropy",
        "For the centroid-centred population covariance eigenvalues lambda_max >= lambda_min, "
        "(lambda_max - lambda_min) / (lambda_max + lambda_min).",
        "dimensionless", "minimize", "shape",
        "N >= 2 and total centroid-centred second moment > 0", True,
        "scale-normalized", True,
    ),
    _metric(
        "x_axis_symmetry", "x-axis reflection symmetry",
        "Whether every point has a reflected partner across the fixed x axis within tolerance.",
        "dimensionless", "descriptive", "symmetry", "N >= 0", False,
        "dimensionless", False,
    ),
    _metric(
        "y_axis_symmetry", "y-axis reflection symmetry",
        "Whether every point has a reflected partner across the fixed y axis within tolerance.",
        "dimensionless", "descriptive", "symmetry", "N >= 0", False,
        "dimensionless", False,
    ),
    _metric(
        "origin_symmetry", "Origin inversion symmetry",
        "Whether every point has a partner under inversion through the chamber origin within "
        "componentwise tolerance; near the tolerance boundary this numerical result can depend "
        "on coordinate orientation.",
        "dimensionless", "descriptive", "symmetry", "N >= 0", False,
        "dimensionless", False,
    ),
)


METRIC_DEFINITIONS = MappingProxyType({definition.key: definition for definition in _DEFINITIONS})
GEOMETRY_METRIC_KEYS = tuple(METRIC_DEFINITIONS)

# ``evaluate_geometry`` remains a compatibility composite of these validation
# fields and the formal metrics above. They are deliberately not metric keys.
CONSTRAINT_RESULT_KEYS = (
    "nozzle_count", "count_ok", "boundary_ok", "overlap_ok", "spacing_ok",
    "feasible", "failure_reasons",
)
HARD_CONSTRAINT_KEYS = ("count_ok", "boundary_ok", "overlap_ok", "spacing_ok")

# Ordered output contract used by the three pre-M4 scripts. New M4 metrics are
# available through the evaluator and M2/M3 records without changing old CSVs.
LEGACY_EVALUATION_KEYS = (
    "nozzle_count",
    "min_center_distance",
    "mean_nearest_neighbor_distance",
    "std_nearest_neighbor_distance",
    "nearest_neighbor_cv",
    "uniformity_score",
    "max_center_radius",
    "mean_center_radius",
    "radial_std",
    "count_ok",
    "boundary_ok",
    "overlap_ok",
    "spacing_ok",
    "feasible",
    "failure_reasons",
    "x_axis_symmetry",
    "y_axis_symmetry",
    "origin_symmetry",
)
LEGACY_METRIC_KEYS = tuple(
    key for key in LEGACY_EVALUATION_KEYS if key not in CONSTRAINT_RESULT_KEYS
)


def metric_definitions() -> dict[str, dict[str, object]]:
    """Return a JSON-ready copy of the complete metric inventory."""

    return {key: definition.to_dict() for key, definition in METRIC_DEFINITIONS.items()}


def legacy_evaluation(report: dict[str, object]) -> dict[str, object]:
    """Select the ordered pre-M4 report fields for legacy tabular outputs."""

    return {key: report[key] for key in LEGACY_EVALUATION_KEYS}
