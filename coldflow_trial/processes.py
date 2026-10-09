"""Track only newly launched Fluent descendants, checking PID reuse before cleanup."""

import os
from pathlib import Path
import re
import time

from coldflow_trial.config import write_json


class OwnedProcesses:
    def __init__(self, executable, directory):
        import psutil
        self.psutil = psutil
        self.installation = Path(executable).parents[3]
        self.directory = Path(directory)
        self.preexisting = set(psutil.pids())
        self.started = time.time()
        self.owned = {}

    def capture(self, session=None):
        psutil = self.psutil
        candidates = {p.pid: p for p in psutil.Process().children(recursive=True)}
        if session is not None:
            props = session.connection_properties
            for pid in (props.cortex_pid, props.fluent_host_pid):
                try:
                    process = psutil.Process(int(pid))
                    candidates[process.pid] = process
                    candidates.update((p.pid, p) for p in process.children(recursive=True))
                except (TypeError, ValueError, psutil.NoSuchProcess):
                    continue
        transcript = self.directory / "fluent.trn"
        if transcript.exists():
            pattern = r"^\s*(?:n\d+\*?|host)\s+\S+\s+(?:\d+/\d+\s+)?Windows-x64\s+(\d+)"
            for match in re.finditer(pattern, transcript.read_text(encoding="utf-8", errors="replace"), re.M):
                try:
                    process = psutil.Process(int(match.group(1)))
                    candidates[process.pid] = process
                    for parent in process.parents():
                        if parent.pid in self.preexisting:
                            break
                        candidates[parent.pid] = parent
                except psutil.NoSuchProcess:
                    continue
        for process in candidates.values():
            try:
                executable = Path(process.exe())
                if process.pid in self.preexisting or process.pid == os.getpid():
                    continue
                if process.create_time() < self.started - 2 or not executable.is_relative_to(self.installation):
                    continue
                if executable.name.lower() in {"ansyslmd.exe", "ansysli_server.exe", "ansysli_monitor.exe"}:
                    continue
                self.owned[process.pid] = {"pid": process.pid, "created_epoch": process.create_time(),
                                           "executable": str(executable)}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        write_json(self.directory / "owned_processes.json", list(self.owned.values()))


def cleanup_records(records):
    import psutil
    errors = []

    def remaining():
        live = []
        unknown = []
        for item in records:
            try:
                process = psutil.Process(item["pid"])
                if (abs(process.create_time() - item["created_epoch"]) < .01
                        and process.exe().lower() == item["executable"].lower()):
                    live.append(process)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                unknown.append(item["pid"])
        return live, unknown

    live, _ = remaining()
    for process in live:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            errors.append(str(error))
    psutil.wait_procs(live, timeout=5)
    live, _ = remaining()
    for process in live:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            errors.append(str(error))
    psutil.wait_procs(live, timeout=5)
    live, unknown = remaining()
    return {"tracked_process_count": len(records), "remaining_process_ids": [p.pid for p in live],
            "unverified_process_ids": unknown, "all_tracked_processes_exited": not live and not unknown,
            "cleanup_messages": errors}
