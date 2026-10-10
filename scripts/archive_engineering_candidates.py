"""Archive selected engineering search candidates, or verify an existing batch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_archive import archive_engineering_candidates, verify_engineering_archive  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--report", type=Path, help="Saved engineering search_report.json.")
    source.add_argument("--verify", type=Path, help="Read-only check of engineering_run.json.")
    parser.add_argument("--output-dir", type=Path, help="New output directory; required with --report.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--select", choices=("pareto", "all"))
    selection.add_argument("--case-id", action="append", help="Feasible case ID; repeat to select several.")
    args = parser.parse_args(argv)
    if args.verify and (args.output_dir or args.select or args.case_id):
        parser.error("--verify does not accept output or selection arguments.")
    if args.report and args.output_dir is None:
        parser.error("--report requires --output-dir.")
    try:
        if args.verify:
            result = verify_engineering_archive(args.verify)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0 if result["overall_match"] else 1
        path = archive_engineering_candidates(
            args.report, output_dir=args.output_dir, selection=args.case_id or args.select or "pareto",
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"engineering_manifest": str(path.resolve()), "status": "completed"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
