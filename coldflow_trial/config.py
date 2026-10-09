"""Portable configuration and read-only preflight for a prepared case/data pair."""

import hashlib
import json
import math
from pathlib import Path
import re
import shutil


EQUATIONS = ("continuity", "x-velocity", "y-velocity", "z-velocity", "energy", "h2", "o2", "h2o")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label}: expected exactly {sorted(expected)}")


def _number(value, label, *, positive=True, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: finite number required")
    if integer and not isinstance(value, int):
        raise ValueError(f"{label}: integer required")
    if positive and value <= 0:
        raise ValueError(f"{label}: must be positive")


def _path(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: set an absolute local path in .local/coldflow.json")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label}: absolute path required")
    return path.resolve()


def validate(config):
    _keys(config, {"schema_version", "purpose", "input", "fluent", "output_root", "model",
                   "boundaries", "expected_conditions", "iterations", "convergence",
                   "timeout_seconds", "minimum_free_bytes"}, "config")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported schema_version")
    if config["purpose"] != "engineering_coldflow" or config["model"] != "laminar":
        raise ValueError("Only engineering_coldflow / laminar is supported; reactions remain OFF")
    source = config["input"]
    _keys(source, {"case_file", "data_file", "case_sha256", "data_sha256"}, "input")
    for kind in ("case", "data"):
        path = _path(source[f"{kind}_file"], f"input.{kind}_file")
        suffix = ".cas.h5" if kind == "case" else ".dat.h5"
        if not path.name.lower().endswith(suffix) or not path.is_file():
            raise ValueError(f"input.{kind}_file: existing {suffix} file required")
        expected = source[f"{kind}_sha256"]
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"input.{kind}_sha256: SHA-256 required")
        with path.open("rb") as stream:
            if stream.read(8) != b"\x89HDF\r\n\x1a\n":
                raise ValueError(f"input.{kind}_file: HDF5 signature missing")
        if sha256(path) != expected:
            raise ValueError(f"input.{kind}_file: SHA-256 mismatch")
        source[f"{kind}_file"] = str(path)
    fluent = config["fluent"]
    _keys(fluent, {"executable", "product_version", "processes", "license_product",
                   "license_server", "start_timeout_seconds"}, "fluent")
    executable = _path(fluent["executable"], "fluent.executable")
    if not executable.is_file() or executable.name.lower() != "fluent.exe":
        raise ValueError("fluent.executable: existing fluent.exe required")
    if executable.parent.parent.parent.name.lower() != "fluent":
        raise ValueError("Expected <installation>/fluent/ntbin/win64/fluent.exe")
    if fluent["product_version"] != "25.2.0" or fluent["license_product"] != "premium":
        raise ValueError("This adapter supports the verified 25.2.0 / premium launch only")
    for key in ("processes", "start_timeout_seconds"):
        _number(fluent[key], f"fluent.{key}", integer=True)
    if fluent["license_server"] is not None and (not isinstance(fluent["license_server"], str)
                                                or not fluent["license_server"].strip()):
        raise ValueError("fluent.license_server: null or a nonempty local license setting")
    fluent["executable"] = str(executable)
    root = _path(config["output_root"], "output_root")
    if not str(root).isascii():
        raise ValueError("output_root: use an ASCII path for the verified Fluent/HDF5 workflow")
    if root.is_relative_to(PROJECT_ROOT) and not any(root.is_relative_to(PROJECT_ROOT / part)
                                                   for part in (".local", "outputs/coldflow")):
        raise ValueError("Repository output_root must be under .local/ or outputs/coldflow/")
    if root.exists() and not root.is_dir():
        raise ValueError("output_root must be a directory")
    config["output_root"] = str(root)
    _keys(config["boundaries"], {"fuel_inlet", "oxidizer_inlet", "outlet", "wall"}, "boundaries")
    names = list(config["boundaries"].values())
    if any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != 4:
        raise ValueError("boundaries: four distinct, nonempty zone names required")
    conditions = config["expected_conditions"]
    _keys(conditions, {"fuel_velocity_m_s", "oxidizer_velocity_m_s", "fuel_temperature_K",
                      "oxidizer_temperature_K", "oxidizer_o2_mass_fraction", "outlet_gauge_pressure_Pa",
                      "backflow_temperature_K", "operating_pressure_Pa"}, "expected_conditions")
    for key, value in conditions.items():
        _number(value, key, positive=key != "outlet_gauge_pressure_Pa")
    if conditions["oxidizer_o2_mass_fraction"] >= 1:
        raise ValueError("oxidizer_o2_mass_fraction must be between 0 and 1")
    iterations = config["iterations"]
    _keys(iterations, {"maximum", "block_size", "minimum", "consecutive_passing_blocks"}, "iterations")
    for key, value in iterations.items():
        _number(value, f"iterations.{key}", integer=True)
    if iterations["minimum"] > iterations["maximum"] or iterations["block_size"] > iterations["maximum"]:
        raise ValueError("Minimum and block size must not exceed the additional iteration budget")
    criteria = config["convergence"]
    _keys(criteria, {"scaled_residuals", "relative_mass_imbalance", "h2_relative_convective_imbalance",
                     "relative_pressure_drop_change"}, "convergence")
    _keys(criteria["scaled_residuals"], EQUATIONS, "scaled_residuals")
    for key, value in {**criteria["scaled_residuals"], **{k: v for k, v in criteria.items()
                                                       if k != "scaled_residuals"}}.items():
        _number(value, key)
    _number(config["timeout_seconds"], "timeout_seconds")
    _number(config["minimum_free_bytes"], "minimum_free_bytes", integer=True)
    if config["timeout_seconds"] <= fluent["start_timeout_seconds"]:
        raise ValueError("timeout_seconds must exceed the Fluent launch timeout")
    existing = root
    while not existing.exists():
        existing = existing.parent
    if shutil.disk_usage(existing).free < config["minimum_free_bytes"]:
        raise ValueError("Insufficient free disk space for the configured budget")
    return config


def load_config(path):
    def reject_constant(value):
        raise ValueError(f"Invalid JSON number: {value}")
    return validate(json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_constant))


def inspect(config):
    return {"purpose": config["purpose"], "model": config["model"], "chemical_reactions_enabled": False,
            "warm_start": True, "maximum_additional_iterations": config["iterations"]["maximum"],
            "processes": config["fluent"]["processes"], "output_root": config["output_root"],
            "input_hashes_verified": True, "execution_allowed": False, "formal_m8b_ready": False,
            "scientific_eligible": False,
            "note": "Read-only file/config checks. Live case physics and licensing are checked only on --execute."}
