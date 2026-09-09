"""Machine-local planning only; never passed into M2/M6/M7 specifications."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from fluent import audit_capability


@dataclass(frozen=True)
class LocalFluentConfig:
    executable: str
    working_directory: str
    dimension: str = "3D"
    precision: str = "double"
    processes: int = 2
    plan_create_directory: bool = False
    declared_product: str | None = None
    declared_release: str | None = None
    installation_hint: str | None = None

    def __post_init__(self):
        for key in ("executable", "working_directory"):
            value = os.fspath(getattr(self, key))
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                raise ValueError(f"{key} requires a nonempty local path")
            object.__setattr__(self, key, value)
        if type(self.plan_create_directory) is not bool:
            raise ValueError("plan_create_directory requires bool")
        for key in ("declared_product", "declared_release", "installation_hint"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{key} requires explicit nonempty declaration")

    @classmethod
    def from_environment(cls, *, environment=None, **overrides):
        env = os.environ if environment is None else environment
        values = {"executable": env.get("FLUENT_EXECUTABLE"),
                  "working_directory": env.get("FLUENT_WORKING_DIRECTORY")}
        if "FLUENT_PROCESSES" in env and overrides.get("processes") is None:
            values["processes"] = int(env["FLUENT_PROCESSES"])
        values.update({k: v for k, v in overrides.items() if v is not None})
        if any(values[k] is None for k in ("executable", "working_directory")):
            raise ValueError("Provide executable and working directory via CLI or environment")
        return cls(**values)


def _local_absolute(value):
    windows = PureWindowsPath(value)
    return (Path(value).is_absolute() and not value.startswith(("\\\\", "//"))
            and not str(windows.drive).startswith("\\\\"))


def _probe_writable(directory):
    # One exclusive attempt: Windows tempfile may retry permission failures
    # thousands of times when os.access disagrees with the sandbox/ACL.
    probe = directory / (".m8a-write-probe-" + uuid4().hex)
    try:
        stream = probe.open("xb")
    except OSError as exc:
        return {"status": "create_failed", "error_type": type(exc).__name__, "residual_probe_path": None}
    try:
        stream.close()
        probe.unlink()
    except OSError as exc:
        return {"status": "cleanup_failed", "error_type": type(exc).__name__, "residual_probe_path": str(probe)}
    return {"status": "writable", "error_type": None, "residual_probe_path": None}


def inspect_environment(config):
    """Check file presence and directory write access without launching anything.

    A temporary empty file probes existing directory/nearest parent permissions;
    cleanup is attempted immediately and any failure is reported. Planned
    directories are never created here.
    """
    checks = {}
    exe, work = Path(config.executable), Path(config.working_directory)
    checks["executable_absolute_local"] = _local_absolute(config.executable)
    checks["executable_filename"] = exe.name.lower() in {"fluent.exe", "fluent"}
    checks["executable_exists"] = exe.is_file()
    checks["working_directory_absolute_local"] = _local_absolute(config.working_directory)
    checks["dimension"] = config.dimension == "3D"
    checks["precision"] = config.precision == "double"
    checks["processes"] = type(config.processes) is int and config.processes > 0
    directory_status = "invalid"
    writable = False
    probe_report = {"status": "not_attempted", "error_type": None, "residual_probe_path": None}
    if checks["working_directory_absolute_local"]:
        candidate = work
        if not work.exists() and config.plan_create_directory:
            while not candidate.exists() and candidate != candidate.parent:
                candidate = candidate.parent
            directory_status = "planned_creation"
        elif work.is_dir():
            directory_status = "existing"
        if directory_status != "invalid" and candidate.is_dir():
            probe_report = _probe_writable(candidate)
            writable = probe_report["status"] == "writable"
            if not writable:
                directory_status = "cleanup_failed" if probe_report["status"] == "cleanup_failed" else "not_writable"
    checks["working_directory_writable_or_planned"] = writable
    capability = audit_capability(config.executable, environment={})
    detected = all(checks[k] for k in (
        "executable_absolute_local", "executable_filename", "executable_exists"))
    return {"environment_ready": all(checks.values()), "checks": checks,
            "errors": [key for key, valid in checks.items() if not valid],
            "working_directory_status": directory_status,
            "writable_probe": probe_report,
            "capability_audit": capability,
            "fluent_installation": {
                "status": "detected" if detected else "not_detected",
                "version_verified": False, "license_verified": False,
                "declared_product": config.declared_product,
                "release": ("user_declared:" + config.declared_release
                            if config.declared_release else "unknown"),
                "installation_hint": config.installation_hint}}


@dataclass(frozen=True)
class FluentLaunchPlan:
    local_config: LocalFluentConfig

    @property
    def execution_allowed(self):
        return False

    def to_dict(self):
        return {**asdict(self.local_config), "execution_allowed": False,
                "command_preview": None, "mode": "structured_preview_only",
                "reason": "M8A has no execution adapter; verified release-specific CLI syntax unavailable"}
