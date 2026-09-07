"""Best-effort, read-only Git and runtime provenance (never case identity)."""

from __future__ import annotations

import platform
import subprocess
from importlib import metadata
from pathlib import Path

from config import PROJECT_ROOT


def collect_provenance(project_root: str | Path = PROJECT_ROOT) -> dict:
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project_root), *args], check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=5,
        ).stdout.strip()

    git_info = {"status": "unavailable", "commit": None, "branch": None,
                "working_tree": "unavailable", "dirty": None}
    try:
        commit = git("rev-parse", "HEAD")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        status = git("status", "--porcelain", "--untracked-files=normal")
        git_info.update(status="available", commit=commit, branch=branch,
                        dirty=bool(status), working_tree="dirty" if status else "clean")
    except (OSError, subprocess.SubprocessError) as exc:
        git_info["unavailable_reason"] = type(exc).__name__

    dependencies = {}
    for name in ("numpy", "pandas", "matplotlib", "pytest"):
        try:
            dependencies[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            dependencies[name] = None
    return {
        "git": git_info, "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(), "dependencies": dependencies,
        "project_version": None,  # Project has no independently declared release version.
    }
