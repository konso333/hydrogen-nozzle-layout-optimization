"""A registered generator cannot bypass public geometry validation."""

import math

import numpy as np
import pytest

import layouts
from geometry.constraints import LayoutConstraintError
from validation import InputValidationError


@pytest.fixture
def register(monkeypatch):
    monkeypatch.setattr(layouts, "LAYOUT_REGISTRY", dict(layouts.LAYOUT_REGISTRY))

    def register_points(points):
        layouts.register_layout("m1_extension", lambda **kwargs: points)

    return register_points


def test_public_entry_normalizes_valid_extension_and_preserves_order(register):
    register(np.array([[20, 0], [0, 0]], dtype=int))
    assert layouts.generate_layout("m1_extension", 2, 55, 4, 8) == [(20.0, 0.0), (0.0, 0.0)]


@pytest.mark.parametrize("points,reason", [
    ([(54, 0), (0, 0)], "boundary"),
    ([(0, 0), (0, 0)], "overlap"),
    ([(0, 0), (6, 0)], "minimum centre distance"),
    ([(0, 0)], "expected 2"),
    ([], "expected 2"),
])
def test_public_entry_rejects_geometrically_invalid_extension(register, points, reason):
    register(points)
    with pytest.raises(LayoutConstraintError, match=reason):
        layouts.generate_layout("m1_extension", 2, 55, 4, 8)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_public_entry_rejects_nonfinite_extension_coordinates(register, bad):
    register([(0, 0), (bad, 20)])
    with pytest.raises(ValueError, match="finite") as error:
        layouts.generate_layout("m1_extension", 2, 55, 4, 8)
    assert not isinstance(error.value, LayoutConstraintError)


@pytest.mark.parametrize("points", [[0, 20], [(0, 0, 0), (20, 0, 0)], [[0], [20, 0]],
                                    [[], []], np.empty((0, 3)), None, [["bad", 0]]])
def test_public_entry_rejects_malformed_extension_output(register, points):
    register(points)
    with pytest.raises(ValueError, match="shape"):
        layouts.generate_layout("m1_extension", 2, 55, 4, 8)


def test_public_entry_uses_requested_tolerance(register):
    register([(53 + 5e-7, 0), (0, 0)])
    with pytest.raises(LayoutConstraintError):
        layouts.generate_layout("m1_extension", 2, 55, 4, 8)
    assert len(layouts.generate_layout("m1_extension", 2, 55, 4, 8, tolerance=1e-6)) == 2


@pytest.mark.parametrize("field", ["R", "d", "s_min", "tolerance", "N"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_public_entry_validates_inputs_before_calling_extension(monkeypatch, field, bad):
    def should_not_run(**kwargs):
        pytest.fail("invalid public inputs reached the extension")

    monkeypatch.setattr(layouts, "LAYOUT_REGISTRY", {"m1_extension": should_not_run})
    parameters = dict(N=2, R=55, d=4, s_min=8, tolerance=1e-9)
    parameters[field] = bad
    with pytest.raises(InputValidationError, match=field):
        layouts.generate_layout("m1_extension", **parameters)
