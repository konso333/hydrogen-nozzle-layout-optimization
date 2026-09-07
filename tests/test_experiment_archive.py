"""Real archive artifacts and parameter-only replay across all built-in families."""

import json
import shutil

import numpy as np
import pytest
from matplotlib import image as mpimg

from config import GeometryConfig
from experiments import archive
from experiments.archive import CaseRequest, archive_run, reproduce_case, verify_case, verify_run
from experiments.spec import CaseSpec
from layouts import BASELINE_LAYOUT_PARAMETERS, generate_layout
from optimization.layout_search import default_layout_specs
from scripts.compare_layouts import COMPARISON_SPECS


CASES = [(kind, params) for _, kind, params in COMPARISON_SPECS] + [
    ("rectangular", {"spacing": 8}), ("hexagonal", {"spacing": 8}),
    ("ring", {"ring_radii": [25, 48], "points_per_ring": [8, 16]}),
    ("nonuniform_ring", {"points_per_ring": [4, 8, 12], "inner_radius": 18,
                         "outer_radius": 50, "radial_exponent": 1.2}),
]


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    cases = [CaseRequest(CaseSpec.create(kind, 24, GeometryConfig(), params),
                         f"legacy-{index}") for index, (kind, params) in enumerate(CASES)]
    path = archive_run(cases, name="All built-in layouts", output_root=tmp_path_factory.mktemp("m2_runs"))
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_run_index_paths_provenance_and_complete_specs(full_run):
    path, run = full_run
    assert run["status"] == "completed"
    assert run["case_count"] == run["planned_case_count"] == len(CASES)
    assert len(run["requested_cases"]) == len(CASES)
    assert run["created_at"].endswith("+00:00")
    assert run["provenance"]["git"]["status"] in {"available", "unavailable"}
    assert verify_run(path)["overall_match"]
    for entry in run["cases"]:
        manifest_path = path.parent / entry["manifest"]
        assert manifest_path.is_relative_to(path.parent)
        case = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert case["case_id"] == entry["case_id"] != run["run_id"]
        assert case["run_id"] == run["run_id"]
        assert case["legacy_candidate_id"] == entry["legacy_candidate_id"]
        assert (manifest_path.parent / case["provenance"]["file"]).resolve() == path.resolve()
        assert case["cfd"]["case_id"] == case["case_id"]
        assert case["cfd"]["run_id"] == run["run_id"]
        assert case["cfd"]["status"] == "not_started"
        assert all(value is None for value in case["cfd"]["metrics"].values())
        assert case["cfd"]["results_file"] is None
        assert case["validation"]["feasible"]
        assert set(case["validation"]).isdisjoint(case["metrics"])
        assert case["units"] == {"length": "mm", "angle": "rad", "count": "1"}


@pytest.mark.parametrize("index", range(len(CASES)), ids=[kind for kind, _ in CASES])
def test_all_families_replay_ordered_coordinates_metrics_csv_and_png(full_run, index):
    path, run = full_run
    case_path = path.parent / run["cases"][index]["manifest"]
    data = json.loads(case_path.read_text(encoding="utf-8"))
    kind, parameters = CASES[index]
    expected = generate_layout(kind, 24, 55, 4, 8, **parameters)
    regenerated = reproduce_case(case_path)
    assert regenerated.spec.case_id == data["case_id"]
    assert len(regenerated.points) == 24
    np.testing.assert_allclose(regenerated.points, expected, rtol=0, atol=1e-9)
    assert regenerated.metrics == data["metrics"]
    assert verify_case(case_path)["overall_match"]
    image = mpimg.imread(case_path.parent / data["files"]["figure"])
    assert image.ndim == 3 and np.isfinite(image).all()
    raw = (case_path.parent / data["files"]["coordinates"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbfnozzle_id,x_mm,y_mm,z_mm")


def test_replay_needs_only_case_manifest_and_saved_baseline_defaults(full_run, tmp_path, monkeypatch):
    from layouts.rectangular import RECTANGULAR_BASELINE_PARAMETERS
    path, run = full_run
    source = path.parent / run["cases"][0]["manifest"]
    isolated = tmp_path / "case.json"
    shutil.copyfile(source, isolated)
    original = reproduce_case(source).points
    monkeypatch.setitem(BASELINE_LAYOUT_PARAMETERS, "A_Rectangular", {"spacing": 999})
    monkeypatch.setitem(RECTANGULAR_BASELINE_PARAMETERS, "spacing", 17)
    assert generate_layout("A_Rectangular", 24, 55, 4, 8) != original
    regenerated = reproduce_case(isolated)
    assert len(regenerated.points) == 24
    assert regenerated.spec.normalized_spec["layout_parameters"]["spacing"] == 18
    np.testing.assert_array_equal(regenerated.points, original)
    assert list(tmp_path.iterdir()) == [isolated]


def test_saved_spec_and_manifest_ignore_changed_generator_default(full_run, monkeypatch):
    from layouts.ring import ring_layout
    path, run = full_run
    index = next(i for i, (kind, _) in enumerate(CASES) if kind == "ring")
    case_path = path.parent / run["cases"][index]["manifest"]
    old = reproduce_case(case_path)
    saved = old.spec.normalized_spec
    assert saved["layout_parameters"]["angular_offset"] == 0
    monkeypatch.setattr(ring_layout, "__kwdefaults__",
                        {**ring_layout.__kwdefaults__, "angular_offset": 0.75})
    assert generate_layout("ring", 24, 55, 4, 8, **CASES[index][1]) != old.points
    for spec in (old.spec, CaseSpec.from_normalized(saved), CaseSpec(json.dumps(saved))):
        assert spec.case_id == old.spec.case_id
        np.testing.assert_array_equal(spec.generate(), old.points)
    fresh = reproduce_case(case_path)
    assert fresh.spec.case_id == old.spec.case_id
    assert fresh.validation == old.validation and fresh.metrics == old.metrics
    np.testing.assert_array_equal(fresh.points, old.points)


def test_all_default_search_specs_preserve_identity_and_generation():
    from geometry.constraints import LayoutConstraintError
    total, feasible = 0, 0
    for N in range(12, 41):
        for legacy in default_layout_specs(N, GeometryConfig()):
            total += 1
            spec = CaseSpec.create(legacy.layout_type, N, GeometryConfig(), legacy.parameters)
            restored = CaseSpec.from_normalized(spec.normalized_spec)
            assert restored.case_id == spec.case_id
            try:
                original = generate_layout(legacy.layout_type, N, 55, 4, 8, **legacy.parameters)
            except LayoutConstraintError:
                with pytest.raises(LayoutConstraintError):
                    restored.generate()
            else:
                feasible += 1
                np.testing.assert_allclose(restored.generate(), original, rtol=0, atol=1e-9)
    assert (total, feasible) == (426, 415)


def test_repeated_case_distinct_runs_and_provenance_do_not_change_identity(tmp_path, monkeypatch):
    request = CaseRequest(CaseSpec.create("A_Rectangular", 24, GeometryConfig()))
    outputs = []
    for commit in ("first", "second"):
        monkeypatch.setattr(archive, "collect_provenance", lambda: {"git": {"commit": commit}})
        path = archive_run([request], name=commit, output_root=tmp_path)
        outputs.append((path, json.loads(path.read_text(encoding="utf-8"))))
    assert outputs[0][1]["run_id"] != outputs[1][1]["run_id"]
    assert outputs[0][1]["cases"][0]["case_id"] == outputs[1][1]["cases"][0]["case_id"]
    assert outputs[0][0].is_file() and outputs[1][0].is_file()


def test_duplicate_cases_rejected_before_creating_outputs(tmp_path):
    request = CaseRequest(CaseSpec.create("A_Rectangular", 24, GeometryConfig()))
    with pytest.raises(ValueError, match="Duplicate"):
        archive_run([request, request], name="Duplicates", output_root=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_run_reservation_retries_collision_without_overwriting(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    class FrozenClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(archive, "datetime", FrozenClock)
    suffixes = iter(["first", "first", "second"])
    monkeypatch.setattr(archive.secrets, "token_hex", lambda _: next(suffixes))
    request = CaseRequest(CaseSpec.create("rectangular", 1, GeometryConfig(), {"spacing": 8}))
    first = archive_run([request], name="One", output_root=tmp_path)
    old = first.read_bytes()
    second = archive_run([request], name="Two", output_root=tmp_path)
    assert first != second and first.read_bytes() == old


def test_single_nozzle_undefined_metrics_are_strict_json_null(tmp_path):
    request = CaseRequest(CaseSpec.create("rectangular", 1, GeometryConfig(), {"spacing": 8}))
    run = archive_run([request], name="Single point", output_root=tmp_path)
    data = json.loads(run.read_text(encoding="utf-8"))
    case_path = run.parent / data["cases"][0]["manifest"]
    raw = case_path.read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    case = json.loads(raw)
    assert case["validation"]["feasible"]
    assert case["metrics"]["min_center_distance"] is None
    assert case["metrics"]["uniformity_score"] is None
    assert verify_case(case_path)["overall_match"]


def test_infeasible_run_retains_diagnostic_and_completed_case_index(tmp_path):
    requests = [
        CaseRequest(CaseSpec.create("A_Rectangular", 24, GeometryConfig())),
        CaseRequest(CaseSpec.create("rectangular", 24, GeometryConfig(), {"spacing": 100})),
    ]
    from geometry.constraints import LayoutConstraintError
    with pytest.raises(LayoutConstraintError):
        archive_run(requests, name="Failure is recorded", output_root=tmp_path)
    path = next(tmp_path.glob("*/run.json"))
    run = json.loads(path.read_text(encoding="utf-8"))
    assert run["status"] == "failed" and run["case_count"] == 1
    assert run["planned_case_count"] == 2
    assert run["error"]["type"] == "LayoutConstraintError"
    assert run["error"]["message"]
    assert verify_case(path.parent / run["cases"][0]["manifest"])["overall_match"]


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(case_id="wrong"),
    lambda data: data["normalized_spec"]["geometry"].update(R=56),
    lambda data: data.update(schema_version=999),
    lambda data: data.update(units={"length": "m"}),
])
def test_replay_rejects_modified_identity_or_schema(full_run, tmp_path, mutation):
    path, run = full_run
    data = json.loads((path.parent / run["cases"][0]["manifest"]).read_text(encoding="utf-8"))
    mutation(data)
    target = tmp_path / "case.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        reproduce_case(target)


def test_archive_is_portable_and_file_changes_are_detected(full_run, tmp_path):
    path, run = full_run
    original = path.parent / run["cases"][0]["manifest"]
    moved = tmp_path / "moved"
    shutil.copytree(original.parent, moved)
    assert verify_case(moved / "case.json")["overall_match"]
    with (moved / "coordinates.csv").open("a", encoding="utf-8") as stream:
        stream.write("25,0,0,0\n")
    result = verify_case(moved / "case.json")
    assert not result["overall_match"] and not result["file_hashes_match"]
    assert not result["count_match"]
    assert len(reproduce_case(moved / "case.json").points) == 24


def test_file_reference_cannot_escape_case_directory(full_run, tmp_path):
    path, run = full_run
    original = path.parent / run["cases"][0]["manifest"]
    data = json.loads(original.read_text(encoding="utf-8"))
    data["files"]["coordinates"] = "../outside.csv"
    target = tmp_path / "case.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="inside"):
        verify_case(target)


@pytest.mark.parametrize("fault", [
    "case_run_id", "index_case_id", "case_id", "missing_case", "absolute_case_path",
    "escaping_case_path", "missing_reference", "missing_provenance_file",
    "wrong_provenance_file", "wrong_provenance_pointer", "missing_run_provenance",
    "absolute_provenance_path", "invalid_cfd_status", "cfd_run_id", "cfd_case_id",
    "not_started_metrics", "duplicate_index", "wrong_count", "requested_id",
])
def test_verify_run_rejects_broken_linkage(full_run, tmp_path, fault):
    original, _ = full_run
    root = tmp_path / "copied"
    shutil.copytree(original.parent, root)
    path = root / "run.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    case_path = root / run["cases"][0]["manifest"]
    case = json.loads(case_path.read_text(encoding="utf-8"))
    if fault == "case_run_id":
        # Corrupt both IDs as in the audit: local self-consistency is insufficient.
        case["run_id"] = case["cfd"]["run_id"] = "run_wrong"
    elif fault == "index_case_id":
        run["cases"][0]["case_id"] = "case_wrong"
    elif fault == "case_id":
        case["case_id"] = "case_wrong"
    elif fault == "missing_case":
        run["cases"][0]["manifest"] = "cases/missing/case.json"
    elif fault == "absolute_case_path":
        run["cases"][0]["manifest"] = str(case_path.resolve())
    elif fault == "escaping_case_path":
        run["cases"][0]["manifest"] = "../outside/case.json"
    elif fault == "missing_reference":
        case.pop("provenance")
    elif fault == "missing_provenance_file":
        case["provenance"]["file"] = "../../missing.json"
    elif fault == "wrong_provenance_file":
        case["provenance"]["file"] = "metrics.json"
    elif fault == "wrong_provenance_pointer":
        case["provenance"]["json_pointer"] = "/missing"
    elif fault == "missing_run_provenance":
        run.pop("provenance")
    elif fault == "absolute_provenance_path":
        case["provenance"]["file"] = str(path.resolve())
    elif fault == "invalid_cfd_status":
        case["cfd"]["status"] = "banana"
    elif fault == "cfd_run_id":
        case["cfd"]["run_id"] = "run_wrong"
    elif fault == "cfd_case_id":
        case["cfd"]["case_id"] = "case_wrong"
    elif fault == "not_started_metrics":
        case["cfd"]["metrics"]["pressure_loss"] = 0
    elif fault == "duplicate_index":
        run["cases"][1] = run["cases"][0].copy()
    elif fault == "wrong_count":
        run["case_count"] += 1
    elif fault == "requested_id":
        run["requested_cases"][0]["case_id"] = "case_wrong"
    path.write_text(json.dumps(run), encoding="utf-8")
    case_path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_run(path)


def test_verify_run_is_portable_and_separate_from_geometry_replay(full_run, tmp_path, monkeypatch):
    path, _ = full_run
    moved = tmp_path / "another_location"
    shutil.copytree(path.parent, moved)
    assert verify_run(moved / "run.json")["overall_match"]
    run = json.loads((moved / "run.json").read_text(encoding="utf-8"))
    case_path = moved / run["cases"][0]["manifest"]
    # Deny all old coordinate reads during reproduction, without deleting them.
    original_open = type(case_path).open
    def no_csv(self, *args, **kwargs):
        if self.suffix == ".csv":
            raise AssertionError("Reproduction must not read old coordinates")
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(type(case_path), "open", no_csv)
    assert len(reproduce_case(case_path).points) == 24


@pytest.mark.parametrize("kind", ["oserror", "windows_message", "posix_message", "unc_message"])
def test_failed_archive_records_structured_error_without_absolute_paths(tmp_path, monkeypatch, kind):
    request = CaseRequest(CaseSpec.create("A_Rectangular", 24, GeometryConfig()))
    external = tmp_path / "private folder" / "source.csv"
    secret = {
        "oserror": str(external.resolve()),
        "windows_message": "Z:\\private folder\\source.csv",
        "posix_message": "/private/source folder/source.csv",
        "unc_message": "\\\\server\\private folder\\source.csv",
    }[kind]
    failure = OSError(13, "Permission denied", secret) if kind == "oserror" else ValueError(
        f"Cannot export '{secret}': invalid output")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(archive, "export_coordinates", fail)
    with pytest.raises(type(failure)) as caught:
        archive_run([request], name="Error context", output_root=tmp_path / "runs")
    assert caught.value is failure  # Diagnostics must not swallow the original error.
    run_path = next((tmp_path / "runs").glob("*/run.json"))
    raw = run_path.read_text(encoding="utf-8")
    run = json.loads(raw)
    error = run["error"]
    assert run["status"] == "failed"
    assert error["type"] == error["error_type"] == type(failure).__name__
    assert error["case_id"] == request.spec.case_id
    assert error["relative_path"] == f"cases/{request.spec.case_id}/coordinates.csv"
    assert secret not in error["message"] and "private folder" not in raw
    assert "source.csv" not in raw and "server" not in raw
    if kind == "oserror":
        assert error["error_code"] == 13 and "Permission denied" in error["message"]
    else:
        assert error["error_code"] is None and "invalid output" in error["message"]
        assert "[absolute path omitted]" in error["message"]
