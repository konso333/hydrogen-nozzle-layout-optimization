"""M1 input checks exercise public and direct generators before construction."""

import math
from decimal import Decimal

import numpy as np
import pytest

from layouts import LAYOUT_REGISTRY, generate_layout
from validation import InputValidationError


FAMILIES = [
    ("rectangular", {"spacing": 10, "rows": 2, "columns": 2}),
    ("hexagonal", {"spacing": 10, "row_counts": [2, 2]}),
    ("ring", {"ring_radii": [20], "points_per_ring": [4]}),
    ("staggered_ring", {"ring_radii": [20], "points_per_ring": [4], "delta_theta": 0.2}),
    ("nonuniform_ring", {"points_per_ring": [4], "inner_radius": 20, "outer_radius": 40}),
    ("radial_spoke", {"num_spokes": 2, "points_per_spoke": 2,
                      "inner_radius": 10, "outer_radius": 20}),
    ("sector", {"num_sectors": 2, "points_per_sector": 2, "radial_levels": 2,
                "inner_radius": 10, "outer_radius": 20, "sector_angle": 0.2}),
    ("deterministic_irregular", {"inner_radius": 10, "outer_radius": 40,
                                "angular_increment": math.pi / 2,
                                "angular_offset": 0, "radial_exponent": 0.5}),
]


@pytest.mark.parametrize("layout_type,parameters", FAMILIES)
def test_public_and_direct_generators_agree_for_valid_inputs(layout_type, parameters):
    direct = LAYOUT_REGISTRY[layout_type](N=4, R=55, d=4, s_min=8, **parameters)
    assert generate_layout(layout_type, 4, 55, 4, 8, **parameters) == direct
    assert len(direct) == 4


@pytest.mark.parametrize("layout_type,parameters", FAMILIES)
@pytest.mark.parametrize("field", ["R", "d", "s_min", "tolerance"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_direct_generators_reject_nonfinite_geometry(layout_type, parameters, field, bad):
    common = dict(N=4, R=55, d=4, s_min=8, tolerance=1e-9)
    common[field] = bad
    with pytest.raises(InputValidationError, match=field):
        LAYOUT_REGISTRY[layout_type](**common, **parameters)


REAL_PARAMETERS = [
    (layout_type, parameters, name)
    for layout_type, parameters in FAMILIES
    for name in parameters
    if name not in {"rows", "columns", "row_counts", "points_per_ring", "num_spokes",
                    "points_per_spoke", "num_sectors", "points_per_sector", "radial_levels"}
] + [("ring", {"ring_radii": [20], "points_per_ring": [4], "ring_offsets": [0],
               "angular_offset": 0}, name) for name in ("ring_offsets", "angular_offset")]


@pytest.mark.parametrize("layout_type,parameters,field", REAL_PARAMETERS)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_generators_reject_nonfinite_layout_parameters(layout_type, parameters, field, bad):
    parameters = dict(parameters)
    parameters[field] = [bad] if field in {"ring_radii", "ring_offsets"} else bad
    with pytest.raises(InputValidationError, match=field):
        LAYOUT_REGISTRY[layout_type](N=4, R=55, d=4, s_min=8, **parameters)


COUNT_SEQUENCES = [
    ("ring", "points_per_ring", {"ring_radii": [20]}),
    ("staggered_ring", "points_per_ring", {"ring_radii": [20], "delta_theta": 0.2}),
    ("nonuniform_ring", "points_per_ring", {"inner_radius": 20, "outer_radius": 40}),
    ("hexagonal", "row_counts", {"spacing": 10}),
]


@pytest.mark.parametrize("layout_type,field,parameters", COUNT_SEQUENCES)
@pytest.mark.parametrize("bad", [4.0, 4.9, True, False, np.bool_(True), -1, 0, "4", None,
                                 math.nan, math.inf, -math.inf])
def test_count_sequences_never_coerce_invalid_elements(layout_type, field, parameters, bad):
    with pytest.raises(InputValidationError, match=field):
        generate_layout(layout_type, 4, 55, 4, 8, **parameters, **{field: [bad]})


@pytest.mark.parametrize("layout_type,field,parameters", COUNT_SEQUENCES)
@pytest.mark.parametrize("count", [4, np.int32(4), np.int64(4), np.uint64(4)])
def test_count_sequences_accept_integral_scalars(layout_type, field, parameters, count):
    expected = generate_layout(layout_type, 4, 55, 4, 8, **parameters, **{field: [4]})
    actual = generate_layout(layout_type, np.int64(4), 55, 4, 8,
                             **parameters, **{field: [count]})
    assert actual == expected


@pytest.mark.parametrize("layout_type,field,parameters", COUNT_SEQUENCES)
@pytest.mark.parametrize("bad", [[], "4", 4, None])
def test_count_sequence_containers_have_explicit_contract(layout_type, field, parameters, bad):
    if field == "row_counts" and bad is None:
        assert len(generate_layout(layout_type, 4, 55, 4, 8, **parameters, row_counts=None)) == 4
    else:
        with pytest.raises(InputValidationError, match=field):
            generate_layout(layout_type, 4, 55, 4, 8, **parameters, **{field: bad})


SCALAR_COUNTS = [
    (layout_type, parameters, field)
    for layout_type, parameters in FAMILIES
    for field in parameters
    if field in {"rows", "columns", "num_spokes", "points_per_spoke",
                 "num_sectors", "points_per_sector", "radial_levels"}
]


@pytest.mark.parametrize("layout_type,parameters,field", SCALAR_COUNTS)
@pytest.mark.parametrize("bad", [2.0, 2.9, True, False])
def test_all_layout_count_scalars_require_integers(layout_type, parameters, field, bad):
    parameters = {**parameters, field: bad}
    with pytest.raises(InputValidationError, match=field):
        generate_layout(layout_type, 4, 55, 4, 8, **parameters)


@pytest.mark.parametrize("layout_type,parameters", FAMILIES)
@pytest.mark.parametrize("bad", [4.0, 4.9, True, False, 0, -1, "4", None])
def test_direct_generators_reject_invalid_n(layout_type, parameters, bad):
    with pytest.raises(InputValidationError, match="N"):
        LAYOUT_REGISTRY[layout_type](N=bad, R=55, d=4, s_min=8, **parameters)


@pytest.mark.parametrize("bad", [1, 0, 1.5, "yes", None, math.nan, math.inf,
                                 [], [True], {}, np.array(True)])
def test_include_center_is_a_boolean_flag_not_a_count(bad):
    with pytest.raises(InputValidationError, match="include_center"):
        generate_layout("ring", 4, 55, 4, 8, ring_radii=[20], points_per_ring=[4],
                        include_center=bad)


RING_CONTINUOUS_PARAMETERS = [
    ("ring", {"ring_radii": [20, 40]}, "ring_radii"),
    ("ring", {"ring_radii": [20, 40], "angular_offset": 0.25}, "angular_offset"),
    ("ring", {"ring_radii": [20, 40], "ring_offsets": [0.1, 0.2]}, "ring_offsets"),
    ("staggered_ring", {"ring_radii": [20, 40], "delta_theta": 0.3}, "delta_theta"),
    ("nonuniform_ring", {"inner_radius": 20, "outer_radius": 40}, "inner_radius"),
    ("nonuniform_ring", {"inner_radius": 20, "outer_radius": 40}, "outer_radius"),
    ("nonuniform_ring", {"inner_radius": 20, "outer_radius": 40,
                         "radial_exponent": 2.0}, "radial_exponent"),
]


@pytest.mark.parametrize("layout_type,parameters,field", RING_CONTINUOUS_PARAMETERS)
@pytest.mark.parametrize("representation", [str, Decimal], ids=["string", "decimal"])
def test_ring_continuous_parameters_preserve_float_conversion(layout_type, parameters, field,
                                                             representation):
    numeric = parameters[field]
    value = ([representation(str(item)) for item in numeric] if isinstance(numeric, list)
             else representation(str(numeric)))
    expected = generate_layout(layout_type, 8, 55, 4, 8, points_per_ring=[4, 4], **parameters)
    actual = generate_layout(layout_type, 8, 55, 4, 8, points_per_ring=[4, 4],
                             **{**parameters, field: value})
    assert actual == expected


@pytest.mark.parametrize("layout_type,parameters,field", RING_CONTINUOUS_PARAMETERS)
@pytest.mark.parametrize("bad", ["NaN", "+Inf", "-Inf", "invalid"])
def test_ring_float_conversion_still_rejects_undefined_values(layout_type, parameters, field, bad):
    value = [bad, 40] if isinstance(parameters[field], list) else bad
    with pytest.raises(InputValidationError, match=field):
        generate_layout(layout_type, 8, 55, 4, 8, points_per_ring=[4, 4],
                        **{**parameters, field: value})


@pytest.mark.parametrize("layout_type,parameters", [
    ("ring", {"ring_radii": [20]}),
    ("staggered_ring", {"ring_radii": [20], "delta_theta": 0.2}),
    ("nonuniform_ring", {"inner_radius": 20, "outer_radius": 40}),
])
@pytest.mark.parametrize("flag", [True, False, np.bool_(True), np.bool_(False)])
def test_include_center_accepts_python_and_numpy_bool_scalars(layout_type, parameters, flag):
    n = 5 if flag else 4
    expected = generate_layout(layout_type, n, 55, 4, 8, points_per_ring=[4],
                               include_center=bool(flag), **parameters)
    actual = generate_layout(layout_type, n, 55, 4, 8, points_per_ring=[4],
                             include_center=flag, **parameters)
    assert actual == expected
    assert len(actual) == n
    assert ((0.0, 0.0) in actual) == bool(flag)
