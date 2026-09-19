"""The sole solver entry point. Fresh gates precede each M6 attempt."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from cfd import create_attempt, load_attempt, import_cfd_results
from fluent_execution.plan import prepare, sha256
from fluent_execution.process import run_process
from fluent_execution.results import extract_diagnostics, result_status


class ExecutionRefused(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(", ".join(report["blocking_reasons"]))


class ExecutionFinalizationError(RuntimeError):
    """Expose primary and secondary failures without losing either."""
    def __init__(self, primary, secondary, terminal=None):
        self.primary_failure = primary
        self.secondary_failures = secondary
        self.terminal_failure = terminal
        super().__init__(json.dumps({"primary_execution_failure": primary,
                                     "secondary_reporting_failures": secondary,
                                     "terminal_state_update_failure": terminal}, ensure_ascii=False))


class AttemptTerminalUpdateError(ExecutionFinalizationError):
    """Serious: M6 may still be running; no claim of durable termination."""


def _write(path, data):
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(text)
    temporary.replace(path)


def _finalize(package_path, attempt, stage, report):
    """Auxiliary failures cannot prevent the independent M6 terminal attempt.

    Collect reporting failures before the one immutable terminal M6 write, so
    the original error and all known secondary failures can be recorded there.
    Failure of M6 itself is exposed explicitly, never downgraded to a warning.
    """
    secondary = report["secondary_errors"]
    def auxiliary(label, operation):
        try:
            return operation()
        except BaseException as exc:
            secondary.append({"stage": label, "error_type": type(exc).__name__, "message": str(exc)})
            return None
    log_reference = None
    try:
        for name in ("stdout.log", "stderr.log", "run.jou", "contract.json"):
            def capture(name=name):
                file = stage / name
                if file.is_file():
                    report["artifacts"][name] = {"sha256": sha256(file), "bytes": file.stat().st_size}
            auxiliary("artifact_capture:" + name, capture)
        auxiliary("execution_report", lambda: _write(stage / "execution.json", report))
        if attempt is not None:
            def link_report():
                directory = Path(package_path).resolve().parent / "execution_logs"
                directory.mkdir(exist_ok=True)
                link = directory / (attempt.stem + ".json")
                _write(link, {"attempt_id": attempt.stem, "purpose": "shakedown", "scientific_eligible": False,
                    "solver_status": report["solver_status"], "solver_exit_status": report["solver_exit_status"],
                    "artifacts": report["artifacts"], "primary_error": report["error"],
                    "secondary_errors": secondary})
                return link.relative_to(Path(package_path).resolve().parent).as_posix()
            log_reference = auxiliary("execution_link", link_report)
    finally:
        if attempt is not None:
            try:
                record = load_attempt(package_path, attempt.stem)
                record["status"] = "failed"
                error = report["error"] or {"error_type": "execution_incomplete", "message": "Execution interrupted"}
                message = error["message"]
                if secondary:
                    message += "; secondary_reporting_failures=" + json.dumps(secondary, ensure_ascii=False)
                record["error"] = {"error_type": error["error_type"], "message": message,
                    "solver_exit_status": report["solver_exit_status"], "relative_log_path": log_reference}
                import_cfd_results(package_path, record)
            except BaseException as exc:
                raise AttemptTerminalUpdateError({**report["error"], "solver_exit_status": report["solver_exit_status"]}, secondary,
                    {"error_type": type(exc).__name__, "message": str(exc)}) from exc


def execute(package_path, automation_manifest, local_config, *, authorize=False, **inputs):
    """No execution by default. Never trust a saved readiness report.

    M6 completed means formal required metrics exist. Until a verified extractor
    is implemented, a successful shakedown process has solver_status=completed
    but its M6 attempt terminates failed/unresolved_result_mapping.
    """
    plan = prepare(package_path, automation_manifest, local_config, **inputs).to_dict()
    if authorize is not True:
        plan["blocking_reasons"].append("explicit_execution_authorization_required")
    if plan["blocking_reasons"]:
        raise ExecutionRefused(plan)
    # Detach caller-owned nested configuration before any filesystem mutation.
    contract = json.loads(json.dumps(inputs["contract"], allow_nan=False))
    work = Path(local_config.working_directory).resolve()
    work.mkdir(parents=True, exist_ok=True)
    # This is a staging/storage directory, never a competing execution identity.
    stage = Path(tempfile.mkdtemp(prefix="m8b-", dir=work))
    report = {**plan, **result_status(), "execution_allowed": True, "attempt_id": None,
              "runtime_directory": str(stage), "solver_exit_status": None,
              "started_at": None, "finished_at": None, "artifacts": {}, "error": None, "secondary_errors": []}
    attempt = None
    primary_exception = None
    try:
        source = Path(plan["input_artifact"])
        suffix = next(ext for ext in (".msh.h5", ".cas.h5", ".msh", ".cas")
                      if source.name.lower().endswith(ext))
        staged_input = stage / ("input" + suffix)
        shutil.copyfile(source, staged_input)
        shutil.copyfile(plan["journal_artifact"], stage / "run.jou")
        if sha256(staged_input) != plan["input_sha256"] or sha256(stage / "run.jou") != plan["journal_sha256"]:
            raise ValueError("staged_artifact_checksum_mismatch")
        _write(stage / "contract.json", contract)
        # Recheck sources, executable, review, resources and M6/M7 after staging.
        fresh = prepare(package_path, automation_manifest, local_config, **{**inputs, "contract": contract}).to_dict()
        if fresh["blocking_reasons"] or fresh["identity"] != plan["identity"]:
            raise ExecutionRefused(fresh)
        with (stage / "stdout.log").open("xb") as stdout, (stage / "stderr.log").open("xb") as stderr:
            _write(stage / "execution.json", report)
            settings = {**plan["numerical_settings"], "purpose": "shakedown", "scientific_eligible": False,
                        "automation_digest": plan["identity"]["automation_digest"]}
            # No further journal generation or input validation after this point.
            attempt = create_attempt(package_path, solver_version="user-reviewed:2025 R2",
                                     data_kind="external_cfd", numerical_settings=settings)
            report["attempt_id"] = attempt.stem
            report["attempt_created"] = True
            def started():
                record = load_attempt(package_path, attempt.stem)
                record["status"] = "running"
                import_cfd_results(package_path, record)
                report["solver_status"] = "running"
                report["started_at"] = datetime.now(timezone.utc).isoformat()
                _write(stage / "execution.json", report)
            report["solver_exit_status"] = run_process(plan["argv_preview"], cwd=stage, stdout=stdout,
                stderr=stderr, timeout=plan["timeout_seconds"], on_started=started)
        if report["solver_exit_status"] != 0:
            raise RuntimeError("solver_nonzero_exit")
        for name in plan["expected_outputs"]:
            artifact = stage / name
            if artifact.is_symlink() or not artifact.resolve().is_relative_to(stage) or not artifact.is_file() or artifact.stat().st_size == 0:
                raise FileNotFoundError("missing_output_artifact: " + name)
            report["artifacts"][name] = {"sha256": sha256(artifact), "bytes": artifact.stat().st_size}
        report["solver_status"] = "completed"
        if contract["diagnostics"] is not None:
            report["diagnostics"] = extract_diagnostics(stage / contract["diagnostics"]["artifact"])
        report["error"] = {"error_type": "unresolved_result_mapping",
                           "message": "Solver exited successfully; M6 required metrics have no verified extractor"}
    except BaseException as exc:
        primary_exception = exc
        report["solver_status"] = "failed" if attempt else "not_started"
        kind = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else type(exc).__name__
        report["error"] = {"error_type": kind, "message": str(exc)}
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _finalize(package_path, attempt, stage, report)
    if primary_exception is not None:
        primary_exception.secondary_reporting_failures = report["secondary_errors"]
        raise primary_exception.with_traceback(primary_exception.__traceback__)
    if report["secondary_errors"]:
        raise ExecutionFinalizationError({**report["error"], "solver_exit_status": report["solver_exit_status"]}, report["secondary_errors"])
    return report
