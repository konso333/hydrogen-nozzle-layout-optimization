"""Compare N=24 baselines with representative deterministic extensions."""

from __future__ import annotations

import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    COORDINATES_DIR,
    FIGURES_DIR,
    SUMMARIES_DIR,
    DEFAULT_CONFIG,
    ensure_output_directories,
)
from geometry.metrics import evaluate_geometry  # noqa: E402
from io_utils import export_coordinates, export_summary, plot_layout  # noqa: E402
from layouts import generate_layout  # noqa: E402


COMPARISON_SPECS = [
    ("A_Rectangular", "A_Rectangular", {}),
    ("B_Hexagonal", "B_Hexagonal", {}),
    ("C_Double_Ring", "C_Double_Ring", {}),
    ("D_Triple_Ring", "D_Triple_Ring", {}),
    (
        "E_Sector_4x6",
        "sector",
        {
            "num_sectors": 4,
            "points_per_sector": 6,
            "inner_radius": 18.0,
            "outer_radius": 46.0,
            "sector_angle": math.pi / 6.0,
            "angular_offset": math.pi / 16.0,
            "radial_levels": 3,
        },
    ),
    (
        "F_Radial_8x3",
        "radial_spoke",
        {
            "num_spokes": 8,
            "points_per_spoke": 3,
            "inner_radius": 16.0,
            "outer_radius": 46.0,
            "angular_offset": math.pi / 16.0,
        },
    ),
    (
        "G_Staggered_Ring_8_16",
        "staggered_ring",
        {
            "ring_radii": [25.0, 48.0],
            "points_per_ring": [8, 16],
            "delta_theta": math.pi / 16.0,
        },
    ),
    (
        "H_Nonuniform_3_Ring",
        "nonuniform_ring",
        {
            "ring_radii": [18.0, 34.0, 50.0],
            "points_per_ring": [4, 8, 12],
            "ring_offsets": [0.0, math.pi / 8.0, math.pi / 12.0],
        },
    ),
    (
        "I_Deterministic_Irregular",
        "deterministic_irregular",
        {"inner_radius": 0.0, "outer_radius": 50.0},
    ),
]


def main() -> None:
    config = DEFAULT_CONFIG
    ensure_output_directories()
    rows = []
    for display_name, layout_type, parameters in COMPARISON_SPECS:
        points = generate_layout(
            layout_type,
            24,
            config.R,
            config.d,
            config.s_min,
            tolerance=config.tolerance,
            **parameters,
        )
        metrics = evaluate_geometry(
            points,
            R=config.R,
            d=config.d,
            s_min=config.s_min,
            expected_N=24,
            tolerance=config.tolerance,
        )
        rows.append(
            {
                "layout_name": display_name,
                "layout_type": layout_type,
                "N": 24,
                "layout_parameters": parameters,
                **metrics,
            }
        )
        export_coordinates(
            points,
            COORDINATES_DIR / f"comparison_{display_name}_coordinates.csv",
        )
        plot_layout(
            points,
            R=config.R,
            d=config.d,
            title=display_name,
            metrics=metrics,
            png_path=FIGURES_DIR / f"comparison_{display_name}.png",
        )

    path = export_summary(rows, SUMMARIES_DIR / "layout_comparison.csv")
    print(f"Compared {len(rows)} deterministic N=24 layouts.")
    print(f"Comparison summary: {path}")


if __name__ == "__main__":
    main()
