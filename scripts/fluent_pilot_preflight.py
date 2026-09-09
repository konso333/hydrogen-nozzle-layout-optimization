"""M8A H1 preparation example or read-only existing-package preflight; no solve."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfd import CFDSpec, export_cfd_package
from experiments.archive import archive_run
from fluent import export_automation
from fluent_pilot import LocalFluentConfig, preflight, select_pilot_request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--automation-manifest", type=Path)
    parser.add_argument("--prepare-example", action="store_true",
                        help="Explicitly create M2/M6/M7 preparation only, no attempt")
    parser.add_argument("--output-root", type=Path, default=Path("outputs/m8a"))
    parser.add_argument("--fluent-executable")
    parser.add_argument("--working-directory")
    parser.add_argument("--processes", type=int)
    parser.add_argument("--dimension", default="3D")
    parser.add_argument("--precision", default="double")
    parser.add_argument("--plan-create-directory", action="store_true")
    parser.add_argument("--declared-product")
    parser.add_argument("--declared-release")
    parser.add_argument("--installation-hint")
    args = parser.parse_args(argv)
    if args.prepare_example:
        if args.package or args.automation_manifest:
            parser.error("--prepare-example cannot be combined with existing package inputs")
    elif not (args.package and args.automation_manifest):
        parser.error("Provide --package and --automation-manifest, or --prepare-example")
    try:
        config = LocalFluentConfig.from_environment(
            executable=args.fluent_executable, working_directory=args.working_directory,
            processes=args.processes, dimension=args.dimension, precision=args.precision,
            plan_create_directory=args.plan_create_directory,
            declared_product=args.declared_product, declared_release=args.declared_release,
            installation_hint=args.installation_hint)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    package, manifest = args.package, args.automation_manifest
    if args.prepare_example:
        request = select_pilot_request()
        run = archive_run([request], name="M8A H1 hex7 S10 geometry pilot preparation",
                          output_root=args.output_root / "runs")
        # No guessed physical conditions, solver models or numerical settings.
        spec = CFDSpec.create(request.spec.case_id, {}, {})
        package = export_cfd_package(run, spec, output_root=args.output_root / "cfd",
                                     required_metrics=["pressure_loss"])
        manifest = export_automation(package, args.output_root / "prepared" / package.parent.name)
    report = preflight(package, manifest, config)
    print(json.dumps({"package": str(package), "automation_manifest": str(manifest),
                      "report": report}, ensure_ascii=False, indent=2, allow_nan=False))
    return 0  # Successful preflight reporting does not authorize execution.


if __name__ == "__main__":
    raise SystemExit(main())
