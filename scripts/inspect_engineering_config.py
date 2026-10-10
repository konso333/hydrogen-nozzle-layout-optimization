"""Inspect engineering inputs without generating layouts or launching Fluent."""

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
from engineering_geometry import validate_engineering_layout  # noqa: E402


def _load_coordinates(path: Path) -> list[tuple[float, float]]:
    """Read existing legacy or M2 planar coordinate exports; units are mm."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames not in (
            ["x_mm", "y_mm"], ["nozzle_id", "x_mm", "y_mm", "z_mm"],
        ):
            raise ValueError("Expected x_mm,y_mm or nozzle_id,x_mm,y_mm,z_mm CSV columns.")
        points = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Coordinate CSV row has missing or extra columns.")
            if "z_mm" in row and float(row["z_mm"]) != 0:
                raise ValueError("Planar layout validation requires z_mm=0.")
            points.append((float(row["x_mm"]), float(row["y_mm"])))
        return points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--coordinates", type=Path, help="Read-only check of an existing planar CSV.")
    parser.add_argument("--N", type=int, help="Expected positive nozzle count; requires --coordinates.")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return exit code 2 if installation radius or clearances remain unspecified.",
    )
    args = parser.parse_args(argv)
    if (args.coordinates is None) != (args.N is None):
        parser.error("--coordinates and --N must be supplied together.")
    try:
        config = EngineeringGeometryConfig.load(args.config)
        report = config.inspect()
        if args.coordinates is not None:
            config.require_complete()
            report = validate_engineering_layout(
                _load_coordinates(args.coordinates), config=config, N=args.N,
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    if args.coordinates is not None and not report["validation"]["feasible"]:
        return 1
    return 2 if args.require_complete and not report["parameters_complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
