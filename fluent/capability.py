"""Read-only discovery. File presence is not a working solver/license check."""

import os
from pathlib import Path
import re
import shutil


def audit_capability(executable=None, *, environment=None):
    """Never execute even a version command; return local, ephemeral provenance.

    Version hints from AWP_ROOT### are explicitly unverified installation labels.
    They must not be reported as an actual executed solver version.
    """
    env = dict(os.environ if environment is None else environment)
    candidates = []
    if executable is not None:
        candidates.append((str(executable), "explicit", None))
    else:
        for key in ("FLUENT_EXECUTABLE", "FLUENT_EXE"):
            if env.get(key):
                candidates.append((env[key], key, None))
        for key in sorted(env):
            if re.fullmatch(r"AWP_ROOT\d{3}", key) and env[key]:
                candidates.append((str(Path(env[key]) / "fluent/ntbin/win64/fluent.exe"), key, key[-3:]))
        found = shutil.which("fluent", path=env.get("PATH", ""))
        if found:
            candidates.append((found, "PATH", None))
    for candidate, source, hint in candidates:
        try:
            path = Path(candidate)
            if path.is_file() and path.name.lower() in {"fluent", "fluent.exe"}:
                return {"status": "detected", "executable": str(path.resolve()),
                        "version": "unknown", "installation_version_hint": hint,
                        "source": source, "executed": False,
                        "warning": "File presence only; version and license not verified"}
        except (OSError, ValueError):
            continue
    return {"status": "not_detected", "executable": None, "version": "unknown",
            "installation_version_hint": None, "source": None, "executed": False,
            "warning": "No solver launched; discovery failure does not block preparation"}
