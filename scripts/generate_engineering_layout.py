"""Export one validated engineering layout; never launch Fluent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from engineering_config import EngineeringGeometryConfig  # noqa: E402
from engineering_layout import generate_engineering_layout  # noqa: E402
from experiments.provenance import collect_provenance  # noqa: E402
from experiments.spec import BASELINE_GENERATORS, BUILTIN_TYPES  # noqa: E402
from geometry.constraints import LayoutConstraintError  # noqa: E402
from io_utils.export_csv import export_coordinates  # noqa: E402
from io_utils.plot_engineering_layout import plot_engineering_layout as _plot_layout  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--layout", choices=sorted(BUILTIN_TYPES | set(BASELINE_GENERATORS)), required=True)
    parser.add_argument("--N", type=int, required=True)
    parser.add_argument("--parameters", type=Path, required=True,
                        help="JSON object of layout-specific parameters; lengths in mm, angles in rad.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New directory only; existing directories and files are never overwritten.")
    args = parser.parse_args(argv)
    try:
        config = EngineeringGeometryConfig.load(args.config)
        config.require_complete()
        parameters = json.loads(args.parameters.read_text(encoding="utf-8-sig"))
        if args.output_dir.exists():
            raise FileExistsError(f"Output already exists: {args.output_dir}. Choose a new directory.")
        points, report = generate_engineering_layout(
            config=config, layout_type=args.layout, N=args.N, parameters=parameters,
        )
        report["provenance"] = collect_provenance()
        serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        # Validate and serialize before creating any output. Write the complete
        # report last so a failed CSV/PNG export cannot appear as a completed run.
        args.output_dir.mkdir(parents=True, exist_ok=False)
        export_coordinates(points, args.output_dir / "coordinates.csv")
        _plot_layout(points, config, args.layout, args.output_dir / "layout.png")
        (args.output_dir / "generation.json").write_text(serialized, encoding="utf-8")
    except LayoutConstraintError as exc:
        print(f"Engineering layout infeasible: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps({**report, "output_directory": str(args.output_dir.resolve())},
                     ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
