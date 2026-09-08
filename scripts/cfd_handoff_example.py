"""Synthetic/test-only contract demo; creates no numerical CFD result."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfd import CFDSpec, create_attempt, export_cfd_package, load_cfd_package
from config import GeometryConfig
from experiments.archive import CaseRequest, archive_run, verify_run
from experiments.spec import CaseSpec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-manifest", type=Path, help="Existing M2 run.json; requires --case-id")
    parser.add_argument("--case-id", help="Explicit geometry case from that run")
    parser.add_argument("--output-root", type=Path, default=Path("outputs/m6_example"))
    args = parser.parse_args()
    if bool(args.run_manifest) != bool(args.case_id):
        parser.error("Provide --run-manifest and --case-id together.")
    if args.run_manifest:
        run, case_id = args.run_manifest, args.case_id
    else:
        geometry = CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10})
        run = archive_run([CaseRequest(geometry)], name="M6 synthetic/test-only contract example",
                          output_root=args.output_root / "runs")
        case_id = geometry.case_id
    specs, packages = [], []
    for ratio in (.4, .5):
        condition = {"equivalence_ratio": {"value": ratio, "unit": "1"}}
        config = {"solver": "Fluent", "dimensionality": "3D"}
        spec = CFDSpec.create(case_id, condition, config)
        assert spec.cfd_case_id == CFDSpec.create(case_id, condition, config).cfd_case_id
        path = export_cfd_package(run, spec, output_root=args.output_root / "cfd",
                                  required_metrics=["pressure_loss"])
        create_attempt(path, data_kind="synthetic/test-only")
        load_cfd_package(path)
        specs.append(spec)
        packages.append(str(path))
    assert specs[0].cfd_case_id != specs[1].cfd_case_id
    assert verify_run(run)["overall_match"]
    print(json.dumps({"data_kind": "synthetic/test-only", "case_id": case_id,
                      "cfd_case_ids": [s.cfd_case_id for s in specs],
                      "same_condition_same_id": True, "M2_verified": True,
                      "packages": packages, "CFD_executed": False}, indent=2))


if __name__ == "__main__":
    main()
