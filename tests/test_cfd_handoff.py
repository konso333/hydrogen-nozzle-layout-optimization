"""All numerical CFD fixtures here are synthetic/test-only, never solver output."""

from decimal import Decimal
from copy import deepcopy
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from cfd import (CFDSpec, CFD_STATUSES, create_attempt, export_cfd_package,
                 import_cfd_results, load_attempt, load_cfd_package,
                 metric_definitions, select_cases)
from config import GeometryConfig
from experiments.archive import CaseRequest, archive_run, verify_run
from experiments.spec import CaseSpec
from geometry.definitions import metric_definitions as geometry_definitions
from optimization.cfd_metrics import CFD_FIELDS


def geometry_spec():
    return CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10})


def spec(condition=None, config=None):
    return CFDSpec.create(geometry_spec().case_id,
                          {"equivalence_ratio": {"value": .4, "unit": "1"}} if condition is None else condition,
                          {"solver": "Fluent", "dimensionality": "3D"} if config is None else config)


@pytest.fixture
def package(tmp_path):
    run = archive_run([CaseRequest(geometry_spec())], name="M6 synthetic/test-only", output_root=tmp_path / "runs")
    path = export_cfd_package(run, spec(), output_root=tmp_path / "cfd", required_metrics=["pressure_loss"])
    return run, path


def attempt(path):
    created = create_attempt(path, solver_version="synthetic/test-only", data_kind="synthetic/test-only")
    return load_attempt(path, created.stem)


def running(path):
    record = attempt(path)
    record["status"] = "running"
    import_cfd_results(path, record)
    return record


def completed(path):
    record = running(path)
    record["status"] = "completed"
    record["metrics"]["pressure_loss"] = {"value": 0, "unit": "Pa", "source": "synthetic/test-only inlet and outlet total-pressure area means"}
    return record


@pytest.mark.parametrize("value", [300, 300., np.int64(300), np.float64(300), Decimal("300")])
def test_numeric_canonicalization(value):
    condition = {"inlet_temperature": {"value": value, "unit": "K"}}
    assert spec(condition).cfd_case_id == spec({"inlet_temperature": {"unit": "K", "value": 300}}).cfd_case_id


@pytest.mark.parametrize("value", [True, np.bool_(False), "300", None, float("nan"), float("inf"), Decimal("NaN"), object(), -1, 0])
def test_invalid_input_values(value):
    with pytest.raises((ValueError, TypeError)):
        spec({"inlet_temperature": {"value": value, "unit": "K"}})


@pytest.mark.parametrize("condition", [
    {"inlet_temperature": 300}, {"inlet_temperature": {"value": 300, "unit": "C"}},
    {"temperature": 300}, {"fuel_mole_fractions": {"H2": True}},
    {"fuel_mole_fractions": {"H2": .5}}, {"extras": {"temperature": 300}},
    {"extras": {"path": "D:/solver"}}, {"extras": {"doi": "example"}},
    {"extras": {"value": True, "unit": "1"}}, {"extras": []},
])
def test_invalid_condition_schema(condition):
    with pytest.raises((ValueError, TypeError)):
        spec(condition)


def test_identity_and_snapshot():
    a = spec()
    condition = {"oxidizer_mole_fractions": {"O2": .21, "N2": .79},
                 "extras": {"wall": {"temperature": {"value": 600, "unit": "K"}}}}
    b = spec(condition)
    reordered = dict(reversed(list(condition.items())))
    assert b == spec(reordered)
    assert b == CFDSpec.from_normalized(b.normalized_spec)
    assert a.cfd_case_id != b.cfd_case_id
    snapshot = b.normalized_spec
    snapshot["simulation_config"]["solver"] = "changed"
    assert b.normalized_spec["simulation_config"]["solver"] == "Fluent"
    del snapshot["operating_condition"]["inlet_temperature"]
    with pytest.raises(ValueError):
        CFDSpec.from_normalized(snapshot)


@pytest.mark.parametrize("field,unit", [("equivalence_ratio", "1"), ("inlet_temperature", "K"), ("inlet_pressure", "Pa"), ("mass_flow_rate", "kg/s")])
def test_physical_changes_identity(field, unit):
    a, b = spec({field: {"value": 1, "unit": unit}}), spec({field: {"value": 2, "unit": unit}})
    assert a.normalized_spec["case_id"] == b.normalized_spec["case_id"] == geometry_spec().case_id
    assert a.cfd_case_id != b.cfd_case_id
    assert a.operating_condition_id != b.operating_condition_id


@pytest.mark.parametrize("config", [{"solver_version": "2026"}, {"dimensionality": 3}, {"steady_or_transient": "maybe"}, {"extras": {"x": float("inf")}}, {"extras": {"created_at": "today"}}])
def test_invalid_config(config):
    with pytest.raises((ValueError, TypeError)):
        spec(config=config)


def test_model_identity():
    a, b = spec(config={"combustion_model": "a"}), spec(config={"combustion_model": "b"})
    assert a.cfd_case_id != b.cfd_case_id
    assert a.operating_condition_id == b.operating_condition_id


def test_package_two_conditions_and_m2_preservation(package):
    run, path = package
    before = {p.relative_to(run.parent): p.read_bytes() for p in run.parent.rglob("*") if p.is_file()}
    root = path.parents[1]
    b = spec({"equivalence_ratio": {"value": .5, "unit": "1"}})
    other = export_cfd_package(run, b, output_root=root, required_metrics=["pressure_loss"])
    assert other != path and other.exists()
    a_data, b_data = load_cfd_package(path), load_cfd_package(other)
    assert a_data["case_id"] == b_data["case_id"]
    assert a_data["cfd_case_id"] != b_data["cfd_case_id"]
    for ref in a_data["geometry"]["run_manifest"], a_data["geometry"]["case_manifest"], a_data["files"]["coordinates"]:
        assert not Path(ref).is_absolute()
    selected = select_cases(run, [a_data["case_id"]])[0]
    assert (path.parent / "geometry/coordinates.csv").read_bytes() == (selected.parent / "coordinates.csv").read_bytes()
    assert import_cfd_results(path, completed(path)).exists()
    assert all(p.read_bytes() == before[p.relative_to(run.parent)] for p in run.parent.rglob("*") if p.is_file())
    assert verify_run(run)["overall_match"]
    with pytest.raises(FileExistsError):
        export_cfd_package(run, spec(), output_root=root, required_metrics=["pressure_loss"])


def test_relocation(package, tmp_path):
    run, path = package
    destination = tmp_path / "relocated"
    destination.mkdir()
    shutil.copytree(tmp_path / "runs", destination / "runs")
    shutil.copytree(tmp_path / "cfd", destination / "cfd")
    relocated = destination / path.relative_to(tmp_path)
    assert load_cfd_package(relocated) == load_cfd_package(path)
    assert create_attempt(relocated).exists()


@pytest.mark.parametrize("field", ["run_id", "case_id", "cfd_case_id", "attempt_id"])
def test_wrong_result_identity_does_not_write(package, field):
    _, path = package
    record = running(path)
    target = path.parent / "attempts" / (record["attempt_id"] + ".json")
    before = target.read_bytes()
    record[field] = "wrong"
    with pytest.raises(ValueError):
        import_cfd_results(path, record)
    assert target.read_bytes() == before


@pytest.mark.parametrize("fault", ["unknown", "missing", "unit", "nan", "bool", "source", "range"])
def test_result_rejections(package, fault):
    _, path = package
    record = completed(path)
    if fault == "unknown":
        record["metrics"]["NOx"] = {"value": 1, "unit": "ppm", "source": "test"}
    elif fault == "missing":
        record["metrics"] = {}
    elif fault == "unit":
        record["metrics"]["pressure_loss"]["unit"] = "kPa"
    elif fault in {"nan", "bool"}:
        record["metrics"]["pressure_loss"]["value"] = float("nan") if fault == "nan" else True
    elif fault == "source":
        record["metrics"]["pressure_loss"]["source"] = ""
    else:
        record["metrics"]["hydrogen_conversion"] = {"value": 1.1, "unit": "1", "source": "synthetic/test-only"}
    with pytest.raises(ValueError):
        import_cfd_results(path, record)
    assert load_attempt(path, record["attempt_id"])["status"] == "running"


def test_status_lifecycle_null_zero_and_repeats(package, tmp_path):
    _, path = package
    record = attempt(path)
    second = attempt(path)
    assert record["attempt_id"] != second["attempt_id"]
    assert record["cfd_case_id"] == second["cfd_case_id"]
    assert all(v is None for v in record["metrics"].values())
    for status in ("prepared", "exported", "running"):
        record["status"] = status
        import_cfd_results(path, record)
    record["status"] = "completed"
    record["metrics"]["pressure_loss"] = {"value": 0, "unit": "Pa", "source": "synthetic/test-only"}
    input_json = tmp_path / "synthetic_result.json"
    input_json.write_text(json.dumps(record), encoding="utf-8")
    import_cfd_results(path, input_json)
    result = load_attempt(path, record["attempt_id"])
    assert result["metrics"]["pressure_loss"]["value"] == 0
    assert result["metrics"]["hydrogen_conversion"] is None
    import_cfd_results(path, record)  # idempotent
    record["metrics"]["pressure_loss"]["value"] = 1
    with pytest.raises(ValueError, match="immutable"):
        import_cfd_results(path, record)
    assert load_attempt(path, second["attempt_id"])["status"] == "not_started"


@pytest.mark.parametrize("status", ["banana", "completed", "running", "failed"])
def test_invalid_state_or_incomplete_record(package, status):
    _, path = package
    record = load_attempt(path, create_attempt(path).stem)
    record["status"] = status
    with pytest.raises(ValueError):
        import_cfd_results(path, record)


def test_failed_sanitizes_paths_and_preserves_missing(package):
    _, path = package
    record = attempt(path)
    record["status"] = "failed"
    record["error"] = {"error_type": "SolverError", "message": 'Cannot read D:\\private folder\\case.cas',
                       "solver_exit_status": 2, "relative_log_path": None}
    import_cfd_results(path, record)
    result = load_attempt(path, record["attempt_id"])
    assert "D:" not in result["error"]["message"]
    assert "absolute path omitted" in result["error"]["message"]
    assert all(v is None for v in result["metrics"].values())


@pytest.mark.parametrize("ref", ["D:/private/log", "/tmp/log", "\\\\server\\share\\log", "../../escape.log"])
def test_failed_log_path_rejected(package, ref):
    _, path = package
    record = attempt(path)
    record.update(status="failed", error={"error_type": "SolverError", "message": "failed",
                                         "solver_exit_status": None, "relative_log_path": ref})
    with pytest.raises(ValueError):
        import_cfd_results(path, record)


@pytest.mark.parametrize("fault", ["identity", "coordinates", "source", "contract", "status",
                                  "missing_provenance", "empty_provenance", "missing_geometry",
                                  "malformed_metadata", "unknown_field"])
def test_package_corruption(package, fault):
    _, path = package
    data = json.loads(path.read_text(encoding="utf-8"))
    if fault == "identity":
        data["cfd_case_id"] = "wrong"
    elif fault == "coordinates":
        (path.parent / "geometry/coordinates.csv").write_text("wrong", encoding="utf-8")
    elif fault == "source":
        data["geometry"]["run_manifest"] = "D:/private/run.json"
    elif fault == "contract":
        data["result_contract"]["metrics"]["pressure_loss"]["unit"] = "kPa"
    elif fault == "status":
        data["status"] = "completed"
    elif fault == "missing_provenance":
        del data["provenance"]
    elif fault == "empty_provenance":
        data["provenance"] = {}
    elif fault == "missing_geometry":
        del data["geometry"]["units"]
    elif fault == "malformed_metadata":
        data["result_contract"]["metrics"]["pressure_loss"] = None
    else:
        data["unexpected"] = "field"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cfd_package(path)


def test_namespace_separation_and_selection(package):
    run, _ = package
    definitions = metric_definitions(["pressure_loss"])
    assert set(definitions) == set(CFD_FIELDS)
    assert not set(definitions).intersection(geometry_definitions())
    assert len(geometry_definitions()) == 19
    assert {"not_started", "prepared", "exported", "running", "completed", "failed"} == set(CFD_STATUSES)
    for selection in ([], ["wrong"], [geometry_spec().case_id] * 2):
        with pytest.raises(ValueError):
            select_cases(run, selection)


def test_no_results_before_start_and_no_provenance_reclassification(package):
    _, path = package
    record = attempt(path)
    record["metrics"]["pressure_loss"] = {"value": 0, "unit": "Pa", "source": "synthetic/test-only"}
    with pytest.raises(ValueError):
        import_cfd_results(path, record)
    record["metrics"]["pressure_loss"] = None
    record["provenance"]["data_kind"] = "external_cfd"
    with pytest.raises(ValueError):
        import_cfd_results(path, record)


def test_changed_execution_settings_require_new_attempt(package):
    _, path = package
    record = running(path)
    record["provenance"]["solver_version"] = "another-version"
    with pytest.raises(ValueError):
        import_cfd_results(path, record)
    new = create_attempt(path, solver_version="another-version", numerical_settings={"iterations": {"value": 200, "unit": "1"}})
    assert load_attempt(path, new.stem)["cfd_case_id"] == record["cfd_case_id"]


# Static pre-cleanup oracle: manually specified fields and fixed digest, checked
# against the existing M6 example before changing the implementation. Never
# compute the expected digest with CFDSpec during a test.
CANONICAL_CFD_JSON = (
    '{"case_id":"case_v1_4d9f068ca11d6f4145df3b7c445488b9c9c8dca1f431ba826a53d78f43602a6d",'
    '"operating_condition":{"equivalence_ratio":{"unit":"1","value":0.4},"extras":{},'
    '"fuel_mole_fractions":null,"inlet_pressure":null,"inlet_temperature":null,'
    '"mass_flow_rate":null,"oxidizer_mole_fractions":null},"schema_version":1,'
    '"simulation_config":{"combustion_model":null,"dimensionality":"3D","extras":{},'
    '"model_name":null,"solver":"Fluent","steady_or_transient":null,"turbulence_model":null}}'
)
CANONICAL_CFD_ID = "cfd_v1_87affd44e3d0060e663010f7e859ec467884c1ec31e163b509d3b25b32afa387"


def test_fixed_canonical_identity():
    snapshot = json.loads(CANONICAL_CFD_JSON)
    assert geometry_spec().case_id == snapshot["case_id"]
    for current in (spec(), CFDSpec.from_normalized(snapshot)):
        assert current.normalized_spec == snapshot
        assert current._canonical == CANONICAL_CFD_JSON
        assert current.cfd_case_id == CANONICAL_CFD_ID


@pytest.mark.parametrize("scope", ["operating_condition", "simulation_config"])
@pytest.mark.parametrize("depth", ["top", "nested", "list"])
def test_execution_metadata_rejected_recursively(scope, depth):
    # Independent task-level inventory, not imported from the implementation.
    keys = (
        "output_path", "output_dir", "working_directory", "workdir", "absolute_path",
        "created_at", "updated_at", "started_at", "finished_at", "timestamp",
        "solver_execution_time", "execution_time", "wall_clock_time", "elapsed_time", "runtime",
        "git_commit", "git_branch", "git_dirty", "platform", "hostname", "machine",
        "computer_name", "attempt_id", "status", "result", "results", "solver_version",
        "output_directory", "installation_path", "path", "git", "branch", "numerical_settings",
        "citation", "doi", "run_id", "execution_environment", "provenance", "OUTPUT_PATH",
    )
    for key in keys:
        extra = {key: "synthetic/test-only"}
        if depth == "nested":
            extra = {"execution": {"metadata": extra}}
        elif depth == "list":
            extra = {"settings": [{"nested": extra}]}
        original = deepcopy(extra)
        condition = {"extras": extra} if scope == "operating_condition" else {}
        config = {"extras": extra} if scope == "simulation_config" else {}
        with pytest.raises(ValueError, match="belongs outside physical specification"):
            spec(condition, config)
        assert extra == original  # Reject, never silently strip the input.


@pytest.mark.parametrize("scope", ["operating_condition", "simulation_config"])
def test_physical_extra_changes_identity(scope):
    snapshots = []
    for value in (.4, .6):
        extra = {"swirl_number": {"value": value, "unit": "1"}}
        snapshots.append(spec({"extras": extra} if scope == "operating_condition" else {},
                              {"extras": extra} if scope == "simulation_config" else {}))
        assert snapshots[-1].normalized_spec[scope]["extras"] == extra
    assert snapshots[0].cfd_case_id != snapshots[1].cfd_case_id
    assert snapshots[0].normalized_spec["case_id"] == snapshots[1].normalized_spec["case_id"]


@pytest.mark.parametrize("field", ["platform", "solver_version", "git_commit"])
def test_execution_environment_does_not_change_physical_identity(package, field):
    _, path = package
    records = []
    for value in ("synthetic-A", "synthetic-B"):
        environment = {"platform": "synthetic-platform", "git_commit": "synthetic-commit",
                       "git_dirty": np.bool_(False), "hostname": "synthetic-host",
                       "extraction_tool_version": "synthetic-tool"}
        version = "synthetic-solver"
        if field == "solver_version":
            version = value
        else:
            environment[field] = value
        target = create_attempt(path, solver_version=version, data_kind="synthetic/test-only",
                                execution_environment=environment)
        record = load_attempt(path, target.stem)
        assert record["provenance"]["execution_environment"]["git_dirty"] is False
        assert "solver_version" not in record["provenance"]["execution_environment"]
        records.append(record)
    assert records[0]["attempt_id"] != records[1]["attempt_id"]
    assert records[0]["cfd_case_id"] == records[1]["cfd_case_id"] == CANONICAL_CFD_ID
    assert records[0]["case_id"] == records[1]["case_id"] == geometry_spec().case_id
    assert records[0]["provenance"] != records[1]["provenance"]


@pytest.mark.parametrize("environment", [
    [], {"git_dirty": 1}, {"git_dirty": "false"}, {"platform": 123},
    {"platform": ""}, {"hostname": "D:/private/host"}, {"platform": float("nan")},
    {"unknown": "value"}, {"solver_version": "same-or-conflicting-version"},
])
def test_invalid_execution_environment(environment):
    from cfd.handoff import _provenance
    with pytest.raises((TypeError, ValueError)):
        _provenance({"solver_version": "synthetic/test-only", "data_kind": "synthetic/test-only",
                     "numerical_settings": {}, "execution_environment": environment}, executed=True)


def test_numerical_provenance_keeps_legacy_validation():
    from cfd.handoff import _provenance
    original = {"solver_version": "synthetic/test-only", "data_kind": "synthetic/test-only",
                "numerical_settings": {"runtime": {"value": 10, "unit": "s"}}}
    assert _provenance(original, executed=True)["numerical_settings"] == original["numerical_settings"]
    original["numerical_settings"] = {"solver_version": "contradicting-version"}
    with pytest.raises(ValueError, match="belongs outside physical specification"):
        _provenance(original, executed=True)


@pytest.mark.parametrize("terminal", [False, True])
def test_legacy_attempt_without_environment_loads_without_rewrite(package, terminal):
    _, path = package
    record = completed(path) if terminal else attempt(path)
    if terminal:
        import_cfd_results(path, record)
    target = path.parent / "attempts" / (record["attempt_id"] + ".json")
    legacy = json.loads(target.read_text(encoding="utf-8"))
    del legacy["provenance"]["execution_environment"]
    target.write_text(json.dumps(legacy), encoding="utf-8")
    original = target.read_bytes()
    loaded = load_attempt(path, record["attempt_id"])
    assert loaded["provenance"]["execution_environment"] is None
    assert target.read_bytes() == original
    if terminal:
        import_cfd_results(path, loaded)  # Legacy terminal remains idempotent.
        assert target.read_bytes() == original
    else:
        loaded["status"] = "running"
        import_cfd_results(path, loaded)
        assert load_attempt(path, loaded["attempt_id"])["status"] == "running"


def test_execution_environment_unknown_and_later_declared(package):
    _, path = package
    record = running(path)
    assert record["provenance"]["execution_environment"] is None
    record["provenance"]["execution_environment"] = {"platform": "remote-synthetic-platform"}
    import_cfd_results(path, record)
    record = load_attempt(path, record["attempt_id"])
    assert record["provenance"]["execution_environment"]["hostname"] is None
    original = deepcopy(record)
    for replacement in ("other-platform", None):
        record["provenance"]["execution_environment"]["platform"] = replacement
        with pytest.raises(ValueError, match="Known execution environment is fixed"):
            import_cfd_results(path, record)
    record = original
    record["status"] = "completed"
    record["provenance"]["execution_environment"]["extraction_tool_version"] = "synthetic-v1"
    record["metrics"]["pressure_loss"] = {"value": 0, "unit": "Pa", "source": "synthetic/test-only"}
    import_cfd_results(path, record)
    assert load_attempt(path, record["attempt_id"])["status"] == "completed"


def test_complete_fields_cannot_bypass_not_started_transition(package):
    from cfd.handoff import _validate_record
    _, path = package
    record = attempt(path)  # Valid solver version, identity and creation time.
    record["status"] = "completed"
    record["metrics"]["pressure_loss"] = {"value": 0, "unit": "Pa", "source": "synthetic/test-only"}
    _validate_record(record, load_cfd_package(path), path)  # All non-transition checks pass.
    with pytest.raises(ValueError, match="Invalid CFD transition: not_started -> completed"):
        import_cfd_results(path, record)
    assert load_attempt(path, record["attempt_id"])["status"] == "not_started"


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_terminal_cannot_restart_with_otherwise_valid_record(package, status):
    from cfd.handoff import _validate_record
    _, path = package
    record = completed(path) if status == "completed" else running(path)
    if status == "failed":
        record.update(status="failed", error={"error_type": "SyntheticFailure", "message": "synthetic/test-only",
                                             "solver_exit_status": 1, "relative_log_path": None})
    target = import_cfd_results(path, record)
    original = target.read_bytes()
    record.update(status="running", error=None)
    _validate_record(record, load_cfd_package(path), path)
    with pytest.raises(ValueError, match="Terminal attempt is immutable"):
        import_cfd_results(path, record)
    assert target.read_bytes() == original


def test_failed_rerun_creates_independent_attempt(package):
    _, path = package
    first = running(path)
    first.update(status="failed", error={"error_type": "SyntheticFailure", "message": "synthetic/test-only",
                                         "solver_exit_status": None, "relative_log_path": None})
    first_path = import_cfd_results(path, first)
    before = first_path.read_bytes()
    second = completed(path)
    second_path = import_cfd_results(path, second)
    assert first["cfd_case_id"] == second["cfd_case_id"]
    assert first["attempt_id"] != second["attempt_id"]
    assert first_path != second_path and first_path.read_bytes() == before
    assert load_attempt(path, second["attempt_id"])["status"] == "completed"


@pytest.mark.parametrize("field,wrong", [
    ("run_id", "run_20260908T000000000000Z_0123456789abcdef"),
    ("case_id", "case_v1_" + "a" * 64),
    ("cfd_case_id", "cfd_v1_" + "a" * 64),
])
def test_valid_format_wrong_association_is_rejected_independently(package, field, wrong):
    _, path = package
    record = running(path)
    target = path.parent / "attempts" / (record["attempt_id"] + ".json")
    original = target.read_bytes()
    record[field] = wrong
    with pytest.raises(ValueError, match=f"Result {field} mismatch"):
        import_cfd_results(path, record)
    assert target.read_bytes() == original


def test_valid_attempt_id_mismatch_does_not_write(package):
    from cfd.handoff import _validate_record
    _, path = package
    record = running(path)
    target = path.parent / "attempts" / (record["attempt_id"] + ".json")
    original = target.read_bytes()
    # Fixture corruption isolates filename vs embedded attempt_id; creation time,
    # status, solver version, and run/case/CFD identity all remain identical.
    wrong_id = "attempt_" + "a" * 32
    wrong_target = target.with_name(wrong_id + ".json")
    wrong_target.write_bytes(original)
    record["attempt_id"] = wrong_id
    _validate_record(record, load_cfd_package(path), path)
    with pytest.raises(ValueError, match="Attempt identity or creation time mismatch"):
        import_cfd_results(path, record)
    assert wrong_target.read_bytes() == original == target.read_bytes()
