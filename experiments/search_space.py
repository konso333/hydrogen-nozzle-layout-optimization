"""Validated, deterministic experiment designs; expanded cases remain M2 CaseSpec."""

from __future__ import annotations

import inspect
import json
import math
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path

from config import GeometryConfig
from experiments.archive import CaseRequest
from experiments.spec import BUILTIN_TYPES, CaseSpec
from json_values import json_value
from layouts import LAYOUT_REGISTRY, UnknownLayoutTypeError
from layouts.ring import _validate_ring_inputs
from optimization.layout_search import default_layout_specs
from validation import InputValidationError, require_count, require_tolerance


GEOMETRY_KEYS = ("R", "d", "s_min", "tolerance")


def _fields(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= set(value)
            or set(value) - set(required) - set(optional)):
        raise InputValidationError(f"Expected fields {required}; optional {optional}.")


def _sequence(value, label):
    if not isinstance(value, (list, tuple)) or not value:
        raise InputValidationError(f"{label} must be a nonempty ordered list.")
    return value


def n_values(value):
    """A single count, ordered discrete counts, or inclusive {start, stop, step}."""
    if isinstance(value, dict):
        _fields(value, ("start", "stop"), ("step",))
        start = require_count(value["start"], "N.start")
        stop = require_count(value["stop"], "N.stop")
        step = require_count(value.get("step", 1), "N.step")
        if stop < start:
            raise InputValidationError("N.stop must be >= N.start.")
        return list(range(start, stop + 1, step))
    if isinstance(value, (list, tuple)):
        return [require_count(n, "N") for n in _sequence(value, "N")]
    return [require_count(value, "N")]


def _parameter_contract(spec):
    """Preflight M1 parameter relationships without constructing coordinates.

    Scalar/count contracts and signature defaults are resolved by CaseSpec.
    These checks mirror the existing family relationships, not geometry tests.
    """
    data = spec.normalized_spec
    kind, n, p = data["layout_type"], data["N"], data["layout_parameters"]

    def check(condition, message):
        if not condition:
            raise InputValidationError(f"{kind}: {message}")

    if kind in {"rectangular", "hexagonal"}:
        check(p["spacing"] > 0, "spacing must be positive.")
    if kind == "cross_5":
        check(n == 5, "N must equal 5.")
        check(p["pitch"] > 0, "pitch must be positive.")
    if kind == "rectangular":
        rows, columns = p["rows"], p["columns"]
        check((rows is None) == (columns is None), "rows and columns must be paired.")
        if rows is not None:
            check(rows * columns == n, "rows * columns must equal N.")
    if kind == "hexagonal" and p["row_counts"] is not None:
        check(sum(p["row_counts"]) == n, "row_counts must sum to N.")
    if kind in {"ring", "staggered_ring", "nonuniform_ring"}:
        counts, radii = p["points_per_ring"], p["ring_radii"]
        check(counts is not None, "points_per_ring is required.")
        check(sum(counts) + int(p["include_center"]) == n, "ring counts plus center must equal N.")
        if radii is None:
            check(kind == "nonuniform_ring", "ring_radii is required.")
            inner, outer = p["inner_radius"], p["outer_radius"]
            check(inner is not None and outer is not None, "provide inner/outer radius.")
            check(0 <= inner <= outer, "require 0 <= inner_radius <= outer_radius.")
            check(p["radial_exponent"] > 0, "radial_exponent must be positive.")
            check(inner != 0 or counts[0] == 1, "zero-radius ring can contain only one point.")
            check(outer != 0 or all(count == 1 for count in counts),
                  "zero-radius rings can contain only one point each.")
        else:
            _validate_ring_inputs(radii, counts)
        if p.get("ring_offsets") is not None:
            check(len(p["ring_offsets"]) == len(counts), "ring_offsets length must match rings.")
    if kind in {"sector", "radial_spoke", "deterministic_irregular"}:
        check(0 <= p["inner_radius"] <= p["outer_radius"], "require 0 <= inner_radius <= outer_radius.")
    if kind == "sector":
        check(p["num_sectors"] * p["points_per_sector"] == n, "sector counts must multiply to N.")
        check(p["radial_levels"] <= p["points_per_sector"], "too many radial_levels.")
        check(0 <= p["sector_angle"] < 2 * math.pi / p["num_sectors"], "invalid sector_angle.")
    if kind == "radial_spoke":
        check(p["num_spokes"] * p["points_per_spoke"] == n, "spoke counts must multiply to N.")
        check(not (p["num_spokes"] > 1 and p["points_per_spoke"] > 1 and p["inner_radius"] == 0),
              "inner_radius must be positive for multiple multi-point spokes.")
    if kind == "deterministic_irregular":
        check(p["radial_exponent"] > 0, "radial_exponent must be positive.")


def _expand(design):
    _fields(design, ("schema_version", "name", "blocks"), ("description", "symmetry_tolerance"))
    if type(design["schema_version"]) is not int or design["schema_version"] != 1:
        raise InputValidationError("Unsupported search-space schema_version.")
    if not isinstance(design["name"], str) or not design["name"].strip():
        raise InputValidationError("name must be nonempty text.")
    if not isinstance(design.get("description", ""), str):
        raise InputValidationError("description must be text.")
    symmetry = design.get("symmetry_tolerance", 1e-6)
    require_tolerance(symmetry, "symmetry_tolerance")
    expanded = []
    for bi, block in enumerate(_sequence(design["blocks"], "blocks")):
        _fields(block, ("geometry_space", "groups"))
        geometry = block["geometry_space"]
        _fields(geometry, GEOMETRY_KEYS)
        axes = [_sequence(geometry[key], key) for key in GEOMETRY_KEYS]
        configs = [GeometryConfig(**dict(zip(GEOMETRY_KEYS, values))) for values in product(*axes)]
        for gi, config in enumerate(configs):
            for li, group in enumerate(_sequence(block["groups"], "groups")):
                _fields(group, ("N", "layout_type", "parameter_sets"), ("legacy_candidate_id",))
                kind = group["layout_type"]
                if not isinstance(kind, str):
                    raise InputValidationError("layout_type must be text.")
                if kind not in BUILTIN_TYPES:
                    raise UnknownLayoutTypeError(f"Unsupported M3 layout_type: {kind!r}")
                counts = n_values(group["N"])
                sets = _sequence(group["parameter_sets"], "parameter_sets")
                legacy = group.get("legacy_candidate_id")
                if legacy is not None and (not isinstance(legacy, str) or not legacy.strip()
                                           or len(counts) != 1 or len(sets) != 1):
                    raise InputValidationError("legacy_candidate_id requires one N and one parameter set.")
                for ni, n in enumerate(counts):
                    for pi, parameters in enumerate(sets):
                        if not isinstance(parameters, dict):
                            raise InputValidationError("Each parameter set must be an object.")
                        # Catch malformed user keywords only at signature binding;
                        # TypeError from runtime code is never classified as infeasibility.
                        if set(parameters) & {"N", *GEOMETRY_KEYS}:
                            raise InputValidationError("Common parameters belong in N / geometry_space.")
                        signature = inspect.signature(LAYOUT_REGISTRY[kind])
                        try:
                            signature.bind(N=n, **asdict(config), **parameters)
                        except TypeError as exc:
                            raise InputValidationError(str(exc)) from exc
                        for key, value in parameters.items():
                            if value is None and signature.parameters[key].default is not None:
                                raise InputValidationError(f"{kind}.{key} cannot be null.")
                        spec = CaseSpec.create(kind, n, config, parameters, symmetry_tolerance=symmetry)
                        _parameter_contract(spec)
                        source = f"blocks[{bi}]/geometry[{gi}]/groups[{li}]/N[{ni}]/parameter_sets[{pi}]"
                        expanded.append((CaseRequest(spec, legacy), source))
    return expanded


@dataclass(frozen=True)
class SearchPlan:
    """Pre-generation plan: M2 requests plus design paths, never runtime results."""

    requests: tuple[CaseRequest, ...]
    sources: dict[str, list[str]]
    planned: int

    @property
    def unique(self):
        return len(self.requests)

    @property
    def duplicates(self):
        return self.planned - self.unique


@dataclass(frozen=True)
class ExperimentSearchSpace:
    """Immutable JSON experiment design, validated before numeric serialization."""

    _json: str

    def __post_init__(self):
        data = json.loads(self._json)
        _expand(data)  # Full preflight, no generator invocation.
        object.__setattr__(self, "_json", json.dumps(data, ensure_ascii=False, sort_keys=True,
                                                    indent=2, allow_nan=False) + "\n")

    @classmethod
    def from_dict(cls, data):
        # Keep 4.0 distinct from 4 until M1 count validation has run.
        return cls(json.dumps(json_value(data), ensure_ascii=False, allow_nan=False))

    @classmethod
    def load(cls, path):
        return cls(Path(path).read_text(encoding="utf-8"))

    def to_dict(self):
        return json.loads(self._json)

    def save(self, path):
        Path(path).write_text(self._json, encoding="utf-8")

    def plan(self):
        requests, sources = {}, {}
        expanded = _expand(self.to_dict())
        for request, source in expanded:
            case_id = request.spec.case_id
            requests.setdefault(case_id, request)
            sources.setdefault(case_id, []).append(source)
        return SearchPlan(tuple(requests.values()), sources, len(expanded))

    @property
    def planned_count(self):
        return self.plan().planned


def default_search_space():
    """Materialize the legacy 426-spec design; saved JSON needs no factory replay."""
    config = GeometryConfig()
    groups = []
    for n in range(12, 41):
        for index, spec in enumerate(default_layout_specs(n, config), start=1):
            groups.append({"N": n, "layout_type": spec.layout_type,
                           "parameter_sets": [spec.parameters],
                           "legacy_candidate_id": f"N{n:03d}_{spec.layout_type}_{index:03d}"})
    return ExperimentSearchSpace.from_dict({
        "schema_version": 1, "name": "M3 legacy default search",
        "description": "Materialized legacy N=12..40, R=55, d=4, s_min=8 design.",
        "symmetry_tolerance": 1e-6,
        "blocks": [{"geometry_space": {key: [value] for key, value in asdict(config).items()},
                    "groups": groups}],
    })
