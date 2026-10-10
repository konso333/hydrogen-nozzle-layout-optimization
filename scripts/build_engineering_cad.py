"""Inspect, build or verify independent internal-flow CAD copies; no meshing/Fluent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_cad_build import build_engineering_cad, inspect_engineering_cad_build, verify_engineering_cad_build  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("handoff", "automation", "template-profile", "placement"):
        parser.add_argument("--" + name, type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--inspect", action="store_true", help="Default: source checks only; no CAD initialization.")
    action.add_argument("--build", action="store_true", help="Explicitly build STEP/BREP only, not a mesh.")
    action.add_argument("--verify", type=Path, help="Existing cad_build_manifest.json; no file writes.")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if bool(args.output_dir) != args.build:
        parser.error("--output-dir is required only with --build.")
    inputs = (args.handoff, args.automation, args.template_profile, args.placement)
    try:
        if args.build:
            marker = build_engineering_cad(*inputs, args.output_dir)
            result = {"cad_build_manifest": str(marker), "cad_built": True, "mesh_generated": False, "ready_to_execute": False}
        elif args.verify:
            result = verify_engineering_cad_build(*inputs, args.verify)
        else:
            result = inspect_engineering_cad_build(*inputs)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
