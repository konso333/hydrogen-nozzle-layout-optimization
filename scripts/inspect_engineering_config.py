"""Inspect engineering inputs without generating layouts or launching Fluent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engineering_config import EngineeringGeometryConfig  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return exit code 2 if installation radius or clearances remain unspecified.",
    )
    args = parser.parse_args(argv)
    try:
        report = EngineeringGeometryConfig.load(args.config).inspect()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if args.require_complete and not report["parameters_complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
