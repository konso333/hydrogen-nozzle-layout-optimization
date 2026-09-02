"""Unified entry point and registry for all layout generators."""

from __future__ import annotations

from collections.abc import Callable

from layouts.hexagonal import (
    HEXAGONAL_BASELINE_PARAMETERS,
    hexagonal_baseline,
    hexagonal_layout,
)
from layouts.irregular import deterministic_irregular_layout
from layouts.radial import radial_spoke_layout
from layouts.rectangular import (
    RECTANGULAR_BASELINE_PARAMETERS,
    rectangular_baseline,
    rectangular_layout,
)
from layouts.ring import (
    DOUBLE_RING_BASELINE_PARAMETERS,
    TRIPLE_RING_BASELINE_PARAMETERS,
    double_ring_baseline,
    nonuniform_ring_layout,
    ring_layout,
    staggered_ring_layout,
    triple_ring_baseline,
)
from layouts.sector import sector_layout


LayoutGenerator = Callable[..., list[tuple[float, float]]]


LAYOUT_REGISTRY: dict[str, LayoutGenerator] = {
    "rectangular": rectangular_layout,
    "hexagonal": hexagonal_layout,
    "ring": ring_layout,
    "sector": sector_layout,
    "radial_spoke": radial_spoke_layout,
    "staggered_ring": staggered_ring_layout,
    "nonuniform_ring": nonuniform_ring_layout,
    "deterministic_irregular": deterministic_irregular_layout,
    "A_Rectangular": rectangular_baseline,
    "B_Hexagonal": hexagonal_baseline,
    "C_Double_Ring": double_ring_baseline,
    "D_Triple_Ring": triple_ring_baseline,
}


BASELINE_LAYOUT_PARAMETERS: dict[str, dict[str, object]] = {
    "A_Rectangular": dict(RECTANGULAR_BASELINE_PARAMETERS),
    "B_Hexagonal": {
        "row_counts": list(HEXAGONAL_BASELINE_PARAMETERS["row_counts"]),
        "spacing": HEXAGONAL_BASELINE_PARAMETERS["spacing"],
    },
    "C_Double_Ring": dict(DOUBLE_RING_BASELINE_PARAMETERS),
    "D_Triple_Ring": dict(TRIPLE_RING_BASELINE_PARAMETERS),
}


def available_layout_types() -> tuple[str, ...]:
    return tuple(LAYOUT_REGISTRY)


def register_layout(name: str, generator: LayoutGenerator) -> None:
    """Register a future generator without changing the search code."""

    if not name or name in LAYOUT_REGISTRY:
        raise ValueError(f"Layout name is empty or already registered: {name!r}")
    if not callable(generator):
        raise TypeError("generator must be callable.")
    LAYOUT_REGISTRY[name] = generator


def generate_layout(
    layout_type: str,
    N: int,
    R: float,
    d: float,
    s_min: float,
    **layout_parameters,
) -> list[tuple[float, float]]:
    """Generate and validate a layout through one consistent public API."""

    try:
        generator = LAYOUT_REGISTRY[layout_type]
    except KeyError as exc:
        choices = ", ".join(available_layout_types())
        raise ValueError(f"Unknown layout_type {layout_type!r}. Available: {choices}") from exc

    return generator(N=N, R=R, d=d, s_min=s_min, **layout_parameters)


__all__ = [
    "BASELINE_LAYOUT_PARAMETERS",
    "available_layout_types",
    "generate_layout",
    "register_layout",
    "rectangular_layout",
    "hexagonal_layout",
    "ring_layout",
    "sector_layout",
    "radial_spoke_layout",
    "staggered_ring_layout",
    "nonuniform_ring_layout",
    "deterministic_irregular_layout",
]
