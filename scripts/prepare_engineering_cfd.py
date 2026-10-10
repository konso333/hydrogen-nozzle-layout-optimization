"""Prepare an engineering-aware M6 CFD package, or verify it without writing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_cfd import prepare_engineering_cfd, verify_engineering_cfd  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path, help="Verified engineering_run.json from step 5.")
    source.add_argument("--verify", type=Path, help="Read-only check of engineering_handoff.json.")
    parser.add_argument("--case-id", help="One explicit geometry case ID from that engineering archive.")
    parser.add_argument("--profile", type=Path, help="Explicit engineering CFD profile JSON.")
    parser.add_argument("--output-root", type=Path, help="M6 output root outside the source archive.")
    args = parser.parse_args(argv)
    if args.verify and (args.case_id or args.profile or args.output_root):
        parser.error("--verify does not accept preparation arguments.")
    if args.archive and not (args.case_id and args.profile and args.output_root):
        parser.error("--archive requires --case-id, --profile and --output-root.")
    try:
        if args.verify:
            result = verify_engineering_cfd(args.verify)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0 if result["overall_match"] else 1
        profile = json.loads(args.profile.read_text(encoding="utf-8-sig"))
        path = prepare_engineering_cfd(args.archive, case_id=args.case_id,
                                       profile=profile, output_root=args.output_root)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"engineering_handoff": str(path.resolve()), "status": "prepared",
                      "ready_to_execute": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
