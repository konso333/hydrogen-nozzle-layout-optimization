"""Search feasible layouts over an N range and export the Pareto frontier."""

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
from io_utils import (  # noqa: E402
    export_coordinates,
    export_summary,
    plot_layout,
    plot_pareto_tradeoffs,
)
from optimization.search_n import search_variable_n  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N-min", type=int, default=12)
    parser.add_argument("--N-max", type=int, default=40)
    parser.add_argument("--R", type=float, default=55.0)
    parser.add_argument("--d", type=float, default=4.0)
    parser.add_argument("--s-min", type=float, default=8.0)
    parser.add_argument(
        "--max-pareto-plots",
        type=int,
        default=12,
        help="Maximum number of representative Pareto layout PNG files",
    )
    return parser.parse_args()


def _representative_rows(rows, limit: int) -> list[dict[str, object]]:
    ordered = sorted(rows, key=lambda row: (int(row["N"]), str(row["candidate_id"])))
    if limit <= 0:
        return []
    if len(ordered) <= limit:
        return ordered
    indices = {
        round(index * (len(ordered) - 1) / (limit - 1))
        for index in range(limit)
    } if limit > 1 else {len(ordered) - 1}
    return [ordered[index] for index in sorted(indices)]


def main() -> None:
    args = parse_args()
    config = GeometryConfig(R=args.R, d=args.d, s_min=args.s_min)
    ensure_output_directories()
    variable_coordinate_dir = COORDINATES_DIR / "variable_n"
    variable_figure_dir = FIGURES_DIR / "variable_n"
    variable_coordinate_dir.mkdir(parents=True, exist_ok=True)
    variable_figure_dir.mkdir(parents=True, exist_ok=True)

    candidates, marked_rows, pareto_rows = search_variable_n(
        args.N_min,
        args.N_max,
        config,
    )
    if not candidates:
        raise RuntimeError("No feasible geometric candidates were found.")

    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for candidate in candidates:
        export_coordinates(
            candidate.points,
            variable_coordinate_dir / f"{candidate.candidate_id}.csv",
        )

    results_path = export_summary(
        marked_rows,
        SUMMARIES_DIR / "variable_n_results.csv",
    )
    pareto_path = export_summary(
        pareto_rows,
        SUMMARIES_DIR / "pareto_candidates.csv",
    )
    tradeoff_path = plot_pareto_tradeoffs(
        marked_rows,
        variable_figure_dir / "pareto_tradeoffs.png",
    )

    for row in _representative_rows(pareto_rows, args.max_pareto_plots):
        candidate = candidate_by_id[str(row["candidate_id"])]
        plot_layout(
            candidate.points,
            R=config.R,
            d=config.d,
            title=f"Pareto geometry candidate: {candidate.candidate_id}",
            metrics=candidate.metrics,
            png_path=variable_figure_dir / f"{candidate.candidate_id}.png",
            annotate=len(candidate.points) <= 40,
        )

    print(f"Feasible candidates: {len(candidates)}")
    print(f"Geometry-only Pareto candidates: {len(pareto_rows)}")
    print("No single 'best N' is selected because CFD objectives are not available.")
    print(f"Results: {results_path}")
    print(f"Pareto CSV: {pareto_path}")
    print(f"Trade-off figure: {tradeoff_path}")


if __name__ == "__main__":
    main()
