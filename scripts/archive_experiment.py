"""Create an isolated M2 example run, or verify a saved case from its parameters."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import GeometryConfig, OUTPUT_ROOT  # noqa: E402
from experiments.archive import CaseRequest, archive_run, verify_case, verify_run  # noqa: E402
from experiments.spec import CaseSpec  # noqa: E402
from optimization.layout_search import LayoutSpec, search_layouts_for_n  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT / "runs")
    parser.add_argument("--name", default="M2 reproducibility example")
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument("--verify", type=Path, help="Check one case's local artifacts and geometry")
    verification.add_argument("--verify-run", type=Path, help="Check a complete run.json archive")
    args = parser.parse_args()
    if args.verify or args.verify_run:
        result = verify_run(args.verify_run) if args.verify_run else verify_case(args.verify)
        print(json.dumps(result, indent=2))
        if not result["overall_match"]:
            raise SystemExit(1)
        return
    config = GeometryConfig()
    candidates = search_layouts_for_n(24, config, [
        LayoutSpec("ring", {"ring_radii": [25, 48], "points_per_ring": [8, 16]})])
    cases = [
        CaseRequest(CaseSpec.create("A_Rectangular", 24, config)),
        CaseRequest.from_candidate(candidates[0], config),
        CaseRequest(CaseSpec.create("sector", 24, config, {
            "num_sectors": 4, "points_per_sector": 6, "inner_radius": 18,
            "outer_radius": 46, "sector_angle": 0.5235987755982988,
            "angular_offset": 0.19634954084936207, "radial_levels": 3,
        })),
    ]
    path = archive_run(cases, name=args.name, output_root=args.output_root,
                       description="Baseline, legacy search candidate and sector; geometry only.")
    run = verify_run(path)
    if not run["overall_match"]:
        raise RuntimeError("Run archive verification failed.")
    print(f"Run manifest: {path}")
    print(f"Archived and independently regenerated {run['case_count']} cases.")


if __name__ == "__main__":
    main()
