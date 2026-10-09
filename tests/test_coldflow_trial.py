"""Cold-flow configuration/lifecycle tests; no Fluent or license process is launched."""

import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest

from coldflow_trial import config as configuration
from coldflow_trial import execution, solver
from coldflow_trial.config import EQUATIONS, sha256, write_json
from scripts.fluent_coldflow import main


@pytest.fixture(autouse=True)
def forbid_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No real subprocesses in cold-flow tests")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def configured(tmp_path):
    config = json.loads((configuration.PROJECT_ROOT / "examples/coldflow.template.json").read_text(encoding="utf-8"))
    for kind, suffix in (("case", "cas.h5"), ("data", "dat.h5")):
        path = tmp_path / f"source.{suffix}"
        path.write_bytes(b"\x89HDF\r\n\x1a\nINERT TEST ONLY " + kind.encode())
        config["input"][f"{kind}_file"] = str(path.resolve())
        config["input"][f"{kind}_sha256"] = sha256(path)
    executable = tmp_path / "ansys/v252/fluent/ntbin/win64/fluent.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"INERT TEST ONLY -- never execute")
    config["fluent"]["executable"] = str(executable.resolve())
    # Static inspection never creates this ASCII directory. Execution tests
    # replace validation after separately testing it and use workspace tmp_path.
    config["output_root"] = str(Path(tmp_path.anchor) / "coldflow-inert-test-output")
    return config


def test_template_is_inert():
    template = json.loads((configuration.PROJECT_ROOT / "examples/coldflow.template.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="absolute local path"):
        configuration.validate(template)


def test_inspect_is_read_only_without_optional_solver_imports(configured, tmp_path, monkeypatch, capsys):
    import builtins
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("ansys", "h5py", "psutil")):
            raise AssertionError("Inspection must not import solver dependencies")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    path = tmp_path / "config.json"
    write_json(path, configured)
    before = set(tmp_path.rglob("*"))
    assert main(["--config", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["input_hashes_verified"] and not report["execution_allowed"]
    assert not report["formal_m8b_ready"] and not report["scientific_eligible"]
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("field,value", [
    ("purpose", "scientific"), ("model", "k-epsilon"), ("schema_version", True),
    ("timeout_seconds", float("nan")), ("minimum_free_bytes", True),
])
def test_rejects_invalid_top_level(configured, field, value):
    configured[field] = value
    with pytest.raises(ValueError):
        configuration.validate(configured)


@pytest.mark.parametrize("key,value", [("maximum", True), ("block_size", 501), ("minimum", 501), ("maximum", 0)])
def test_rejects_invalid_iteration_budget(configured, key, value):
    configured["iterations"][key] = value
    with pytest.raises(ValueError):
        configuration.validate(configured)


def test_rejects_changed_source(configured):
    Path(configured["input"]["data_file"]).write_bytes(b"\x89HDF\r\n\x1a\nchanged")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        configuration.validate(configured)


def test_rejects_unknown_config_and_reused_boundary(configured):
    bad = copy.deepcopy(configured)
    bad["force"] = True
    with pytest.raises(ValueError, match="expected exactly"):
        configuration.validate(bad)
    configured["boundaries"]["wall"] = configured["boundaries"]["outlet"]
    with pytest.raises(ValueError, match="distinct"):
        configuration.validate(configured)


def test_authorization_precedes_any_filesystem_write(configured, tmp_path):
    with pytest.raises(ValueError, match="authorization"):
        execution.execute(configured)


def transcript_row(index, residual=1e-5):
    return f"{index} " + " ".join(str(residual) for _ in EQUATIONS) + " 0:00:01 5"


def test_transcript_excludes_initial_row_and_deduplicates_batch_boundary():
    header = "iter " + " ".join(EQUATIONS) + " time/iter"
    text = "\n".join([header, transcript_row(500), transcript_row(501), transcript_row(502),
                       header, transcript_row(502), transcript_row(503)])
    assert solver.parse_residuals(text, 500, 503) == dict.fromkeys(EQUATIONS, 1e-5)


@pytest.mark.parametrize("row", [transcript_row(502), transcript_row(501, float("inf"))])
def test_missing_or_invalid_residual_history_fails(row):
    with pytest.raises(ValueError):
        solver.parse_residuals("iter " + " ".join(EQUATIONS) + " time/iter\n" + row, 500, 501)


def physics_state(config):
    def constant(value):
        return {"option": "value", "value": value}

    def species(field, fuel=False):
        return {"specify_species_in_mole_fractions": False,
                field: {name: constant(value) for name, value in
                        {"h2": 1 if fuel else 0, "o2": 0 if fuel else .4, "h2o": 0}.items()}}

    inlet = lambda fuel: {"momentum": {"velocity_specification_method": "Magnitude, Normal to Boundary",
                                      "velocity_magnitude": constant(10)},
                           "thermal": {"temperature": constant(300)}, "species": species("species_mass_fraction", fuel)}
    names = config["boundaries"]
    return {"general": {"solver": {"type": "pressure-based", "time": "steady"},
                        "operating_conditions": {"gravity": {"enable": False}, "operating_pressure": 101325}},
            "models": {"viscous": {"model": "laminar"}, "energy": {"enabled": True},
                       "species": {"model": {"option": "species-transport"},
                                   "reactions": {"enable_volumetric_reactions": False}},
                       "multiphase": {"model": "none"}, "radiation": {"model": "none"}},
            "boundary_conditions": {"velocity_inlet": {names["fuel_inlet"]: inlet(True), names["oxidizer_inlet"]: inlet(False)},
                "pressure_outlet": {names["outlet"]: {"momentum": {"gauge_pressure": constant(0)},
                    "thermal": {"backflow_total_temperature": constant(300)}, "species": species("backflow_species_mass_fraction")}},
                "wall": {names["wall"]: {"momentum": {"wall_motion": "Stationary Wall", "shear_condition": "No Slip"},
                    "thermal": {"thermal_condition": "Heat Flux", "heat_flux": constant(0)}}}},
            "cell_zones": {"fluid": {"fluid": {"general": {"material": "test-mixture"}}}},
            "materials": {"mixture": {"test-mixture": {"species": {"volumetric_species": dict.fromkeys(("h2", "o2", "n2", "h2o"), {})}}}},
            "controls": {}, "methods": {"spatial_discretization": {"discretization_scheme":
                dict.fromkeys(("mom", "temperature", "species-0", "species-1", "species-2", "pressure"), "inherited")}}}


@pytest.mark.parametrize("fault", ["reaction", "velocity", "mole_fraction", "extra_species", "extra_inlet", "wall_heat"])
def test_rejects_incompatible_physics_before_solving(configured, fault):
    state = physics_state(configured)
    if fault == "reaction":
        state["models"]["species"]["reactions"]["enable_volumetric_reactions"] = True
    elif fault == "velocity":
        state["boundary_conditions"]["velocity_inlet"]["fuel_inlet"]["momentum"]["velocity_magnitude"]["value"] = 11
    elif fault == "mole_fraction":
        state["boundary_conditions"]["velocity_inlet"]["fuel_inlet"]["species"]["specify_species_in_mole_fractions"] = True
    elif fault == "extra_species":
        state["materials"]["mixture"]["test-mixture"]["species"]["volumetric_species"]["ar"] = {}
    elif fault == "extra_inlet":
        state["boundary_conditions"]["mass_flow_inlet"] = {"extra": {"name": "extra"}}
    else:
        state["boundary_conditions"]["wall"]["wall"]["thermal"]["heat_flux"]["value"] = 5
    with pytest.raises(ValueError):
        solver.verify_physics(state, configured, laminar=True)


def test_source_preservation_detects_numerical_change(configured):
    before = physics_state(configured)
    after = copy.deepcopy(before)
    after["methods"]["spatial_discretization"]["discretization_scheme"]["mom"] = "changed"
    with pytest.raises(ValueError, match="numerical scheme"):
        solver.verify_preserved(before, after, configured)


def test_only_inactive_turbulence_controls_may_disappear(configured):
    before = physics_state(configured)
    after = copy.deepcopy(before)
    before["controls"] = {"under_relaxation": {"k": .8, "epsilon": .8, "turb-viscosity": 1, "pressure": .3}}
    after["controls"] = {"under_relaxation": {"pressure": .3}}
    solver.verify_preserved(before, after, configured)
    after["controls"]["under_relaxation"]["pressure"] = .4
    with pytest.raises(ValueError, match="active controls"):
        solver.verify_preserved(before, after, configured)


def test_cleanup_does_not_kill_reused_pid(monkeypatch):
    import sys
    from coldflow_trial.processes import cleanup_records
    class Gone(Exception):
        pass
    class Denied(Exception):
        pass
    process = NS(create_time=lambda: 200, exe=lambda: "fluent.exe",
                 terminate=lambda: pytest.fail("Do not kill a reused PID"),
                 kill=lambda: pytest.fail("Do not kill a reused PID"))
    monkeypatch.setitem(sys.modules, "psutil", NS(Process=lambda pid: process, NoSuchProcess=Gone,
                                               AccessDenied=Denied, wait_procs=lambda *args, **kwargs: None))
    cleanup = cleanup_records([{"pid": 123, "created_epoch": 100, "executable": "fluent.exe"}])
    assert cleanup["all_tracked_processes_exited"]


def test_cleanup_does_not_claim_success_when_access_denied(monkeypatch):
    import sys
    from coldflow_trial.processes import cleanup_records
    class Gone(Exception):
        pass
    class Denied(Exception):
        pass
    def denied(pid):
        raise Denied("cannot inspect")
    monkeypatch.setitem(sys.modules, "psutil", NS(Process=denied, NoSuchProcess=Gone,
                                               AccessDenied=Denied, wait_procs=lambda *args, **kwargs: None))
    cleanup = cleanup_records([{"pid": 123, "created_epoch": 100, "executable": "fluent.exe"}])
    assert not cleanup["all_tracked_processes_exited"] and cleanup["unverified_process_ids"] == [123]


def clean():
    return {"all_tracked_processes_exited": True, "cleanup_messages": []}


@pytest.fixture
def fake_session(configured, tmp_path, monkeypatch):
    directory = tmp_path / "trial"
    directory.mkdir()
    for kind, suffix in (("case", "cas.h5"), ("data", "dat.h5")):
        (directory / f"input.{suffix}").write_bytes(Path(configured["input"][f"{kind}_file"]).read_bytes())
    iteration = [500]
    requested = []
    closed = []
    events = []
    saved = {"input.dat.h5": 500}

    def write_case_data(file_name):
        case = Path(file_name)
        case.write_bytes(b"INERT case")
        data = case.with_name(case.name.replace(".cas.h5", ".dat.h5"))
        data.write_bytes(b"INERT data")
        saved[data.name] = iteration[0]
        events.append(("save", case.name))

    def iterate(iter_count):
        requested.append(iter_count)
        with (directory / "fluent.trn").open("a", encoding="utf-8") as stream:
            stream.write("iter " + " ".join(EQUATIONS) + " time/iter\n")
            stream.write(transcript_row(iteration[0]) + "\n")
            for _ in range(iter_count):
                iteration[0] += 1
                stream.write(transcript_row(iteration[0]) + "\n")

    class Equations(dict):
        def get_object_names(self):
            return list(self)

    residual = NS(equations=Equations({name: NS() for name in EQUATIONS}), options=NS(residual_values=NS()))
    file = NS(start_transcript=lambda **kwargs: None, read_case_data=lambda **kwargs: None,
              write_case_data=write_case_data, stop_transcript=lambda: None)
    session = NS(settings=NS(file=file, setup=NS(models=NS(viscous=NS(model="laminar"))),
                 solution=NS(monitor=NS(residual=residual), run_calculation=NS(iterate=iterate)),
                 mesh=NS(check=lambda: None, quality=lambda: None)), exit=lambda **kwargs: closed.append(True))

    class Tracker:
        owned = {}
        def __init__(self, *args):
            pass
        def capture(self, *args):
            pass

    monkeypatch.setattr(solver, "mesh_fingerprint", lambda path: "unchanged")
    monkeypatch.setattr(solver, "saved_iteration", lambda path: saved[Path(path).name])
    monkeypatch.setattr(solver, "snapshot", lambda session: physics_state(configured))
    monkeypatch.setattr(solver, "cleanup_records", lambda records: clean())

    def reports(session, config, previous):
        events.append(("report", iteration[0]))
        return {"relative_mass_imbalance": 1e-6, "h2_relative_convective_imbalance": 1e-6,
                "relative_pressure_drop_change": .001 if previous else None,
                "area_weighted_total_pressure_drop_Pa": {"fuel_inlet": 46., "oxidizer_inlet": 244.}}

    monkeypatch.setattr(solver, "block_reports", reports)
    configured["iterations"] = {"maximum": 5, "block_size": 2, "minimum": 3, "consecutive_passing_blocks": 2}
    # Keep the fake H2 residual above criterion to exercise exact budget exhaustion.
    configured["convergence"]["scaled_residuals"]["h2"] = 1e-6
    return NS(config=configured, directory=directory, session=session, tracker=Tracker,
              requested=requested, closed=closed, events=events)


def run_fake(fake):
    return solver.run_session(fake.config, fake.directory,
                              session_factory=lambda *args: fake.session, tracker_factory=fake.tracker)


def test_budget_remainder_and_checkpoint_before_reports(fake_session):
    state = run_fake(fake_session)
    assert state["status"] == "iteration_budget_reached"
    assert state["additional_iterations"] == 5 and state["solver_iteration"] == 505
    assert fake_session.requested == [2, 2, 1] and fake_session.closed == [True]
    for index, event in enumerate(fake_session.events):
        if event[0] == "report":
            assert fake_session.events[index - 1][0] == "save"
    assert (fake_session.directory / "final.cas.h5").exists()
    assert not state["numerical_criteria_met"]


def test_consecutive_passing_blocks_required(fake_session):
    fake_session.config["iterations"]["maximum"] = 10
    fake_session.config["convergence"]["scaled_residuals"] = dict.fromkeys(EQUATIONS, 1e-4)
    state = run_fake(fake_session)
    assert state["status"] == "numerical_criteria_met"
    assert fake_session.requested == [2, 2, 2]  # First block has no pressure stability comparison.


def test_report_failure_preserves_completed_batch_and_exits(fake_session, monkeypatch):
    def broken(*args):
        raise RuntimeError("synthetic report failure")
    monkeypatch.setattr(solver, "block_reports", broken)
    state = run_fake(fake_session)
    assert state["status"] == "failed" and state["additional_iterations"] == 2
    assert (fake_session.directory / "checkpoint_000002.cas.h5").exists()
    assert fake_session.closed == [True] and "report failure" in state["error"]


def test_worker_reporting_failure_still_closes_and_records_terminal_state(fake_session, monkeypatch):
    original = solver.write_json
    def broken(path, value):
        if Path(path).name == "configured_state.json":
            raise OSError("synthetic reporting error")
        original(path, value)
    monkeypatch.setattr(solver, "write_json", broken)
    state = run_fake(fake_session)
    assert state["status"] == "failed" and not fake_session.requested
    assert fake_session.closed == [True]
    assert json.loads((fake_session.directory / "solver_status.json").read_text(encoding="utf-8"))["status"] == "failed"


@pytest.fixture
def parent_config(configured, tmp_path, monkeypatch):
    configuration.validate(copy.deepcopy(configured))
    configured["output_root"] = str(tmp_path / "runs")
    monkeypatch.setattr(execution, "validate", lambda value: value)
    monkeypatch.setattr(execution, "cleanup_records", lambda records: clean())
    return configured


def test_parent_timeout_records_terminal_status(parent_config, monkeypatch):
    def timeout(argv, **kwargs):
        kwargs["on_started"]()
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(execution, "run_process", timeout)
    report = execution.execute(parent_config, authorize=True)
    assert report["status"] == "timeout" and report["source_files_unchanged"]
    saved = json.loads((Path(report["run_directory"]) / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "timeout"


def test_parent_failure_never_leaves_running(parent_config, monkeypatch):
    def fail(argv, **kwargs):
        kwargs["on_started"]()
        return 1
    monkeypatch.setattr(execution, "run_process", fail)
    report = execution.execute(parent_config, authorize=True)
    assert report["status"] == "failed" and report["source_files_unchanged"]
    assert json.loads((Path(report["run_directory"]) / "run.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_parent_success_and_new_run_directory(parent_config, monkeypatch):
    def fake_worker(argv, **kwargs):
        kwargs["on_started"]()
        directory = Path(argv[-1])
        assert kwargs["timeout"] == parent_config["timeout_seconds"]
        assert (directory / "input.cas.h5").exists()
        final_files = []
        for name in ("final.cas.h5", "final.dat.h5"):
            path = directory / name
            path.write_bytes(b"INERT final")
            final_files.append({"file": name, "sha256": sha256(path)})
        write_json(directory / "solver_status.json", {"status": "iteration_budget_reached", "cleanup": clean(),
            "final_files": final_files, "additional_iterations": 5, "history": []})
        return 0
    monkeypatch.setattr(execution, "run_process", fake_worker)
    first = execution.execute(parent_config, authorize=True)
    second = execution.execute(parent_config, authorize=True)
    assert first["status"] == "iteration_budget_reached"
    assert first["run_directory"] != second["run_directory"]
    assert Path(first["run_directory"], "summary.md").exists()
