"""Geometry validation, metrics, and symmetry helpers."""

from geometry.constraints import (
    LayoutConstraintError,
    boundary_check,
    minimum_center_distance,
    spacing_check,
    validate_layout_constraints,
)
from geometry.definitions import (
    CONSTRAINT_RESULT_KEYS,
    GEOMETRY_METRIC_KEYS,
    HARD_CONSTRAINT_KEYS,
    METRIC_DEFINITIONS,
    MetricDefinition,
    metric_definitions,
)
from geometry.metrics import evaluate_geometry, evaluate_geometry_metrics

__all__ = [
    "LayoutConstraintError",
    "boundary_check",
    "minimum_center_distance",
    "spacing_check",
    "validate_layout_constraints",
    "MetricDefinition",
    "METRIC_DEFINITIONS",
    "GEOMETRY_METRIC_KEYS",
    "HARD_CONSTRAINT_KEYS",
    "CONSTRAINT_RESULT_KEYS",
    "metric_definitions",
    "evaluate_geometry",
    "evaluate_geometry_metrics",
]
