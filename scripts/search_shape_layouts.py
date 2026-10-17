"""Compare mounting layouts for fixed space or explicitly modelled fixed power."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shape_export import export_shape_search, verify_shape_search


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if bool(args.verify) == bool(args.config) or (args.verify and args.output_dir) or (args.config and not args.output_dir):
        parser.error("Use --config JSON --output-dir NEW_DIRECTORY, or --verify REPORT.")
    try:
        if args.verify:
            result = verify_shape_search(args.verify)
            code = 0
        else:
            design = json.loads(args.config.read_text(encoding="utf-8-sig"))
            path = export_shape_search(design, args.output_dir)
            report = json.loads(path.read_text(encoding="utf-8"))
            result = {key: report[key] for key in ("mode", "feasible_count", "infeasible_count",
                      "eligible_count", "objective", "power_status", "comparison")}
            result.update(report=str(path), solver_started=False, scientific_eligible=False)
            code = 0 if report["eligible_count"] else 1
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
