"""Versioned complete case specifications and deterministic scientific identity."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, dataclass

import numpy as np

from config import GeometryConfig
from json_values import json_value
from layouts import BASELINE_LAYOUT_PARAMETERS, LAYOUT_REGISTRY, generate_layout
from validation import require_count, require_counts, require_finite, require_tolerance


SCHEMA_VERSION = 1
UNITS = {"length": "mm", "angle": "rad", "count": "1"}
BASELINE_GENERATORS = {
    "A_Rectangular": "rectangular", "B_Hexagonal": "hexagonal",
    "C_Double_Ring": "ring", "D_Triple_Ring": "ring",
}
BUILTIN_TYPES = {
    "rectangular", "hexagonal", "ring", "sector", "radial_spoke",
    "staggered_ring", "nonuniform_ring", "deterministic_irregular",
}
COUNT_PARAMETERS = {
    "rows", "columns", "num_sectors", "points_per_sector", "radial_levels",
    "num_spokes", "points_per_spoke",
}
COUNT_SEQUENCES = {"row_counts", "points_per_ring"}
RING_TYPES = {"ring", "staggered_ring", "nonuniform_ring"}


def canonical_json(value) -> str:
    return json.dumps(json_value(value, canonical_numbers=True), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def _resolved_parameters(generator_type, N, config, parameters):
    # Signature binding preserves meaningful None defaults (automatic grid mode,
    # generated radii, or no additional ring phase), and rejects unknown keys.
    common = {"N": N, **asdict(config)}
    if set(parameters) & set(common):
        raise ValueError("Geometry parameters belong in N / GeometryConfig.")
    bound = inspect.signature(LAYOUT_REGISTRY[generator_type]).bind(**common, **parameters)
    bound.apply_defaults()
    resolved = {key: value for key, value in bound.arguments.items() if key not in common}
    for key, value in resolved.items():
        if value is None:
            continue
        if key in COUNT_PARAMETERS:
            resolved[key] = require_count(value, key)
        elif key in COUNT_SEQUENCES:
            resolved[key] = require_counts(value, key)
        elif key == "include_center":
            if not isinstance(value, (bool, np.bool_)):
                raise ValueError("include_center must be a Python or NumPy bool scalar.")
            resolved[key] = bool(value)
        elif generator_type in RING_TYPES:
            # Match M1's float-convertible ring input contract before hashing.
            if key in {"ring_radii", "ring_offsets"}:
                resolved[key] = [require_finite(float(item), key) for item in value]
            else:
                resolved[key] = require_finite(float(value), key)
        else:
            resolved[key] = require_finite(value, key)
    return resolved


def _complete_snapshot(value: dict) -> dict:
    """Validate completeness before numeric canonicalization can hide bad counts."""
    expected = {"schema_version", "layout_type", "generator_type", "N",
                "geometry", "layout_parameters", "symmetry_tolerance", "units"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Incomplete or unknown case specification fields.")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported case specification schema_version.")
    if value["units"] != UNITS:
        raise ValueError("M2 specifications require mm / rad / dimensionless counts.")
    if not isinstance(value["geometry"], dict) or set(value["geometry"]) != set(asdict(GeometryConfig())):
        raise ValueError("Saved geometry must contain all resolved fields.")
    config = GeometryConfig(**value["geometry"])
    N = require_count(value["N"], "N")
    require_tolerance(value["symmetry_tolerance"], "symmetry_tolerance")
    layout_type = value["layout_type"]
    if not isinstance(layout_type, str):
        raise ValueError("layout_type must be a string.")
    generator_type = BASELINE_GENERATORS.get(layout_type, layout_type)
    if generator_type not in BUILTIN_TYPES or generator_type != value["generator_type"]:
        raise ValueError("Unsupported or inconsistent layout_type / generator_type.")
    if layout_type in BASELINE_GENERATORS and N != 24:
        raise ValueError("Saved baseline requires N=24.")
    parameters = value["layout_parameters"]
    if not isinstance(parameters, dict):
        raise TypeError("layout_parameters must be a dictionary.")
    resolved = _resolved_parameters(generator_type, N, config, parameters)
    if set(resolved) != set(parameters) or canonical_json(resolved) != canonical_json(parameters):
        raise ValueError("Saved spec is incomplete or is not fully resolved.")
    return value


@dataclass(frozen=True)
class CaseSpec:
    """Immutable JSON snapshot; use create() or from_normalized().

    Identity is a specification identity, not a coordinate-equivalence class.
    It includes constraint/evaluation tolerances and the specification schema,
    but excludes Git, run metadata, filesystem paths and legacy candidate IDs.
    """

    _canonical: str

    def __post_init__(self):
        snapshot = _complete_snapshot(json.loads(self._canonical))
        object.__setattr__(self, "_canonical", canonical_json(snapshot))

    @classmethod
    def create(cls, layout_type: str, N: int, config: GeometryConfig,
               parameters: dict | None = None, *, symmetry_tolerance=1e-6):
        N = require_count(N, "N")
        require_tolerance(symmetry_tolerance, "symmetry_tolerance")
        if not isinstance(layout_type, str):
            raise ValueError("layout_type must be a string.")
        generator_type = BASELINE_GENERATORS.get(layout_type, layout_type)
        if generator_type not in BUILTIN_TYPES:
            raise ValueError(f"M2 schema supports built-in layouts only: {layout_type!r}")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise TypeError("parameters must be a dictionary.")
        if layout_type in BASELINE_GENERATORS:
            if N != 24:
                raise ValueError(f"{layout_type} is a fixed N=24 baseline.")
            if parameters:
                raise ValueError("Baseline parameters are fixed; use the generic layout to customize.")
            parameters = BASELINE_LAYOUT_PARAMETERS[layout_type]
        resolved = _resolved_parameters(generator_type, N, config, parameters)
        return cls(canonical_json({
            "schema_version": SCHEMA_VERSION,
            "layout_type": layout_type, "generator_type": generator_type,
            "N": N, "geometry": asdict(config), "layout_parameters": resolved,
            "symmetry_tolerance": symmetry_tolerance, "units": UNITS,
        }))

    @classmethod
    def from_normalized(cls, value: dict):
        """Require a complete saved v1 spec; never silently fill missing defaults."""
        # Preserve numeric types until __post_init__ validates integer contracts.
        return cls(json.dumps(json_value(value), ensure_ascii=False, allow_nan=False))

    @property
    def normalized_spec(self) -> dict:
        return json.loads(self._canonical)

    @property
    def case_id(self) -> str:
        return "case_v1_" + hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()

    def generate(self):
        # If a future generator adds parameters, fail explicitly rather than
        # silently adopting new defaults for an existing snapshot.
        spec = _complete_snapshot(self.normalized_spec)
        return generate_layout(spec["generator_type"], spec["N"],
                               **spec["geometry"], **spec["layout_parameters"])
