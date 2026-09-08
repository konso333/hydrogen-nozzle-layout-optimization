"""Synthetic/test-only M7 demo, or prepare an explicitly supplied M6 package."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfd import CFDSpec, export_cfd_package
from config import GeometryConfig
from experiments.archive import CaseRequest, archive_run
from experiments.spec import CaseSpec
from fluent import audit_capability, export_automation, verify_automation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, help="M6 cfd_case.json")
    parser.add_argument("--output-root", type=Path, default=Path("outputs/fluent"))
    parser.add_argument("--verify", type=Path, help="Existing automation_manifest.json; requires --package")
    parser.add_argument("--fluent-executable", type=Path, help="Read-only file presence check")
    args = parser.parse_args()
    if args.verify and not args.package:
        parser.error("--verify requires --package")
    package = args.package
    if package is None:
        geometry = CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10})
        run = archive_run([CaseRequest(geometry)], name="M7 synthetic/test-only",
                          output_root=args.output_root / "runs")
        spec = CFDSpec.create(geometry.case_id,
            {"equivalence_ratio": {"value": .4, "unit": "1"}},
            {"solver": "Fluent", "dimensionality": "3D"})
        package = export_cfd_package(run, spec, output_root=args.output_root / "cfd",
                                     required_metrics=["pressure_loss"])
    manifest = args.verify or export_automation(package, args.output_root / "prepared" / package.parent.name)
    report = verify_automation(package, manifest)
    print(json.dumps({"data_kind": "synthetic/test-only" if args.package is None else "user-supplied M6 package",
                      "package": str(package), "automation_manifest": str(manifest),
                      "capability": audit_capability(args.fluent_executable), "report": report}, indent=2))


if __name__ == "__main__":
    main()
