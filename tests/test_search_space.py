"""M3 design, preflight, identity, execution and archive acceptance tests."""

import copy
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from config import GeometryConfig
from experiments import batch
from experiments.archive import verify_run
from experiments.search_space import ExperimentSearchSpace, default_search_space, n_values
from experiments.spec import CaseSpec
from geometry.constraints import LayoutConstraintError
from layouts import UnknownLayoutTypeError
from optimization.layout_search import default_layout_specs
from optimization.search_n import search_variable_n
from validation import InputValidationError


def design():
    return json.loads((Path(__file__).resolve().parents[1] / "examples/m3_small_search.json")
                      .read_text(encoding="utf-8"))


def group(data):
    return data["blocks"][0]["groups"][0]


def ids(space):
    return [request.spec.case_id for request in space.plan().requests]


def test_default_exact_order_identity_coordinates_and_results():
    space = default_search_space()
    plan = space.plan()
    assert (plan.planned, plan.unique, plan.duplicates) == (426, 426, 0)
    expected = []
    config = GeometryConfig()
    for n in range(12, 41):
        for i, spec in enumerate(default_layout_specs(n, config), 1):
            expected.append((CaseSpec.create(spec.layout_type, n, config, spec.parameters),
                             f"N{n:03d}_{spec.layout_type}_{i:03d}"))
    assert [(r.spec, r.legacy_candidate_id) for r in plan.requests] == expected
    assert Counter(r.spec.normalized_spec["layout_type"] for r in plan.requests) == {
        "rectangular": 116, "hexagonal": 116, "ring": 29, "staggered_ring": 29,
        "nonuniform_ring": 29, "sector": 44, "radial_spoke": 34, "deterministic_irregular": 29}
    old, _, old_pareto = search_variable_n(12, 40, config)
    by_legacy = {c.candidate_id: c for c in old}
    for request in plan.requests:
        if request.legacy_candidate_id not in by_legacy:
            with pytest.raises(LayoutConstraintError):
                request.spec.generate()
        else:
            np.testing.assert_array_equal(request.spec.generate(), by_legacy[request.legacy_candidate_id].points)
    report = batch.run_batch(space)
    assert (report["planned"], report["feasible"], report["infeasible"], report["pareto_count"]) == (426, 415, 11, 18)
    assert [row["legacy_candidate_id"] for row in report["rows"] if row["pareto_candidate"]] == [
        row["candidate_id"] for row in old_pareto]


@pytest.mark.parametrize("value,expected", [(4, [4]), ([7, 4], [7, 4]),
    ({"start": 3, "stop": 5}, [3, 4, 5]), ({"start": 3, "stop": 7, "step": 2}, [3, 5, 7])])
def test_n_forms(value, expected):
    assert n_values(value) == expected
    data = design()
    group(data)["N"] = value
    space = ExperimentSearchSpace.from_dict(data)
    assert [r.spec.normalized_spec["N"] for r in space.plan().requests][:len(expected)] == expected


@pytest.mark.parametrize("value", [True, False, 4.0, 4.9, 0, -1, "4", None, [], [4, True],
    {"start": 4, "stop": 3}, {"start": 4.0, "stop": 5}, {"start": 3, "stop": True},
    {"start": 3, "stop": 5, "step": 0}, {"start": 3, "stop": 5, "step": 1.0},
    {"start": 3}, {"start": 3, "stop": 5, "extra": 1}])
def test_invalid_n(value):
    data = design()
    group(data)["N"] = value
    with pytest.raises(InputValidationError):
        ExperimentSearchSpace.from_dict(data)


def test_geometry_product_identity_and_count_without_generation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("planning must not generate coordinates")
    monkeypatch.setattr(CaseSpec, "generate", forbidden)
    data = design()
    data["blocks"][0]["geometry_space"].update(d=[3, 4], s_min=[8, 9], tolerance=[1e-9, 1e-8])
    space = ExperimentSearchSpace.from_dict(data)
    plan = space.plan()
    assert (plan.planned, plan.unique) == (64, 64)
    assert len(set(ids(space))) == 64
    for request in plan.requests:
        p = request.spec.normalized_spec
        assert request.spec == CaseSpec.create(p["layout_type"], p["N"], GeometryConfig(**p["geometry"]),
                                               p["layout_parameters"])


@pytest.mark.parametrize("key,values", [("R", [0]), ("R", [float("inf")]), ("R", [True]),
    ("d", [-1]), ("d", [200]), ("s_min", [-1]), ("tolerance", [-1]), ("R", []), ("R", 55)])
def test_invalid_geometry(key, values):
    data = design()
    data["blocks"][0]["geometry_space"][key] = values
    with pytest.raises((InputValidationError, ValueError)):
        ExperimentSearchSpace.from_dict(data)


@pytest.mark.parametrize("kind,n,parameters", [
    ("rectangular", 12, {"spacing": 12, "rows": 3, "columns": 4}),
    ("hexagonal", 12, {"spacing": 12, "row_counts": [3, 4, 5]}),
    ("ring", 12, {"ring_radii": [30], "points_per_ring": [12]}),
    ("staggered_ring", 12, {"ring_radii": [20, 40], "points_per_ring": [4, 8], "delta_theta": 0.2}),
    ("nonuniform_ring", 12, {"points_per_ring": [4, 8], "inner_radius": 20, "outer_radius": 40}),
    ("sector", 12, {"num_sectors": 4, "points_per_sector": 3, "inner_radius": 20,
                    "outer_radius": 40, "sector_angle": 0.3, "radial_levels": 3}),
    ("radial_spoke", 12, {"num_spokes": 4, "points_per_spoke": 3, "inner_radius": 20, "outer_radius": 40}),
    ("deterministic_irregular", 12, {"inner_radius": 0, "outer_radius": 45}),
])
def test_family_parameter_sets(kind, n, parameters):
    data = design()
    data["blocks"][0]["groups"] = [{"N": n, "layout_type": kind, "parameter_sets": [parameters]}]
    plan = ExperimentSearchSpace.from_dict(data).plan()
    assert plan.planned == 2
    assert plan.requests[0].spec == CaseSpec.create(kind, n, GeometryConfig(R=50), parameters)


@pytest.mark.parametrize("kind,parameters", [
    ("rectangular", {"spacing": 12, "ring_radii": [20]}),
    ("rectangular", {}), ("rectangular", {"spacing": 0}),
    ("rectangular", {"spacing": None}),
    ("rectangular", {"spacing": 12, "rows": 3}),
    ("rectangular", {"spacing": 12, "rows": 2, "columns": 2}),
    ("rectangular", {"spacing": 12, "rows": 3.0, "columns": 4}),
    ("hexagonal", {"spacing": 12, "row_counts": [3, 4]}),
    ("ring", {"ring_radii": [20], "points_per_ring": [4]}),
    ("ring", {"ring_radii": None, "points_per_ring": [12]}),
    ("ring", {"ring_radii": [20], "points_per_ring": None}),
    ("ring", {"ring_radii": [20], "points_per_ring": [12], "include_center": None}),
    ("ring", {"ring_radii": [20, 40], "points_per_ring": [12]}),
    ("ring", {"ring_radii": [-20], "points_per_ring": [12]}),
    ("ring", {"ring_radii": [0], "points_per_ring": [12]}),
    ("ring", {"ring_radii": [20], "points_per_ring": [12], "ring_offsets": [0, 1]}),
    ("nonuniform_ring", {"points_per_ring": [4, 8]}),
    ("nonuniform_ring", {"points_per_ring": [4, 8], "inner_radius": 20, "outer_radius": 10}),
    ("nonuniform_ring", {"points_per_ring": [4, 8], "inner_radius": 20, "outer_radius": 40, "radial_exponent": 0}),
    ("nonuniform_ring", {"points_per_ring": [1, 11], "inner_radius": 0, "outer_radius": 0}),
    ("sector", {"num_sectors": 4, "points_per_sector": 3, "inner_radius": 20,
                "outer_radius": 40, "sector_angle": 2}),
    ("radial_spoke", {"num_spokes": 4, "points_per_spoke": 3, "inner_radius": 0, "outer_radius": 40}),
    ("deterministic_irregular", {"inner_radius": 0, "outer_radius": 40, "radial_exponent": 0}),
])
def test_family_invalid_preflight(kind, parameters, monkeypatch):
    monkeypatch.setattr(CaseSpec, "generate", lambda *_: pytest.fail("must fail before generation"))
    data = design()
    # Deliberately put the invalid request AFTER valid groups.
    data["blocks"][0]["groups"].append({"N": 12, "layout_type": kind, "parameter_sets": [parameters]})
    with pytest.raises(InputValidationError):
        batch.run_batch(ExperimentSearchSpace.from_dict(data))


def test_unknown_layout():
    data = design()
    group(data)["layout_type"] = "unknown"
    with pytest.raises(UnknownLayoutTypeError):
        ExperimentSearchSpace.from_dict(data)


def test_duplicates_normalized_identity_and_sources():
    data = design()
    duplicate = copy.deepcopy(group(data))
    duplicate["parameter_sets"] = [{"spacing": 12.0, "rows": None, "columns": None}]
    data["blocks"][0]["groups"].append(duplicate)
    space = ExperimentSearchSpace.from_dict(data)
    plan = space.plan()
    assert (plan.planned, plan.unique, plan.duplicates) == (12, 8, 4)
    assert sum(len(paths) for paths in plan.sources.values()) == 12
    assert all(len(paths) in (1, 2) for paths in plan.sources.values())
    report = batch.run_batch(space)
    assert (report["planned"], report["unique"], report["duplicates"], report["feasible"]) == (12, 8, 4, 8)


def test_json_roundtrip_key_order_and_list_order(tmp_path):
    space = default_search_space()
    path = tmp_path / "search.json"
    space.save(path)
    assert ids(space) == ids(ExperimentSearchSpace.load(path)) == ids(space)
    def reverse_keys(value):
        if isinstance(value, dict):
            return {k: reverse_keys(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reverse_keys(v) for v in value]
        return value
    assert ids(space) == ids(ExperimentSearchSpace.from_dict(reverse_keys(space.to_dict())))
    data = design()
    original = ids(ExperimentSearchSpace.from_dict(data))
    group(data)["N"].reverse()
    changed = ids(ExperimentSearchSpace.from_dict(data))
    assert set(original) == set(changed) and original != changed
    detached = space.to_dict()
    detached["blocks"].clear()
    assert space.planned_count == 426


@pytest.mark.parametrize("error", [InputValidationError("bad"), UnknownLayoutTypeError("bad"),
                                  RuntimeError("bug"), TypeError("bug"), ValueError("bug")])
def test_generation_errors_propagate(monkeypatch, error):
    space = ExperimentSearchSpace.from_dict(design())
    def fail(*args):
        raise error
    monkeypatch.setattr(CaseSpec, "generate", fail)
    with pytest.raises(type(error)) as caught:
        batch.run_batch(space)
    assert caught.value is error


def test_evaluation_constraint_error_is_not_swallowed(monkeypatch):
    def fail(*args, **kwargs):
        raise LayoutConstraintError("evaluation bug")
    monkeypatch.setattr(batch, "evaluate_geometry", fail)
    with pytest.raises(LayoutConstraintError, match="evaluation bug"):
        batch.run_batch(ExperimentSearchSpace.from_dict(design()))


def test_all_infeasible_and_no_archive(tmp_path):
    data = design()
    for g in data["blocks"][0]["groups"]:
        g["parameter_sets"] = [{"spacing": 1000}]
    report = batch.run_batch(ExperimentSearchSpace.from_dict(data), output_root=tmp_path / "runs")
    assert (report["planned"], report["feasible"], report["infeasible"]) == (8, 0, 8)
    assert report["run_id"] is None and report["archive_status"] == "no_feasible_cases"
    assert len(report["infeasible_cases"]) == 8
    assert not (tmp_path / "runs").exists()
    batch.save_report(report, tmp_path / "report.json")


def test_batch_archive_verify_and_replay(tmp_path):
    data = design()
    space = ExperimentSearchSpace.from_dict(data)
    report = batch.run_batch(space, output_root=tmp_path)
    assert (report["planned"], report["unique"], report["feasible"], report["infeasible"]) == (8, 8, 8, 0)
    directory = tmp_path / report["run_id"]
    assert verify_run(directory / "run.json")["overall_match"]
    assert json.loads((directory / "search_report.json").read_text(encoding="utf-8")) == report
    assert ids(ExperimentSearchSpace.load(directory / "search_space.json")) == report["case_ids"]
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert run["planned_case_count"] == 8
    assert [entry["case_id"] for entry in run["cases"]] == report["case_ids"]


def test_pareto_disabled_and_single_nozzle_null():
    data = design()
    group(data)["N"] = 1
    data["blocks"][0]["groups"] = [group(data)]
    report = batch.run_batch(ExperimentSearchSpace.from_dict(data), pareto=False)
    assert report["pareto_count"] is None
    assert "pareto_candidate" not in report["rows"][0]
    json.dumps(report, allow_nan=False)
    assert batch.run_batch(ExperimentSearchSpace.from_dict(data))["pareto_count"] == 0


def test_duplicate_legacy_labels_sources_roundtrip_and_single_execution(tmp_path, monkeypatch):
    data = design()
    first = {"N": 12, "layout_type": "rectangular", "parameter_sets": [{"spacing": 12}],
             "legacy_candidate_id": "source_A"}
    second = {"N": 12, "layout_type": "rectangular",
              "parameter_sets": [{"spacing": 12.0, "rows": None, "columns": None}],
              "legacy_candidate_id": "source_B"}
    data["blocks"][0]["geometry_space"]["R"] = [55]
    data["blocks"][0]["groups"] = [first, second]
    space = ExperimentSearchSpace.from_dict(data)
    plan = space.plan()
    assert (plan.planned, plan.unique, plan.duplicates) == (2, 1, 1)
    case_id = plan.requests[0].spec.case_id
    expected_sources = [
        "blocks[0]/geometry[0]/groups[0]/N[0]/parameter_sets[0]",
        "blocks[0]/geometry[0]/groups[1]/N[0]/parameter_sets[0]",
    ]
    assert plan.sources == {case_id: expected_sources}
    path = tmp_path / "search_space.json"
    space.save(path)
    loaded = ExperimentSearchSpace.load(path)
    replay = loaded.plan()
    assert (replay.planned, replay.unique) == (plan.planned, plan.unique)
    assert ids(loaded) == ids(space)
    assert replay.sources == plan.sources
    assert [g["legacy_candidate_id"] for g in loaded.to_dict()["blocks"][0]["groups"]] == [
        "source_A", "source_B"]
    assert loaded.to_dict() == data
    # JSON key order cannot change the source paths or retained first label.
    reordered = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=lambda pairs: dict(reversed(pairs)))
    assert ExperimentSearchSpace.from_dict(reordered).plan() == replay
    calls = []
    generate = CaseSpec.generate
    def tracked_generate(spec):
        calls.append(spec.case_id)
        return generate(spec)
    monkeypatch.setattr(CaseSpec, "generate", tracked_generate)
    report = batch.run_batch(loaded)
    assert calls == [case_id]
    assert report["sources"] == plan.sources
    assert report["rows"][0]["legacy_candidate_id"] == "source_A"


def test_mixed_archive_contains_exactly_feasible_rows(tmp_path):
    data = design()
    data["blocks"][0]["geometry_space"]["R"] = [55]
    data["blocks"][0]["groups"] = [{
        "N": 12, "layout_type": "rectangular",
        "parameter_sets": [{"spacing": 12}, {"spacing": 1000}, {"spacing": 12.0}],
    }]
    report = batch.run_batch(ExperimentSearchSpace.from_dict(data), output_root=tmp_path)
    assert (report["planned"], report["unique"], report["duplicates"],
            report["feasible"], report["infeasible"]) == (3, 2, 1, 1, 1)
    assert report["planned"] == report["unique"] + report["duplicates"]
    assert report["unique"] == report["feasible"] + report["infeasible"]
    directory = tmp_path / report["run_id"]
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    archived_ids = [case["case_id"] for case in run["cases"]]
    feasible_ids = [row["case_id"] for row in report["rows"]]
    assert archived_ids == feasible_ids
    assert archived_ids != report["case_ids"]
    assert set(report["case_ids"]) - set(archived_ids) == {
        case["case_id"] for case in report["infeasible_cases"]}
    assert run["planned_case_count"] == run["case_count"] == report["feasible"]
    assert sorted(p.name for p in (directory / "cases").iterdir()) == sorted(archived_ids)
    assert verify_run(directory / "run.json")["overall_match"]


def test_multiple_blocks_preserve_pairs_and_share_global_pareto():
    data = design()
    blocks = []
    for radius, diameter, spacing in [(50, 3, 10), (60, 4, 20)]:
        blocks.append({
            "geometry_space": {"R": [radius], "d": [diameter], "s_min": [8], "tolerance": [1e-9]},
            "groups": [{"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": spacing}]}],
        })
    data["blocks"] = blocks
    space = ExperimentSearchSpace.from_dict(data)
    plan = space.plan()
    assert plan.planned == plan.unique == 2
    assert [(r.spec.normalized_spec["geometry"]["R"], r.spec.normalized_spec["geometry"]["d"])
            for r in plan.requests] == [(50, 3), (60, 4)]
    report = batch.run_batch(space)
    assert report["feasible"] == 2
    assert report["pareto_scope"] == "all_unique_feasible_cases"
    assert [row["case_id"] for row in report["rows"]] == ids(space)
    # Same N and perfect grid uniformity; the second block's larger spacing
    # dominates the first only when both blocks share the comparison pool.
    assert [row["pareto_candidate"] for row in report["rows"]] == [False, True]
    for block in blocks:
        single = copy.deepcopy(data)
        single["blocks"] = [block]
        assert batch.run_batch(ExperimentSearchSpace.from_dict(single))["pareto_count"] == 1


def test_cli_help_explains_snapshot_global_pareto_and_verification(monkeypatch, capsys):
    from scripts import run_search_experiment as cli
    monkeypatch.setattr(sys, "argv", ["run_search_experiment.py", "--help"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    for phrase in ("explicit experiment design snapshot", "does not automatically recompute",
                   "does not scale ring_radii", "default_layout_specs()", "Edit parameter_sets",
                   "Pareto is global", "not separate Pareto groups", "M2 archive verification",
                   "not the full M3 semantics"):
        assert phrase in help_text


def test_cli_execution_reports_scope_and_m2_verification(tmp_path, monkeypatch, capsys):
    from scripts import run_search_experiment as cli
    data = design()
    data["blocks"][0]["geometry_space"]["R"] = [55]
    data["blocks"][0]["groups"] = [{
        "N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 12}],
    }]
    config = tmp_path / "design.json"
    ExperimentSearchSpace.from_dict(data).save(config)
    archive_root = tmp_path / "runs"
    monkeypatch.setattr(sys, "argv", ["run_search_experiment.py", "--config", str(config),
                                      "--execute", "--archive-root", str(archive_root)])
    cli.main()
    lines = capsys.readouterr().out.splitlines()
    result = json.loads(lines[1])
    assert result["pareto_scope"] == "all_unique_feasible_cases"
    assert result["pareto_count"] == 1
    assert "M2 archive verification (verify_run): True" in lines
    run = json.loads((archive_root / result["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "completed" and run["case_count"] == 1
