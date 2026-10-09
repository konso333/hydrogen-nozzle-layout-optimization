"""Standalone trial lifecycle, reusing M8B's bounded process supervisor.

No M6 identity, research gate, baseline or production-result contract is changed.
"""

import copy
from datetime import datetime, timezone
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from coldflow_trial.config import PROJECT_ROOT, sha256, validate, write_json
from coldflow_trial.processes import cleanup_records
from fluent_execution.process import run_process


def execute(config, *, authorize=False):
    if authorize is not True:
        raise ValueError("Explicit --execute authorization is required")
    config = validate(copy.deepcopy(config))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(config["output_root"]) / f"coldflow_{stamp}_{uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    report = {"status": "preparing", "purpose": "engineering_coldflow", "formal_m8b_ready": False,
              "scientific_eligible": False, "run_directory": str(directory),
              "started_utc": datetime.now(timezone.utc).isoformat(), "solver": None}
    try:
        versions = {}
        for name in ("ansys-fluent-core", "h5py", "psutil"):
            try:
                versions[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                versions[name] = None
        files = [*sorted((PROJECT_ROOT / "coldflow_trial").glob("*.py")),
                 PROJECT_ROOT / "scripts/fluent_coldflow.py", PROJECT_ROOT / "requirements-fluent-coldflow.txt"]
        report["provenance"] = {"python": sys.version, "dependencies": versions,
            "source_sha256": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in files}}
        write_json(directory / "config.json", config)
        write_json(directory / "run.json", report)
        for kind, suffix in (("case", "cas.h5"), ("data", "dat.h5")):
            staged = directory / f"input.{suffix}"
            shutil.copyfile(config["input"][f"{kind}_file"], staged)
            if sha256(staged) != config["input"][f"{kind}_sha256"]:
                raise ValueError(f"Staged {kind} SHA-256 mismatch")

        def started():
            report["status"] = "running"
            write_json(directory / "run.json", report)

        with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
            code = run_process([sys.executable, "-u", "-m", "coldflow_trial.worker", str(directory)],
                               cwd=PROJECT_ROOT, stdout=stdout, stderr=stderr,
                               timeout=config["timeout_seconds"], on_started=started)
        report["worker_exit_code"] = code
        state = json.loads((directory / "solver_status.json").read_text(encoding="utf-8"))
        report["solver"] = state
        if code != 0 or state["status"] not in {"numerical_criteria_met", "iteration_budget_reached"}:
            raise RuntimeError(state.get("error", f"Solver worker exited {code}"))
        if not state["cleanup"]["all_tracked_processes_exited"]:
            raise RuntimeError("Worker did not verify tracked processes exited")
        final_files = state.get("final_files", [])
        if {entry["file"] for entry in final_files} != {"final.cas.h5", "final.dat.h5"}:
            raise RuntimeError("Final case/data manifest missing")
        for entry in final_files:
            if sha256(directory / entry["file"]) != entry["sha256"]:
                raise RuntimeError("Final artifact hash mismatch")
        report["status"] = state["status"]
    except BaseException as error:
        report.update(status="timeout" if isinstance(error, subprocess.TimeoutExpired) else
                      "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      error=f"{type(error).__name__}: {error}")
    finally:
        # Reporting failures must not prevent process checks or leave run.json running.
        secondary = []
        try:
            path = directory / "owned_processes.json"
            report["cleanup"] = cleanup_records(json.loads(path.read_text(encoding="utf-8")) if path.exists() else [])
            if not report["cleanup"]["all_tracked_processes_exited"]:
                raise RuntimeError("Some tracked Fluent processes remain or could not be inspected")
        except Exception as error:
            secondary.append(f"Cleanup verification: {error}")
        try:
            report["source_files_unchanged"] = all(sha256(config["input"][f"{kind}_file"])
                == config["input"][f"{kind}_sha256"] for kind in ("case", "data"))
            if not report["source_files_unchanged"]:
                raise RuntimeError("Source case/data hashes changed")
        except Exception as error:
            secondary.append(f"Source verification: {error}")
        if report["solver"] is None and (directory / "solver_status.json").exists():
            try:
                report["solver"] = json.loads((directory / "solver_status.json").read_text(encoding="utf-8"))
            except Exception as error:
                secondary.append(f"Worker status: {error}")
        if secondary:
            report["secondary_errors"] = secondary
            report["status"] = "failed"
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(directory / "run.json", report)
    summary = ["# 冷态通流工程试算", "", f"状态：`{report['status']}`", "",
               "反应关闭，已有 case/data 热启动；正式 M8B readiness 未变，结果不自动进入科研优化数据。", ""]
    if report["solver"]:
        state = report["solver"]
        summary.append(f"本次已确认新增步数：{state['additional_iterations']}。")
        if state["history"]:
            last = state["history"][-1]
            summary.extend([f"末次 H₂ 缩放残差：{last['residuals']['h2']:.6g}。",
                            f"总质量相对不平衡：{last['relative_mass_imbalance']:.6g}（比例）。",
                            f"H₂ 对流通量相对不平衡：{last['h2_relative_convective_imbalance']:.6g}（未单独计扩散通量）。",
                            f"入口到出口面积加权总压差（Pa）：{last['area_weighted_total_pressure_drop_Pa']}。"])
    if report.get("error"):
        summary.extend(["", report["error"]])
    summary.extend(["", "达到步数预算不等于数值收敛；数值检查通过不等于物理验证或网格无关性验证。", ""])
    try:
        (directory / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    except Exception as error:
        report.update(status="failed", summary_error=str(error))
        write_json(directory / "run.json", report)
        raise
    return report
