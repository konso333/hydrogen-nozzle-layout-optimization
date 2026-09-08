"""Run an explicit M3 JSON design; default action only prints the search plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.archive import verify_run  # noqa: E402
from experiments.batch import run_batch, save_report  # noqa: E402
from experiments.search_space import ExperimentSearchSpace, default_search_space  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Saved search_space.json is an explicit experiment design snapshot. "
            "Changing geometry does not automatically recompute layout-specific parameters: "
            "changing R does not scale ring_radii or rerun default_layout_specs(). "
            "Edit parameter_sets or generate a new design when layouts should change with geometry. "
            "Default Pareto is global across all unique feasible cases, including different "
            "R, d, s_min and tolerance values; blocks, geometries and layouts are not separate "
            "Pareto groups. M2 archive verification checks the M2 archive, not the full M3 "
            "semantics of search_space.json or search_report.json."
        ),
    )
    parser.add_argument("--config", type=Path, help="JSON design; omitted uses the legacy 426 preset")
    parser.add_argument("--save-config", type=Path)
    parser.add_argument("--execute", action="store_true", help="Generate and evaluate the planned cases")
    parser.add_argument("--archive-root", type=Path, help="Archive all feasible cases using M2")
    parser.add_argument("--report", type=Path, help="Save report, including zero-feasible results")
    parser.add_argument("--no-pareto", action="store_true", help="Disable the default global Pareto calculation")
    args = parser.parse_args()
    if (args.archive_root or args.report) and not args.execute:
        parser.error("--archive-root / --report require --execute")
    space = ExperimentSearchSpace.load(args.config) if args.config else default_search_space()
    if args.save_config:
        space.save(args.save_config)
    plan = space.plan()
    print(json.dumps({"planned": plan.planned, "unique": plan.unique, "duplicates": plan.duplicates}))
    if args.execute:
        report = run_batch(space, output_root=args.archive_root, pareto=not args.no_pareto)
        if args.report:
            save_report(report, args.report)
        print(json.dumps({key: report[key] for key in (
            "planned", "unique", "duplicates", "feasible", "infeasible", "pareto_count", "pareto_scope",
            "run_id", "archive_status")}))
        if report["run_id"]:
            path = args.archive_root / report["run_id"] / "run.json"
            verified = verify_run(path)
            print(f"Run manifest: {path}")
            print(f"M2 archive verification (verify_run): {verified['overall_match']}")
            if not verified["overall_match"]:
                raise SystemExit(1)


if __name__ == "__main__":
    main()
