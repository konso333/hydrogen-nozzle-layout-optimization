"""Small, shared contracts for public numerical inputs."""

from __future__ import annotations

import math
from numbers import Integral, Real


class InputValidationError(ValueError):
    """A caller supplied an invalid parameter, rather than infeasible geometry."""


def require_finite(value, name: str) -> float:
    """Accept real numeric scalars, excluding booleans and numeric strings."""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise InputValidationError(f"{name} must be a finite real number.")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise InputValidationError(f"{name} must be a finite real number.") from exc
    if not math.isfinite(result):
        raise InputValidationError(f"{name} must be a finite real number.")
    return result


def require_tolerance(value, name: str = "tolerance") -> None:
    if require_finite(value, name) < 0:
        raise InputValidationError(f"{name} cannot be negative.")


def require_count(value, name: str, *, allow_zero: bool = False) -> int:
    """Accept Integral scalars (including NumPy integers), never float or bool."""

    lower = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, Integral) or value < lower:
        qualifier = "non-negative" if allow_zero else "positive"
        raise InputValidationError(f"{name} must be a {qualifier} integer (not bool or float).")
    return int(value)


def require_counts(values, name: str) -> list[int]:
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise InputValidationError(f"{name} must be a non-empty sequence of positive integers.") from exc
    counts = [require_count(value, f"{name}[{index}]") for index, value in enumerate(iterator)]
    if not counts:
        raise InputValidationError(f"{name} must be a non-empty sequence of positive integers.")
    return counts
