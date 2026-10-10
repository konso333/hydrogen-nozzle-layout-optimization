"""Inspect, inventory, prepare or verify CAD boundary labels; never mesh or run Fluent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_cad_build import inspect_engineering_cad_build  # noqa: E402
from engineering_boundaries import (inventory_engineering_boundaries, prepare_engineering_boundaries,
                                    verify_engineering_boundaries)  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("handoff", "automation", "template-profile", "placement"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cad-build", type=Path)
    parser.add_argument("--boundary-profile", type=Path)
    parser.add_argument("--output-dir", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--inspect", action="store_true", help="Default: source checks without CAD initialization.")
    action.add_argument("--inventory", action="store_true", help="Print a source face inventory with unassigned roles.")
    action.add_argument("--prepare", action="store_true", help="Create boundary mapping and unmeshed BREP loader.")
    action.add_argument("--verify", type=Path, help="Existing boundary_manifest.json; read-only replay.")
    args = parser.parse_args(argv)
    if bool(args.output_dir) != args.prepare:
        parser.error("--output-dir is required only with --prepare.")
    if bool(args.prepare or args.verify) != bool(args.cad_build and args.boundary_profile):
        parser.error("--cad-build and --boundary-profile are required together for --prepare/--verify only.")
    if bool(args.cad_build) != bool(args.boundary_profile):
        parser.error("Supply --cad-build and --boundary-profile together.")
    inputs = (args.handoff, args.automation, args.template_profile, args.placement)
    try:
        if args.prepare:
            marker = prepare_engineering_boundaries(*inputs, args.cad_build, args.boundary_profile, args.output_dir)
            result = {"boundary_manifest": str(marker), "boundary_mapping_ready": True,
                      "mesh_generated": False, "ready_to_execute": False}
        elif args.verify:
            result = verify_engineering_boundaries(*inputs, args.cad_build, args.boundary_profile, args.verify)
        elif args.inventory:
            result = inventory_engineering_boundaries(*inputs)
        else:
            result = inspect_engineering_cad_build(*inputs) | {"boundary_mapping_ready": False}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
