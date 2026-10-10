"""Search explicit engineering N/parameter groups and export geometry results."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_config import EngineeringGeometryConfig  # noqa: E402
from engineering_search import run_engineering_search  # noqa: E402
from experiments.provenance import collect_provenance  # noqa: E402
from io_utils.plot_layout import plot_pareto_tradeoffs  # noqa: E402


CONFIG_COLUMNS = (
    "nozzle_outer_diameter_mm", "installation_radius_mm",
    "nozzle_edge_gap_mm", "wall_clearance_mm",
)
SUMMARY_COLUMNS = (
    "case_id", "N", "layout_type", *CONFIG_COLUMNS,
    "min_center_distance", "minimum_nozzle_edge_gap_mm", "minimum_wall_clearance_mm",
    "minimum_spacing_margin", "minimum_boundary_margin", "uniformity_score",
    "count_ok", "boundary_ok", "overlap_ok", "spacing_ok", "feasible", "pareto_candidate",
)


def _write_candidates(rows, config: EngineeringGeometryConfig, path: Path) -> None:
    """Keep stable headers for empty results; each CSV carries physical inputs."""
    physical = {key: config.to_dict()[key] for key in CONFIG_COLUMNS}
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({**row, **physical} for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--search", type=Path, required=True,
                        help="Engineering search JSON with explicit N and parameter_sets groups.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New directory only; existing outputs are never overwritten.")
    args = parser.parse_args(argv)
    try:
        config = EngineeringGeometryConfig.load(args.config)
        config.require_complete()
        design = json.loads(args.search.read_text(encoding="utf-8-sig"))
        if args.output_dir.exists():
            raise FileExistsError(f"Output already exists: {args.output_dir}. Choose a new directory.")
        report = run_engineering_search(config=config, design=design)
        report["provenance"] = collect_provenance()
        serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        # Only valid designs reach output creation. The report is written last;
        # it records zero-feasible searches too, but never a failed export.
        args.output_dir.mkdir(parents=True, exist_ok=False)
        _write_candidates(report["rows"], config, args.output_dir / "feasible_candidates.csv")
        _write_candidates([row for row in report["rows"] if row["pareto_candidate"]],
                          config, args.output_dir / "pareto_candidates.csv")
        plot_rows = [row for row in report["rows"]
                     if row["min_center_distance"] is not None and row["uniformity_score"] is not None]
        if plot_rows:
            plot_pareto_tradeoffs(plot_rows, args.output_dir / "pareto_tradeoffs.png")
        (args.output_dir / "search_report.json").write_text(serialized, encoding="utf-8")
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        **{key: report[key] for key in ("planned", "unique", "duplicates", "feasible", "infeasible",
                                        "pareto_count", "largest_feasible_N", "by_N", "search_scope")},
        "output_directory": str(args.output_dir.resolve()),
    }, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["feasible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
