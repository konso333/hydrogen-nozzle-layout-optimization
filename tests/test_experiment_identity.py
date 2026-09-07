"""M2 identity, normalization, provenance and legacy serialization contracts."""

import json
import subprocess
import sys
from decimal import Decimal

import numpy as np
import pytest

from config import GeometryConfig, PROJECT_ROOT
from experiments import provenance
from experiments.archive import CaseRequest
from experiments.spec import CaseSpec, canonical_json
from io_utils import export_summary
from json_values import json_value
from optimization.layout_search import LayoutSpec, search_layouts_for_n


def ring_spec(**changes):
    inputs = dict(layout_type="ring", N=24, config=GeometryConfig(),
                  parameters={"ring_radii": [25, 48], "points_per_ring": [8, 16]})
    inputs.update(changes)
    return CaseSpec.create(**inputs)


@pytest.mark.parametrize("radius", [55, 55.0, 5.5e1, np.int64(55), np.float64(55)])
def test_equal_numeric_geometry_has_same_identity(radius):
    spec = ring_spec(config=GeometryConfig(R=radius))
    assert spec.case_id == ring_spec().case_id
    assert spec.normalized_spec == ring_spec().normalized_spec


@pytest.mark.parametrize("field,value", [
    ("R", 56), ("d", 3), ("s_min", 9), ("tolerance", 1e-8),
])
def test_each_geometry_field_changes_identity(field, value):
    assert ring_spec(config=GeometryConfig(**{field: value})).case_id != ring_spec().case_id


@pytest.mark.parametrize("changes", [
    {"ring_radii": [26, 48]}, {"ring_radii": [48, 25]},
    {"points_per_ring": [16, 8]}, {"angular_offset": 0.1},
    {"ring_offsets": [0, 0.1]},
])
def test_ring_parameters_and_semantic_list_order_change_identity(changes):
    parameters = {"ring_radii": [25, 48], "points_per_ring": [8, 16], **changes}
    assert ring_spec(parameters=parameters).case_id != ring_spec().case_id


def test_n_layout_type_sector_and_evaluation_tolerance_change_identity():
    assert ring_spec(N=25, parameters={"ring_radii": [25, 48],
                                    "points_per_ring": [8, 17]}).case_id != ring_spec().case_id
    assert ring_spec(layout_type="nonuniform_ring").case_id != ring_spec().case_id
    arguments = dict(num_sectors=4, points_per_sector=6, inner_radius=18,
                     outer_radius=46, sector_angle=0.5, radial_levels=3)
    first = CaseSpec.create("sector", 24, GeometryConfig(), arguments)
    for field, value in (("sector_angle", 0.6), ("num_sectors", 6),
                         ("radial_levels", 2), ("inner_radius", 19)):
        assert CaseSpec.create("sector", 24, GeometryConfig(),
                               {**arguments, field: value}).case_id != first.case_id
    changed = CaseSpec.create("ring", 24, GeometryConfig(),
                              {"ring_radii": [25, 48], "points_per_ring": [8, 16]},
                              symmetry_tolerance=1e-5)
    assert changed.case_id != ring_spec().case_id


def test_defaults_numpy_decimal_strings_and_key_order_resolve_identically():
    explicit = {"include_center": np.bool_(False), "angular_offset": -0.0,
                "ring_offsets": None, "points_per_ring": (np.int32(8), np.int64(16)),
                "ring_radii": (Decimal("25"), "4.8e1")}
    assert ring_spec(parameters=explicit).case_id == ring_spec().case_id
    assert ring_spec(parameters=dict(reversed(list(explicit.items())))).case_id == ring_spec().case_id
    assert canonical_json({"z": 55.0, "a": [-0.0, np.int64(2)]}) == '{"a":[0,2],"z":55}'


def test_small_float_changes_are_not_rounded_away():
    changed = ring_spec(config=GeometryConfig(R=np.nextafter(55.0, 56.0)))
    assert changed.case_id != ring_spec().case_id
    assert ring_spec(config=GeometryConfig(R=55.0000000001)).case_id != ring_spec().case_id


def test_spec_snapshot_cannot_be_changed_through_input_or_output_dict():
    parameters = {"ring_radii": [25, 48], "points_per_ring": [8, 16]}
    spec = ring_spec(parameters=parameters)
    original = spec.case_id
    parameters["ring_radii"][0] = 30
    returned = spec.normalized_spec
    returned["geometry"]["R"] = 100
    assert spec.case_id == original
    assert spec.normalized_spec["layout_parameters"]["ring_radii"] == [25, 48]


def test_direct_json_snapshot_uses_canonical_identity():
    spec = ring_spec()
    differently_formatted = json.dumps(spec.normalized_spec, indent=4)
    assert CaseSpec(differently_formatted).case_id == spec.case_id


@pytest.mark.parametrize("field", ["angular_offset", "ring_offsets", "include_center"])
def test_direct_json_requires_complete_defaults(field):
    data = ring_spec().normalized_spec
    data["layout_parameters"].pop(field)
    with pytest.raises(ValueError, match="incomplete"):
        CaseSpec(json.dumps(data))


def test_import_search_and_spec_preserves_matplotlib_backend():
    # A fresh interpreter prevents earlier test imports from masking side effects.
    code = """
import sys
import matplotlib
matplotlib.use('svg')
import optimization.layout_search
import experiments.spec
assert matplotlib.get_backend().lower() == 'svg'
assert 'matplotlib.pyplot' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", [np.int32(5), np.int64(5), np.uint64(5),
                                   np.float32(0.5), np.float64(0.5), np.bool_(True),
                                   (np.int64(3), {"nested": [np.float64(0.5)]}),
                                   Decimal("0.5")])
def test_supported_values_write_as_standard_json(value):
    converted = json_value({"value": value}, canonical_numbers=True)
    assert json.loads(json.dumps(converted, allow_nan=False)) == converted


@pytest.mark.parametrize("value", [object(), {1: "wrong key"}, {1, 2}, np.array([1]),
                                   complex(1, 2)])
def test_unsupported_values_are_not_stringified(value):
    with pytest.raises(TypeError):
        json_value(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                   np.float64("nan"), Decimal("Infinity")])
def test_nonfinite_spec_values_rejected_and_reports_explicitly_nullable(value):
    with pytest.raises(ValueError):
        canonical_json({"nested": [value]})
    assert json_value({"nested": [value]}, nonfinite="null") == {"nested": [None]}


@pytest.mark.parametrize("value", [24.0, True, np.float64(24), "24"])
def test_normalization_does_not_bypass_m1_count_contract(value):
    with pytest.raises(ValueError):
        ring_spec(N=value)
    with pytest.raises(ValueError):
        ring_spec(parameters={"ring_radii": [25], "points_per_ring": [value]})
    data = ring_spec().normalized_spec
    data["N"] = value.item() if isinstance(value, np.generic) else value
    with pytest.raises(ValueError):
        CaseSpec(json.dumps(data))
    with pytest.raises(ValueError):
        CaseSpec.from_normalized(data)


@pytest.mark.parametrize("mutation", [
    lambda data: data.pop("units"),
    lambda data: data.update(schema_version=2),
    lambda data: data.update(schema_version=True),
    lambda data: data["units"].update(length="m"),
    lambda data: data["geometry"].pop("tolerance"),
    lambda data: data["layout_parameters"].pop("angular_offset"),
    lambda data: data["layout_parameters"].update(unknown_parameter=3),
    lambda data: data.update(generator_type="sector"),
])
def test_saved_spec_requires_complete_consistent_versioned_fields(mutation):
    data = ring_spec().normalized_spec
    mutation(data)
    with pytest.raises((ValueError, TypeError)):
        CaseSpec.from_normalized(data)


def test_legacy_id_can_repeat_while_case_identity_changes():
    cases = []
    for radius in (55, 56):
        config = GeometryConfig(R=radius)
        candidate = search_layouts_for_n(24, config, [
            LayoutSpec("ring", {"ring_radii": [25, 48], "points_per_ring": [8, 16]})])[0]
        cases.append(CaseRequest.from_candidate(candidate, config))
    assert cases[0].legacy_candidate_id == cases[1].legacy_candidate_id
    assert cases[0].spec.case_id != cases[1].spec.case_id


def test_candidate_bridge_rejects_stale_coordinates_and_metrics():
    config = GeometryConfig()
    candidate = search_layouts_for_n(24, config, [LayoutSpec("rectangular", {"spacing": 8})])[0]
    original = candidate.points.copy()
    candidate.points.reverse()
    with pytest.raises(ValueError, match="coordinates"):
        CaseRequest.from_candidate(candidate, config)
    candidate.points = original
    candidate.metrics["uniformity_score"] = 0
    with pytest.raises(ValueError, match="metrics"):
        CaseRequest.from_candidate(candidate, config)


def test_legacy_summary_numpy_decimal_fix_and_native_json_bytes(tmp_path):
    config = GeometryConfig()
    parameters = {"ring_radii": [25.0, 48.0], "points_per_ring": [8, 16]}
    native = search_layouts_for_n(24, config, [LayoutSpec("ring", parameters)])[0]
    native_json = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
    assert native.summary_row()["layout_parameters"] == native_json
    rich_parameters = {"ring_radii": [Decimal("25"), np.float64(48)],
                       "points_per_ring": [np.int64(8), np.int32(16)]}
    rich = search_layouts_for_n(24, config, [LayoutSpec("ring", rich_parameters)])[0]
    assert rich.summary_row()["layout_parameters"] == native_json
    assert list(rich.summary_row()) == list(native.summary_row())
    first = export_summary([native.summary_row()], tmp_path / "native.csv")
    second = export_summary([rich.summary_row()], tmp_path / "numpy.csv")
    assert first.read_bytes() == second.read_bytes()
    path = export_summary([{"layout_parameters": rich_parameters}], tmp_path / "nested.csv")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    # Existing native JSON behavior, including numeric mapping keys, must not
    # be tightened by the new archive's stricter specification normalizer.
    native.layout_parameters = {"nested": {7: [1.0, "value"]}}
    assert native.summary_row()["layout_parameters"] == json.dumps(
        native.layout_parameters, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("dirty", [False, True])
def test_provenance_clean_dirty_and_safe_git_commands(monkeypatch, dirty):
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        assert kwargs["timeout"] == 5 and kwargs["check"]
        assert not kwargs.get("shell", False)
        result = "abc123\n" if args[-1] == "HEAD" and "--abbrev-ref" not in args else "test-branch\n"
        if "status" in args:
            result = " M source.py\n" if dirty else ""
        return subprocess.CompletedProcess(args, 0, result, "")
    monkeypatch.setattr(provenance.subprocess, "run", run)
    result = provenance.collect_provenance()
    assert result["git"] == {"status": "available", "commit": "abc123", "branch": "test-branch",
                              "dirty": dirty, "working_tree": "dirty" if dirty else "clean"}
    assert result["python_version"] and result["platform"]
    assert all(result["dependencies"][key] for key in ("numpy", "pandas", "matplotlib"))
    assert len(commands) == 3


@pytest.mark.parametrize("error", [FileNotFoundError(), PermissionError(),
                                   subprocess.CalledProcessError(128, "git"),
                                   subprocess.TimeoutExpired("git", 5)])
def test_unavailable_git_is_explicit_and_nonfatal(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(provenance.subprocess, "run", fail)
    result = provenance.collect_provenance()
    assert result["git"]["status"] == "unavailable"
    assert result["git"]["commit"] is None and result["git"]["dirty"] is None
    assert result["python_version"]
