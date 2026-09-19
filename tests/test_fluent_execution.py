"""M8B tests use inert files and fake processes only; never launch Fluent."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from cfd import CFDSpec, export_cfd_package, load_attempt, load_cfd_package
from fluent import export_automation
from fluent_execution import baseline_spec, load_baseline, prepare_example, prepare, inspect, dry_run, execute, ExecutionRefused
from fluent_execution.plan import sha256, SHAKEDOWN_MAPPING
import fluent_execution.plan as plan_module
import fluent_execution.execution as execution
import fluent_execution.process as process_module
from fluent_execution.results import extract_diagnostics
from fluent_pilot import LocalFluentConfig, FluentLaunchPlan, select_pilot_request
from scripts.fluent_execute_pilot import main


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def tmp_path(tmp_path_factory):
    # M2 retains full scientific IDs in its directory layout. Keep only the
    # test storage prefix short so Windows MAX_PATH does not truncate fixtures.
    return tmp_path_factory.mktemp("m8b")


@pytest.fixture
def prepared(tmp_path):
    return prepare_example(tmp_path / "baseline")


@pytest.fixture
def ready(prepared, tmp_path):
    package, _ = prepared
    old = load_cfd_package(package)
    physical = old["normalized_spec"]
    physical["simulation_config"]["steady_or_transient"] = "steady"
    physical["simulation_config"]["extras"].update({
        "computational_domain": {"dimension": "3D", "description": "synthetic-test-only domain", "geometry_source": "test:geometry"},
        "density_model": "synthetic-test-only density model",
        "gravity_decision": {"enabled": False, "specification": "explicitly disabled for synthetic test"},
        "radiation_model_decision": {"enabled": False, "model": "explicitly disabled for synthetic test"},
        "species_material_database": {"species": ["H2", "O2", "N2", "H2O"], "reference": "test:materials"},
        "inlet_topology": {"fuel_inlet": "circular", "oxidizer_inlet": "circular"}})
    physical["operating_condition"]["extras"]["outlet_backflow"] = {
        "temperature": {"value": 300, "unit": "K"},
        "mass_fractions": {"O2": {"value": .4, "unit": "1"}, "N2": {"value": .6, "unit": "1"}},
        "turbulence_specification": "synthetic-test-only turbulence definition"}
    spec = CFDSpec.from_normalized(physical)
    package = export_cfd_package(package.parent / old["geometry"]["run_manifest"], spec,
                                 output_root=tmp_path / "ready", required_metrics=["pressure_loss"])
    automation = export_automation(package, tmp_path / "automation",
                                   numerical_settings={"max_iterations": {"value": 500, "unit": "1"}})
    exe = tmp_path / "fluent.exe"
    exe.write_bytes(b"INERT synthetic executable -- must never be executed")
    config = LocalFluentConfig(exe, tmp_path, declared_release="2025 R2")
    mesh, journal = tmp_path / "test.input.msh", tmp_path / "test.jou"
    mesh.write_bytes(b"INERT synthetic mesh")
    journal.write_bytes(b"SYNTHETIC JOURNAL; NO VALID FLUENT COMMANDS\n")
    identity = prepare(package, automation, config).to_dict()["identity"]
    numerical = {key: "synthetic-test-only" for key in ("pressure_velocity_coupling", "discretization",
        "residuals", "mass_balance_monitor", "temperature_monitor", "species_monitor",
        "pressure_loss_monitor", "convergence_assessment")}
    numerical.update(initialization="Hybrid Initialization", max_iterations={"value": 500, "unit": "1"})
    numerical["residuals"] = {"continuity": {"value": .001, "unit": "1"}}
    for key in ("mass_balance_monitor", "temperature_monitor", "species_monitor", "pressure_loss_monitor"):
        numerical[key] = {"definition": "synthetic-test-only " + key, "sample": "synthetic steady sample"}
    contract = {"schema_version": 1, "identity": identity,
        "launch": {"arguments": ["SYNTHETIC-{processes}", "{journal}"], "executable_sha256": sha256(exe),
                   "release": "2025 R2", "dimension": "3D", "precision": "double", "evidence_reference": "test:launch"},
        "adapter_review": {"input_sha256": sha256(mesh), "journal_sha256": sha256(journal),
                           "input_mode": "existing_mesh", "dimension": "3D",
                           "boundary_mapping": {key: [key] for key in ("fuel_inlet", "oxidizer_inlet", "outlet", "wall")},
                           **{key + "_evidence": "test:" + key for key in ("geometry", "physics", "mesh", "numerics", "output")},
                           "mesh_classification": "engineering shakedown mesh",
                           "mesh_acceptance": {"checked": True, "criteria": "synthetic-test-only acceptance criteria"}},
        "expected_outputs": ["diagnostics.json"], "numerical_settings": numerical,
        "resource_review": {"minimum_free_bytes": 1, "maximum_processes": 10,
                            "license_evidence": "test:license", "resource_evidence": "test:resources"},
        "diagnostics": {"artifact": "diagnostics.json", "evidence_reference": "test:exporter"},
        "research_review": {key: {"status": "confirmed", "source": "researcher_decision",
                                  "reason": "synthetic-test-only review", "evidence_reference": "test:" + key}
                            for key in SHAKEDOWN_MAPPING}}
    return package, automation, config, {"input_artifact": mesh, "journal": journal, "contract": contract}


@pytest.fixture(autouse=True)
def no_real_solver(monkeypatch):
    monkeypatch.setattr("platform.platform", lambda: "synthetic-test-only-platform")
    original = subprocess.Popen
    def guarded(argv, *args, **kwargs):
        # Existing M2 archive provenance uses read-only git subprocesses.
        if isinstance(argv, (list, tuple)) and str(argv[0]) == "git":
            return original(argv, *args, **kwargs)
        raise AssertionError("Real solver/process forbidden in M8B tests")
    monkeypatch.setattr(subprocess, "Popen", guarded)


def diagnostics():
    return {"schema_version": 1, "sample": "synthetic-test-only iteration 500",
            "metrics": {"iteration_count": {"value": 500, "unit": "1", "source": "fake exporter"}}}


def fake_runner(monkeypatch, package, *, code=0, fail=None, missing=False, calls=None):
    def run(argv, *, cwd, stdout, stderr, timeout, on_started):
        attempts = list((package.parent / "attempts").glob("attempt_*.json"))
        attempt = next(p for p in attempts if load_attempt(package, p.stem)["status"] == "not_started")
        assert load_attempt(package, attempt.stem)["status"] == "not_started"
        if calls is not None:
            calls.append((argv, cwd, timeout))
        stdout.write(b"fake stdout\n")
        stderr.write(b"fake stderr\n")
        assert (cwd / "input.msh").read_bytes() == b"INERT synthetic mesh"
        if fail == "spawn":
            raise OSError("fake launch failure")
        on_started()
        assert load_attempt(package, attempt.stem)["status"] == "running"
        if fail == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        if fail == "interrupt":
            raise KeyboardInterrupt("fake interrupt")
        if not missing:
            write(cwd / "diagnostics.json", diagnostics())
        return code
    monkeypatch.setattr(execution, "run_process", run)


@pytest.mark.parametrize("operation", [prepare, inspect, dry_run])
def test_defaults_never_spawn_or_create_attempt(ready, operation, monkeypatch):
    package, automation, config, inputs = ready
    def forbidden(*args, **kwargs):
        pytest.fail("Preparation must not create attempt or process")
    monkeypatch.setattr(execution, "create_attempt", forbidden)
    monkeypatch.setattr(execution, "run_process", forbidden)
    result = operation(package, automation, config, **inputs).to_dict()
    assert result["m8b_ready"] and not result["execution_allowed"]
    assert result["m8a_readiness"]["execution_allowed"] is False
    assert not list((package.parent / "attempts").iterdir())


def test_baseline_and_research_range_separation(prepared, monkeypatch):
    baseline = load_baseline()
    config = baseline["confirmed_baseline"]["simulation_config"]
    assert config["dimensionality"] == "3D"
    assert {k: v["value"] for k, v in config["extras"]["nozzle_geometry"].items()} == dict(
        D=14, L=30, Da=10, Db=4, Lb=8, Rb=2, dH=1.2, n=8, alpha=90, XH=4)
    condition = baseline["confirmed_baseline"]["operating_condition"]["extras"]
    assert condition["oxidizer_inlet"]["mass_fractions"] == {"O2": {"value": .4, "unit": "1"}, "N2": {"value": .6, "unit": "1"}}
    package = load_cfd_package(prepared[0])
    original = baseline_spec(package["case_id"])
    baseline["future_research_ranges"]["D"]["interval"] = [1, 999]
    monkeypatch.setattr("fluent_execution.baseline.load_baseline", lambda: baseline)
    assert baseline_spec(package["case_id"]).cfd_case_id == original.cfd_case_id
    assert package["case_id"] != select_pilot_request().spec.case_id
    assert not list((prepared[0].parent / "attempts").iterdir())


@pytest.mark.parametrize("fault,reason", [
    ("executable", "local_environment_not_ready"), ("mesh", "geometry_or_mesh_input_required"),
    ("journal", "executable_journal_required"), ("contract", "unverified_launch_contract"),
    ("dimension", "dimension_mismatch"), ("purpose", "unsupported_execution_purpose"),
    ("geometry", "unsupported_input_mode"), ("processes", "process_resource_limit"),
    ("comments", "comment_only_journal")])
def test_execution_gates_before_attempt(ready, fault, reason):
    package, automation, config, inputs = ready
    if fault == "executable":
        config = replace(config, executable=str(Path(config.executable).with_name("missing.exe")))
    elif fault == "mesh":
        inputs["input_artifact"].unlink()
    elif fault == "journal":
        inputs["journal"].unlink()
    elif fault == "contract":
        inputs["contract"] = None
    elif fault == "dimension":
        config = replace(config, dimension="2D")
    elif fault == "purpose":
        inputs["purpose"] = "scientific"
    elif fault == "geometry":
        inputs["input_mode"] = "geometry_artifact"
    elif fault == "processes":
        config = replace(config, processes=11)
    elif fault == "comments":
        inputs["journal"].write_text("; comments only\n")
    with pytest.raises(ExecutionRefused) as error:
        execute(package, automation, config, authorize=True, **inputs)
    assert reason in error.value.report["blocking_reasons"]
    assert not list((package.parent / "attempts").iterdir())


@pytest.mark.parametrize("authorization", [False, None, 1, "true"])
def test_explicit_boolean_authorization(ready, authorization):
    package, automation, config, inputs = ready
    with pytest.raises(ExecutionRefused, match="authorization"):
        execute(package, automation, config, authorize=authorization, **inputs)
    assert not list((package.parent / "attempts").iterdir())


@pytest.mark.parametrize("field,value,reason", [("solver", "OtherSolver", "non_fluent_solver"),
    ("dimensionality", "2D", "dimension_mismatch"), ("steady_or_transient", None, "operating_mode_unresolved")])
def test_m6_compatibility(ready, tmp_path, field, value, reason):
    package, _, config, inputs = ready
    old = load_cfd_package(package)
    physical = old["normalized_spec"]
    physical["simulation_config"][field] = value
    other = export_cfd_package(package.parent / old["geometry"]["run_manifest"], CFDSpec.from_normalized(physical),
                               output_root=tmp_path / "other", required_metrics=["pressure_loss"])
    automation = export_automation(other, tmp_path / "other_m7")
    report = prepare(other, automation, config, **inputs).to_dict()
    assert reason in report["blocking_reasons"]


def test_local_paths_processes_do_not_change_identity(ready, tmp_path):
    package, automation, config, inputs = ready
    first = prepare(package, automation, config, **inputs).to_dict()
    other = tmp_path / "other"
    other.mkdir()
    exe = other / "fluent.exe"
    exe.write_bytes(Path(config.executable).read_bytes())
    second = prepare(package, automation, replace(config, executable=str(exe), working_directory=str(other), processes=4), **inputs).to_dict()
    assert first["identity"] == second["identity"]
    assert first["m8b_ready"] and second["m8b_ready"]
    assert not FluentLaunchPlan(config).execution_allowed


@pytest.mark.parametrize("fault", ["digest", "input_hash", "journal_hash", "exe_hash", "argv", "traversal",
    "empty_outputs", "alias_outputs", "face_overlap", "numerical", "license", "extra", "mesh_classification"])
def test_review_cannot_be_bypassed(ready, fault):
    package, automation, config, inputs = ready
    contract = inputs["contract"]
    if fault == "digest": contract["identity"]["automation_digest"] = "0" * 64
    elif fault == "input_hash": contract["adapter_review"]["input_sha256"] = "0" * 64
    elif fault == "journal_hash": contract["adapter_review"]["journal_sha256"] = "0" * 64
    elif fault == "exe_hash": contract["launch"]["executable_sha256"] = "0" * 64
    elif fault == "argv": contract["launch"]["arguments"] = ["{journal}", "{arbitrary}"]
    elif fault == "traversal": contract["expected_outputs"] = ["../outside"]
    elif fault == "empty_outputs": contract["expected_outputs"] = []
    elif fault == "alias_outputs": contract["expected_outputs"] = ["RUN.JOU"]
    elif fault == "face_overlap": contract["adapter_review"]["boundary_mapping"]["wall"] = ["outlet"]
    elif fault == "numerical": del contract["numerical_settings"]["residuals"]
    elif fault == "license": del contract["resource_review"]["license_evidence"]
    elif fault == "extra": contract["force"] = True
    elif fault == "mesh_classification": contract["adapter_review"]["mesh_classification"] = "validated production mesh"
    with pytest.raises(ExecutionRefused):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


def test_process_success_is_not_formal_result_or_convergence(ready, monkeypatch):
    package, automation, config, inputs = ready
    calls = []
    fake_runner(monkeypatch, package, calls=calls)
    report = execute(package, automation, config, authorize=True, **inputs)
    assert report["solver_status"] == "completed" and report["solver_exit_status"] == 0
    assert report["error"]["error_type"] == "unresolved_result_mapping"
    assert report["diagnostics"]["metrics"]["iteration_count"]["value"] == 500
    assert report["convergence_status"] == "not_assessed"
    assert report["purpose"] == "shakedown" and not report["scientific_eligible"]
    record = load_attempt(package, report["attempt_id"])
    assert record["status"] == "failed" and all(v is None for v in record["metrics"].values())
    assert record["provenance"]["numerical_settings"]["purpose"] == "shakedown"
    stage = Path(report["runtime_directory"])
    assert (stage / "stdout.log").read_bytes() == b"fake stdout\n"
    assert (stage / "stderr.log").read_bytes() == b"fake stderr\n"
    assert len(calls) == 1
    for metric in ("NO", "NO2", "NOx"):
        assert report[metric]["value"] is None


@pytest.mark.parametrize("fault,exception,error_type", [
    ("spawn", OSError, "OSError"), ("timeout", subprocess.TimeoutExpired, "timeout"),
    ("nonzero", RuntimeError, "RuntimeError"), ("missing", FileNotFoundError, "FileNotFoundError"),
    ("interrupt", KeyboardInterrupt, "KeyboardInterrupt")])
def test_failure_persists_attempt_and_logs(ready, monkeypatch, fault, exception, error_type):
    package, automation, config, inputs = ready
    fake_runner(monkeypatch, package, fail=fault, code=9 if fault == "nonzero" else 0, missing=fault == "missing")
    with pytest.raises(exception):
        execute(package, automation, config, authorize=True, **inputs)
    files = [p for p in (package.parent / "attempts").glob("*.json") if not p.name.endswith(".execution.json")]
    assert len(files) == 1
    record = load_attempt(package, files[0].stem)
    assert record["status"] == "failed" and record["error"]["error_type"] == error_type
    assert record["error"]["solver_exit_status"] == (9 if fault == "nonzero" else 0 if fault == "missing" else None)
    assert (package.parent / record["error"]["relative_log_path"]).is_file()


def test_staging_failure_creates_no_attempt(ready, monkeypatch):
    package, automation, config, inputs = ready
    def fail(*a, **kw): raise OSError("journal staging failed")
    monkeypatch.setattr(execution.shutil, "copyfile", fail)
    with pytest.raises(OSError, match="staging"):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


@pytest.mark.parametrize("flag", ["--inspect", "--dry-run", None])
def test_cli_no_execute(ready, capsys, flag):
    package, automation, config, _ = ready
    argv = ["--package", str(package), "--automation-manifest", str(automation),
            "--fluent-executable", config.executable, "--working-directory", config.working_directory]
    if flag: argv.append(flag)
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert not report["execution_allowed"] and not report["attempt_created"]
    assert not list((package.parent / "attempts").iterdir())


@pytest.mark.parametrize("flag", ["--force", "--skip-gate", "--ignore-readiness"])
def test_cli_no_bypass(flag):
    with pytest.raises(SystemExit): main([flag])


@pytest.mark.parametrize("metric,value", [("NOx", 0), ("maximum_temperature", float("nan")),
    ("iteration_count", True), ("iteration_count", 2.5), ("mass_balance_error", -1)])
def test_invalid_diagnostics(tmp_path, metric, value):
    data = diagnostics()
    data["metrics"] = {metric: {"value": value, "unit": "K" if metric == "maximum_temperature" else "1", "source": "test"}}
    path = tmp_path / "diagnostics.json"
    write(path, data)
    with pytest.raises(ValueError): extract_diagnostics(path)


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_timeout(ready, timeout):
    package, automation, config, inputs = ready
    with pytest.raises(ValueError, match="timeout"):
        prepare(package, automation, config, timeout_seconds=timeout, **inputs)


@pytest.mark.parametrize("fail", [False, True])
def test_subprocess_structure_and_cleanup(tmp_path, monkeypatch, fail):
    events = []
    class FakeProcess:
        pid = 123
        _handle = 456
        def wait(self, timeout):
            events.append(("wait", timeout))
            if fail and timeout == 7: raise subprocess.TimeoutExpired("fake", 7)
            return 0
        def poll(self): return None if fail else 0
        def kill(self): events.append("kill")
    class FakeJob:
        def assign(self, proc): events.append("assign")
        def close(self): events.append("close")
    def popen(argv, **kwargs):
        assert argv == ["synthetic", "argument with spaces"]
        assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["cwd"] == tmp_path
        return FakeProcess()
    monkeypatch.setattr(process_module, "WindowsJob", FakeJob)
    monkeypatch.setattr(process_module.subprocess, "Popen", popen)
    if process_module.os.name != "nt":
        monkeypatch.setattr(process_module.os, "killpg", lambda *a: events.append("close"))
    with (tmp_path / "stdout").open("wb") as stdout, (tmp_path / "stderr").open("wb") as stderr:
        kwargs = dict(cwd=tmp_path, stdout=stdout, stderr=stderr, timeout=7, on_started=lambda: events.append("running"))
        if fail:
            with pytest.raises(subprocess.TimeoutExpired):
                process_module.run_process(["synthetic", "argument with spaces"], **kwargs)
        else:
            assert process_module.run_process(["synthetic", "argument with spaces"], **kwargs) == 0
    assert "running" in events and "close" in events
    assert events[-1] == ("wait", 10)


def test_missing_physics_cannot_be_overridden_by_review(prepared, ready):
    package, automation = prepared
    _, _, config, inputs = ready
    with pytest.raises(ExecutionRefused) as exc:
        execute(package, automation, config, authorize=True, **inputs)
    assert "computational_domain_unresolved" in exc.value.report["blocking_reasons"]
    assert "operating_mode_unresolved" in exc.value.report["blocking_reasons"]
    assert not list((package.parent / "attempts").iterdir())


def test_existing_case_mode(ready, tmp_path, monkeypatch):
    package, automation, config, inputs = ready
    case = tmp_path / "input.cas.h5"
    case.write_bytes(b"inert existing case")
    inputs.update(input_mode="existing_case", input_artifact=case)
    inputs["contract"]["adapter_review"].update(input_mode="existing_case", input_sha256=sha256(case))
    assert prepare(package, automation, config, **inputs).to_dict()["m8b_ready"]
    def runner(argv, *, cwd, stdout, stderr, timeout, on_started):
        assert (cwd / "input.cas.h5").read_bytes() == b"inert existing case"
        on_started()
        write(cwd / "diagnostics.json", diagnostics())
        return 0
    monkeypatch.setattr(execution, "run_process", runner)
    assert execute(package, automation, config, authorize=True, **inputs)["solver_status"] == "completed"


def test_repeat_uses_new_m6_attempt_and_fresh_outputs(ready, monkeypatch):
    package, automation, config, inputs = ready
    fake_runner(monkeypatch, package)
    first = execute(package, automation, config, authorize=True, **inputs)
    fake_runner(monkeypatch, package, missing=True)
    with pytest.raises(FileNotFoundError):
        execute(package, automation, config, authorize=True, **inputs)
    attempts = list((package.parent / "attempts").glob("*.json"))
    assert len(attempts) == 2
    assert load_attempt(package, first["attempt_id"])["error"]["error_type"] == "unresolved_result_mapping"
    assert all(load_attempt(package, p.stem)["status"] == "failed" for p in attempts)


def test_final_gate_rechecks_mutated_input(ready, monkeypatch):
    package, automation, config, inputs = ready
    original = execution.shutil.copyfile
    def changed(source, destination):
        result = original(source, destination)
        if Path(destination).name == "run.jou":
            inputs["input_artifact"].write_bytes(b"changed after staging")
        return result
    monkeypatch.setattr(execution.shutil, "copyfile", changed)
    with pytest.raises(ExecutionRefused):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


def test_m7_tampering_rejected_before_attempt(ready):
    package, automation, config, inputs = ready
    with (automation.parent / "prepare.jou").open("ab") as stream:
        stream.write(b"; tampered\n")
    with pytest.raises(ValueError, match="mismatch"):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


def test_cli_explicit_execute_still_refuses_missing_inputs(ready, capsys):
    package, automation, config, _ = ready
    assert main(["--execute", "--package", str(package), "--automation-manifest", str(automation),
                 "--fluent-executable", config.executable, "--working-directory", config.working_directory]) == 2
    assert "geometry_or_mesh_input_required" in json.loads(capsys.readouterr().out)["blocking_reasons"]
    assert not list((package.parent / "attempts").iterdir())


def test_cli_local_config_and_environment(ready, tmp_path, capsys, monkeypatch):
    package, automation, config, _ = ready
    path = tmp_path / "local.json"
    write(path, {"working_directory": config.working_directory, "processes": 3})
    monkeypatch.setenv("FLUENT_EXECUTABLE", config.executable)
    assert main(["--package", str(package), "--automation-manifest", str(automation),
                 "--local-config", str(path), "--processes", "4"]) == 0
    assert json.loads(capsys.readouterr().out)["local_config"]["processes"] == 4


def test_source_packages_remain_byte_identical(ready, monkeypatch):
    package, automation, config, inputs = ready
    paths = [package, *automation.parent.iterdir(), package.parent / "geometry/coordinates.csv"]
    before = {p: p.read_bytes() for p in paths}
    fake_runner(monkeypatch, package)
    execute(package, automation, config, authorize=True, **inputs)
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_template_is_not_authorization(ready):
    package, automation, config, inputs = ready
    inputs["contract"] = json.loads((Path(__file__).parents[1] / "examples/m8b_launch_contract.template.json").read_text())
    with pytest.raises(ExecutionRefused):
        execute(package, automation, config, authorize=True, **inputs)


def test_runtime_cannot_write_into_source_tree(ready):
    package, automation, config, inputs = ready
    config = replace(config, working_directory=str(Path(__file__).parents[1]))
    with pytest.raises(ExecutionRefused, match="runtime_directory_not_ignored"):
        execute(package, automation, config, authorize=True, **inputs)


@pytest.mark.parametrize("state", ["missing", "unresolved", "pending_contract", "not_required"])
def test_research_readiness_is_execution_gate(ready, state):
    package, automation, config, inputs = ready
    review = inputs["contract"]["research_review"]
    if state == "missing":
        review.clear()
    else:
        review["operating_mode"]["status"] = state
    plan = prepare(package, automation, config, **inputs).to_dict()
    assert not plan["research_ready_for_single_nozzle_shakedown"]
    assert not plan["m8b_ready"]
    with pytest.raises(ExecutionRefused, match="single_nozzle_research_not_ready"):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


def test_unmapped_required_m8a_item_blocks_execution(ready, monkeypatch):
    package, automation, config, inputs = ready
    monkeypatch.setitem(plan_module.SHAKEDOWN_MAPPING, "initialization", "unimplemented_adapter")
    result = prepare(package, automation, config, **inputs).to_dict()
    mapping = result["m8a_readiness"]["mapping"]
    assert next(v for v in mapping if v["key"] == "initialization")["mapping_status"] == "pending_mapping_contract"
    with pytest.raises(ExecutionRefused):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


def test_shakedown_projection_keeps_h1_and_nox_unconfirmed(ready):
    package, automation, config, inputs = ready
    result = prepare(package, automation, config, **inputs).to_dict()
    gate = result["m8a_readiness"]["research_gate"]
    assert result["research_ready_for_single_nozzle_shakedown"] and result["m8b_ready"]
    assert not gate["pilot_ready"] and not result["m8a_readiness"]["execution_allowed"]
    assert all(m["gate_status"] == "confirmed" and m["mapping_status"] == "mapped"
               for m in result["m8a_readiness"]["mapping"])
    assert next(v for v in gate["items"] if v["key"] == "nox_unit")["status"] == "pending_contract"


def repackage(ready, tmp_path, mutate):
    package, _, config, inputs = ready
    original = load_cfd_package(package)
    physical = original["normalized_spec"]
    mutate(physical)
    package = export_cfd_package(package.parent / original["geometry"]["run_manifest"],
        CFDSpec.from_normalized(physical), output_root=tmp_path / "mutated", required_metrics=["pressure_loss"])
    automation = export_automation(package, tmp_path / "mutated_m7",
        numerical_settings={"max_iterations": {"value": 500, "unit": "1"}})
    inputs["contract"]["identity"] = prepare(package, automation, config).to_dict()["identity"]
    return package, automation, config, inputs


@pytest.mark.parametrize("inlet,fractions", [
    ("oxidizer_inlet", {"O2": .4, "N2": .6, "Ar": .1}),
    ("oxidizer_inlet", {"O2": .4, "N2": .5}),
    ("oxidizer_inlet", {"O2": -.1, "N2": 1.1}),
    ("oxidizer_inlet", {"O2": .5, "N2": .5}),
    ("fuel_inlet", {"H2": 1, "Ar": .1}),
    ("fuel_inlet", {"H2": .9}),
])
def test_exact_composition_rejects_invalid_package(ready, tmp_path, inlet, fractions):
    def mutate(p):
        p["operating_condition"]["extras"][inlet]["mass_fractions"] = {
            k: {"value": v, "unit": "1"} for k, v in fractions.items()}
    package, automation, config, inputs = repackage(ready, tmp_path, mutate)
    with pytest.raises(ExecutionRefused, match="baseline_physics_mismatch"):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


@pytest.mark.parametrize("field,value", [
    ("computational_domain", False), ("computational_domain", True),
    ("computational_domain", "not_required"), ("computational_domain", {"dimension": "3D"}),
    ("density_model", False), ("gravity_decision", False),
    ("radiation_model_decision", True), ("species_material_database", "confirmed"),
    ("inlet_topology", {"fuel_inlet": "circular", "oxidizer_inlet": "annular"}),
    ("inlet_topology", None), ("energy_equation", False),
])
def test_required_physical_fields_are_typed(ready, tmp_path, field, value):
    package, automation, config, inputs = repackage(ready, tmp_path,
        lambda p: p["simulation_config"]["extras"].__setitem__(field, value))
    with pytest.raises(ExecutionRefused):
        execute(package, automation, config, authorize=True, **inputs)
    assert not list((package.parent / "attempts").iterdir())


def test_composition_roundoff_tolerance(ready, tmp_path):
    def mutate(p):
        p["operating_condition"]["extras"]["oxidizer_inlet"]["mass_fractions"]["O2"]["value"] += 1e-14
    package, automation, config, inputs = repackage(ready, tmp_path, mutate)
    assert prepare(package, automation, config, **inputs).to_dict()["m8b_ready"]


@pytest.mark.parametrize("key", ["residuals", "mass_balance_monitor", "convergence_assessment"])
def test_required_numerical_fields_reject_false(ready, key):
    package, automation, config, inputs = ready
    inputs["contract"]["numerical_settings"][key] = False
    assert not prepare(package, automation, config, **inputs).to_dict()["m8b_ready"]


def test_hydraulic_diameter_is_derived_and_topology_is_gated(ready):
    baseline = load_baseline()
    geometry = baseline["confirmed_baseline"]["simulation_config"]["extras"]["nozzle_geometry"]
    for inlet, diameter in (("fuel_inlet", "dH"), ("oxidizer_inlet", "Da")):
        provenance = baseline["hydraulic_diameter_provenance"][inlet]
        assert provenance["kind"] == "derived_value"
        assert provenance["source"].endswith("_" + diameter)
        assert provenance["value"] == geometry[diameter]["value"]
        assert provenance["required_package_topology"] == "circular"


def only_attempt(package):
    files = list((package.parent / "attempts").glob("*.json"))
    assert len(files) == 1
    return load_attempt(package, files[0].stem)


@pytest.mark.parametrize("target", ["execution_report", "execution_link", "artifact_hash"])
def test_secondary_reporting_error_preserves_nonzero_terminal_state(ready, monkeypatch, target):
    package, automation, config, inputs = ready
    fake_runner(monkeypatch, package, code=9)
    original_write, original_hash = execution._write, execution.sha256
    def write_failure(path, data):
        if ((target == "execution_report" and path.name == "execution.json" and data.get("finished_at"))
                or (target == "execution_link" and path.parent.name == "execution_logs")):
            raise OSError("synthetic secondary reporting failure")
        return original_write(path, data)
    def hash_failure(path):
        if target == "artifact_hash" and Path(path).name == "stdout.log":
            raise OSError("synthetic secondary hash failure")
        return original_hash(path)
    monkeypatch.setattr(execution, "_write", write_failure)
    monkeypatch.setattr(execution, "sha256", hash_failure)
    with pytest.raises(RuntimeError, match="solver_nonzero_exit") as error:
        execute(package, automation, config, authorize=True, **inputs)
    assert error.value.secondary_reporting_failures
    record = only_attempt(package)
    assert record["status"] == "failed" and record["error"]["solver_exit_status"] == 9
    assert "solver_nonzero_exit" in record["error"]["message"]
    assert "secondary_reporting_failures" in record["error"]["message"]


@pytest.mark.parametrize("stage", ["diagnostic", "running_report", "final_report"])
def test_postprocessing_or_report_failure_always_finalizes(ready, monkeypatch, stage):
    package, automation, config, inputs = ready
    fake_runner(monkeypatch, package)
    original = execution._write
    def broken_report(path, data):
        if path.name == "execution.json" and (
                stage == "running_report" and data.get("solver_status") == "running"
                or stage == "final_report" and data.get("finished_at")):
            raise OSError("synthetic report failed")
        return original(path, data)
    def broken_diagnostic(*args): raise OSError("synthetic diagnostic post-processing failed")
    monkeypatch.setattr(execution, "_write", broken_report)
    if stage == "diagnostic": monkeypatch.setattr(execution, "extract_diagnostics", broken_diagnostic)
    with pytest.raises((OSError, execution.ExecutionFinalizationError)):
        execute(package, automation, config, authorize=True, **inputs)
    assert only_attempt(package)["status"] == "failed"


def test_terminal_update_failure_is_explicit_and_retains_primary(ready, monkeypatch):
    package, automation, config, inputs = ready
    fake_runner(monkeypatch, package, code=9)
    original, original_write = execution.import_cfd_results, execution._write
    def fail_terminal(path, record):
        if record["status"] == "failed": raise OSError("synthetic M6 terminal write failed")
        return original(path, record)
    def fail_report(path, data):
        if path.name == "execution.json" and data.get("finished_at"):
            raise OSError("synthetic secondary report failed")
        return original_write(path, data)
    monkeypatch.setattr(execution, "import_cfd_results", fail_terminal)
    monkeypatch.setattr(execution, "_write", fail_report)
    with pytest.raises(execution.AttemptTerminalUpdateError) as error:
        execute(package, automation, config, authorize=True, **inputs)
    assert error.value.primary_failure["solver_exit_status"] == 9
    assert error.value.secondary_failures and error.value.terminal_failure
    assert only_attempt(package)["status"] == "running"  # Explicitly reported persistence failure, not claimed durable.


def test_windows_job_assignment_failure_terminates_launcher_and_fails_attempt(ready, monkeypatch):
    package, automation, config, inputs = ready
    events = []
    class Job:
        def assign(self, child):
            events.append("assign_failed")
            raise OSError("synthetic Windows Job assignment failure")
        def close(self): events.append("job_closed")
    class Child:
        pid = 123
        def poll(self): return None
        def kill(self): events.append("launcher_killed")
        def wait(self, timeout): events.append("launcher_reaped"); return -1
    monkeypatch.setattr(process_module, "WindowsJob", Job)
    monkeypatch.setattr(process_module.subprocess, "Popen", lambda *a, **kw: Child())
    # This repository's target runtime is Windows; fake all process operations.
    monkeypatch.setattr(execution, "run_process", process_module.run_process)
    if process_module.os.name != "nt":
        # Force only the runner's platform decision without changing pathlib.
        from types import SimpleNamespace
        monkeypatch.setattr(process_module, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(process_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    with pytest.raises(OSError, match="Job assignment"):
        execute(package, automation, config, authorize=True, **inputs)
    assert events == ["assign_failed", "job_closed", "launcher_killed", "launcher_reaped"]
    record = only_attempt(package)
    assert record["status"] == "failed" and "Job assignment" in record["error"]["message"]
