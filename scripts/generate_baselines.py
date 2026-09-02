"""Generate the four validated N=24 baseline layouts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    COORDINATES_DIR,
    FIGURES_DIR,
    SUMMARIES_DIR,
    GeometryConfig,
    ensure_output_directories,
)
from geometry.metrics import evaluate_geometry  # noqa: E402
from io_utils import export_coordinates, export_summary, plot_layout  # noqa: E402
from layouts import BASELINE_LAYOUT_PARAMETERS, generate_layout  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--R", type=float, default=55.0, help="Burner radius in mm")
    parser.add_argument("--d", type=float, default=4.0, help="Nozzle diameter in mm")
    parser.add_argument(
        "--s-min",
        type=float,
        default=8.0,
        help="Minimum centre distance in mm",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = GeometryConfig(R=args.R, d=args.d, s_min=args.s_min)
    ensure_output_directories()

    rows = []
    legacy_rows = []
    for name, parameters in BASELINE_LAYOUT_PARAMETERS.items():
        points = generate_layout(
            name,
            24,
            config.R,
            config.d,
            config.s_min,
            tolerance=config.tolerance,
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
                "layout_type": name,
                "N": 24,
                "layout_parameters": parameters,
                **metrics,
            }
        )
        legacy_rows.append(
            {
                "layout": name,
                "nozzle_count": metrics["nozzle_count"],
                "count_ok": metrics["count_ok"],
                "min_center_distance_mm": metrics["min_center_distance"],
                "mean_nearest_neighbour_mm": metrics[
                    "mean_nearest_neighbor_distance"
                ],
                "max_center_radius_mm": metrics["max_center_radius"],
                "boundary_ok": metrics["boundary_ok"],
                "min_spacing_ok": metrics["spacing_ok"],
                "x_axis_symmetric": metrics["x_axis_symmetry"],
                "y_axis_symmetric": metrics["y_axis_symmetry"],
                "origin_symmetric": metrics["origin_symmetry"],
                "overall_ok": metrics["feasible"],
            }
        )
        export_coordinates(
            points,
            COORDINATES_DIR / f"{name}_coordinates.csv",
            legacy_xy_only=True,
        )
        plot_layout(
            points,
            R=config.R,
            d=config.d,
            title=name,
            metrics=metrics,
            png_path=FIGURES_DIR / f"{name}.png",
        )
        print(
            f"{name}: N={len(points)}, "
            f"min_distance={metrics['min_center_distance']:.3f} mm, "
            f"feasible={metrics['feasible']}"
        )

    summary_path = export_summary(
        rows,
        SUMMARIES_DIR / "baseline_layout_summary.csv",
    )
    legacy_summary_path = export_summary(
        legacy_rows,
        SUMMARIES_DIR / "layout_summary.csv",
    )
    print(f"Baseline summary: {summary_path}")
    print(f"Legacy-compatible summary: {legacy_summary_path}")


if __name__ == "__main__":
    main()
