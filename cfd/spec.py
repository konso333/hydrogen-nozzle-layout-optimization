"""M6 physical specifications; execution details never enter these identities."""

from dataclasses import dataclass
import hashlib
import json
import re

from experiments.spec import canonical_json
from json_values import json_value


INPUT_UNITS = {
    "inlet_temperature": "K", "inlet_pressure": "Pa",
    "mass_flow_rate": "kg/s", "equivalence_ratio": "1",
}
CONFIG_FIELDS = {"solver", "model_name", "turbulence_model", "combustion_model",
                 "dimensionality", "steady_or_transient"}
# Keep the original numerical-provenance validation unchanged while tightening
# physical extras. In particular, nested solver_version must not become a second
# version declaration merely because numerical settings are outside identity.
_LEGACY_RESERVED = {"created_at", "solver_version", "installation_path", "path", "git",
                    "branch", "output_directory", "status", "results", "numerical_settings",
                    "attempt_id", "citation", "doi", "run_id"}
# Case-insensitive exact keys, checked at every dict depth (including lists).
# This is a declared metadata boundary, not a classifier of scientific meaning.
RESERVED = _LEGACY_RESERVED | {
    "output_path", "output_dir", "output_directory", "working_directory", "workdir",
    "absolute_path", "installation_path", "path",
    "created_at", "updated_at", "started_at", "finished_at", "timestamp",
    "solver_execution_time", "execution_time", "wall_clock_time", "elapsed_time", "runtime",
    "git", "git_commit", "git_branch", "git_dirty", "branch",
    "platform", "hostname", "machine", "machine_label", "computer_name",
    "attempt_id", "run_id", "status", "result", "results", "results_file",
    "solver_version", "python_version", "extraction_tool_version", "numerical_settings",
    "execution_environment", "provenance", "citation", "doi",
}


def text_value(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")
    # Portable contracts must not embed local paths, including within prose.
    if re.search(r"[A-Za-z]:[\\/]|\\\\|(?<![\w:])/(?!/)", value):
        raise ValueError(f"{label} must not contain an absolute local path.")
    return value


def number(value, label):
    value = json_value(value, canonical_numbers=True)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number, not bool or text.")
    return value


def structured(value, *, physical=True):
    """Validate extensions after shared JSON conversion, requiring numeric units.

    Quantity leaves are {value: number, unit: nonempty string}; dimensionless
    quantities use '1'. Containers and boolean/text settings remain supported.
    Physical extras reject reserved execution keys recursively. Provenance
    numerical settings retain their pre-cleanup checks with physical=False.
    This validates the schema, not a second JSON normalization implementation.
    """
    value = json_value(value, canonical_numbers=True)
    def check(item):
        if isinstance(item, dict):
            if set(item) == {"value", "unit"}:
                number(item["value"], "quantity.value")
                text_value(item["unit"], "quantity.unit")
                return
            for key, child in item.items():
                text_value(key, "extension key")
                if key.lower() in (RESERVED if physical else _LEGACY_RESERVED):
                    raise ValueError(f"{key} belongs outside physical specification.")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif isinstance(item, str):
            text_value(item, "extension text")
        elif item is not None and not isinstance(item, bool):
            raise ValueError("Extension numbers require {value, unit}.")
    check(value)
    return value


def operating_condition(value):
    if not isinstance(value, dict):
        raise ValueError("operating_condition must be an object.")
    allowed = {*INPUT_UNITS, "fuel_mole_fractions", "oxidizer_mole_fractions", "extras"}
    if set(value) - allowed:
        raise ValueError("Unknown operating condition fields.")
    result = {key: None for key in allowed - {"extras"}}
    for key, unit in INPUT_UNITS.items():
        item = value.get(key)
        if item is not None:
            if not isinstance(item, dict) or set(item) != {"value", "unit"} or item["unit"] != unit:
                raise ValueError(f"{key} requires value and fixed unit {unit}.")
            amount = number(item["value"], key)
            if amount <= 0:
                raise ValueError(f"{key} must be positive.")
            result[key] = {"value": amount, "unit": unit}
    for key in ("fuel_mole_fractions", "oxidizer_mole_fractions"):
        item = value.get(key)
        if item is not None:
            if not isinstance(item, dict) or not item:
                raise ValueError(f"{key} requires species mole fractions (unit 1).")
            fractions = {text_value(k, "species"): number(v, key) for k, v in item.items()}
            if any(v < 0 or v > 1 for v in fractions.values()) or abs(sum(fractions.values()) - 1) > 1e-12:
                raise ValueError("Mole fractions must lie in [0, 1] and sum to 1.")
            result[key] = fractions
    extras = value.get("extras", {})
    if not isinstance(extras, dict):
        raise ValueError("extras must be an object.")
    result["extras"] = structured(extras)
    return result


def simulation_config(value):
    if not isinstance(value, dict) or set(value) - (CONFIG_FIELDS | {"extras"}):
        raise ValueError("Unknown simulation configuration fields.")
    result = {key: None for key in CONFIG_FIELDS}
    for key in CONFIG_FIELDS:
        if value.get(key) is not None:
            result[key] = text_value(value[key], key)
    for key, options in {"dimensionality": {"2D", "axisymmetric", "3D"},
                         "steady_or_transient": {"steady", "transient"}}.items():
        if result[key] is not None and result[key] not in options:
            raise ValueError(f"Invalid {key}.")
    extras = value.get("extras", {})
    if not isinstance(extras, dict):
        raise ValueError("extras must be an object.")
    result["extras"] = structured(extras)
    return result


@dataclass(frozen=True)
class CFDSpec:
    _canonical: str

    def __post_init__(self):
        data = json.loads(self._canonical)
        if not isinstance(data, dict) or set(data) != {
            "schema_version", "case_id", "operating_condition", "simulation_config"
        } or type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Incomplete or unsupported CFD specification.")
        if not isinstance(data["case_id"], str) or not re.fullmatch(r"case_v1_[0-9a-f]{64}", data["case_id"]):
            raise ValueError("Expected M2 geometry case_id.")
        condition = operating_condition(data["operating_condition"])
        config = simulation_config(data["simulation_config"])
        if condition != data["operating_condition"] or config != data["simulation_config"]:
            raise ValueError("Saved CFD specification must contain all resolved fields.")
        object.__setattr__(self, "_canonical", canonical_json(data))

    @classmethod
    def create(cls, case_id, condition, config):
        return cls(canonical_json({"schema_version": 1, "case_id": case_id,
                                  "operating_condition": operating_condition(condition),
                                  "simulation_config": simulation_config(config)}))

    @classmethod
    def from_normalized(cls, data):
        return cls(json.dumps(json_value(data), allow_nan=False))

    @property
    def normalized_spec(self):
        return json.loads(self._canonical)

    @property
    def cfd_case_id(self):
        return "cfd_v1_" + hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()

    @property
    def operating_condition_id(self):
        payload = {"schema_version": 1, "operating_condition": self.normalized_spec["operating_condition"]}
        return "condition_v1_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
