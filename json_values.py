"""Neutral JSON conversion with no plotting or experiment initialization."""

from __future__ import annotations

import math
from decimal import Decimal

import numpy as np


def json_value(value, *, canonical_numbers=False, nonfinite="raise"):
    """Convert supported values to JSON types, without a ``default=str`` fallback.

    Native numeric spelling is preserved for legacy CSV consumers. Identity
    mode maps integral floats (including negative zero) to integers. Nonfinite
    inputs are rejected; result reports may explicitly choose ``nonfinite='null'``.
    Decimal uses the float precision used by the existing ring generators.
    """
    if nonfinite not in {"raise", "null"}:
        raise ValueError("nonfinite must be 'raise' or 'null'.")
    if isinstance(value, np.bool_):
        value = bool(value)
    elif isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, (np.floating, Decimal)):
        value = float(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            if nonfinite == "null":
                return None
            raise ValueError("Nonfinite values cannot enter a JSON specification.")
        return int(value) if canonical_numbers and value.is_integer() else value
    options = dict(canonical_numbers=canonical_numbers, nonfinite=nonfinite)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings.")
        return {key: json_value(item, **options) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item, **options) for item in value]
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")
