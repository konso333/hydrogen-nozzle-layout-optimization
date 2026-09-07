"""M1 contracts for geometry configuration scalars."""

import math

import numpy as np
import pytest

from config import GeometryConfig
from validation import InputValidationError


@pytest.mark.parametrize("field", ["R", "d", "s_min", "tolerance"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_config_rejects_nonfinite_fields(field, value):
    with pytest.raises(InputValidationError, match=field):
        GeometryConfig(**{field: value})


@pytest.mark.parametrize("value", [True, False, "55", None, 1 + 2j])
def test_config_requires_real_numeric_scalars(value):
    with pytest.raises(InputValidationError, match="R"):
        GeometryConfig(R=value)


def test_config_accepts_finite_python_and_numpy_numbers():
    config = GeometryConfig(R=np.float64(55), d=np.int64(4), s_min=0, tolerance=0)
    assert config.allowed_center_radius == 53
    assert config.required_center_distance == 4


@pytest.mark.parametrize("parameters", [
    {"R": 0}, {"d": 0}, {"d": 111}, {"s_min": -1}, {"tolerance": -1},
])
def test_config_preserves_finite_domain_restrictions(parameters):
    with pytest.raises(InputValidationError):
        GeometryConfig(**parameters)
