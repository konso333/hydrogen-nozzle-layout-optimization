"""Export or verify a STEP template placement plan without starting CAD or Fluent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_cad import export_engineering_cad, verify_engineering_cad  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--automation", type=Path, required=True, help="Verified step 7 engineering_automation_manifest.json.")
    parser.add_argument("--template-profile", type=Path, required=True, help="Explicit internal-flow STEP/profile inputs.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output-dir", type=Path)
    action.add_argument("--verify", type=Path, help="Existing cad_placement_manifest.json; read-only.")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            report = verify_engineering_cad(args.handoff, args.automation, args.template_profile, args.verify)
        else:
            path = export_engineering_cad(args.handoff, args.automation, args.template_profile, args.output_dir)
            report = {"cad_placement_manifest": str(path), "status": "prepared", "ready_to_execute": False}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
