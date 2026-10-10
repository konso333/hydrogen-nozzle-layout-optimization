"""Prepare engineering Fluent files, or replay them without running a solver."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_fluent import export_engineering_fluent, verify_engineering_fluent  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True, help="Verified engineering_handoff.json from step 6.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output-dir", type=Path, help="New preparation directory outside source archives.")
    action.add_argument("--verify", type=Path, help="Existing engineering_automation_manifest.json.")
    parser.add_argument("--settings", type=Path, help="Optional explicit mesh/numerical preparation settings JSON.")
    args = parser.parse_args(argv)
    if args.verify and args.settings:
        parser.error("--verify reads saved settings and does not accept --settings.")
    try:
        if args.verify:
            report = verify_engineering_fluent(args.handoff, args.verify)
            print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        settings = {"schema_version": 1, "mesh": {}, "numerical_settings": {}}
        if args.settings:
            settings = json.loads(args.settings.read_text(encoding="utf-8-sig"))
        if (not isinstance(settings, dict) or set(settings) != {"schema_version", "mesh", "numerical_settings"}
                or type(settings["schema_version"]) is not int or settings["schema_version"] != 1):
            raise ValueError("Expected v1 preparation settings with mesh and numerical_settings.")
        if not isinstance(settings["mesh"], dict) or not isinstance(settings["numerical_settings"], dict):
            raise ValueError("mesh and numerical_settings must be explicit objects; use {} for unknown settings.")
        path = export_engineering_fluent(args.handoff, args.output_dir, mesh=settings["mesh"],
                                         numerical_settings=settings["numerical_settings"])
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"engineering_automation_manifest": str(path), "status": "prepared",
                      "ready_to_execute": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
