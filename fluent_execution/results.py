"""Diagnostic-only JSON extraction. No invented Fluent text parsing or M6 values."""

import math

from fluent_execution.plan import read_json
from optimization.cfd_metrics import CFD_FIELDS


def extract_diagnostics(path):
    """Versioned export from the reviewed journal, never scraped from stdout.

    Exporter must supply sample and surface/source provenance. This is a small
    external export contract, not a claim to implement Fluent report commands.
    """
    data = read_json(path)
    if not isinstance(data, dict) or set(data) != {"schema_version", "sample", "metrics"} or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Invalid diagnostic export schema")
    if not isinstance(data["sample"], str) or not data["sample"].strip():
        raise ValueError("Diagnostic sample required")
    units = {"maximum_temperature": "K", "mass_balance_error": "1", "iteration_count": "1",
             "residual_summary": "1"}
    if not isinstance(data["metrics"], dict) or set(data["metrics"]) - set(units):
        raise ValueError("Unsupported diagnostic metric; NOx and formal metrics are excluded")
    for key, item in data["metrics"].items():
        if not isinstance(item, dict) or set(item) != {"value", "unit", "source"} or item["unit"] != units[key]:
            raise ValueError("Invalid diagnostic quantity")
        if not isinstance(item["source"], str) or not item["source"].strip():
            raise ValueError("Diagnostic source required")
        values = item["value"]
        if key == "residual_summary":
            if not isinstance(values, dict) or not values:
                raise ValueError("Per-equation residual values required")
            values = list(values.values())
        else:
            values = [values]
        for value in values:
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Finite nonnegative diagnostics required")
        if key == "iteration_count" and type(item["value"]) is not int:
            raise ValueError("Iteration count requires integer")
    return data


def result_status():
    return {"formal_metrics": {key: {"status": "unresolved_result_mapping", "value": None}
                               for key in CFD_FIELDS},
            "NO": {"status": "unsupported", "value": None},
            "NO2": {"status": "unsupported", "value": None},
            "NOx": {"status": "pending_contract", "value": None},
            "diagnostics": None, "convergence_status": "not_assessed",
            "purpose": "shakedown", "scientific_eligible": False}
