"""M8B single-nozzle shakedown; default inspect has NO SOLVER PROCESS."""

import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fluent_execution import prepare, prepare_example, execute, ExecutionRefused
from fluent_pilot import LocalFluentConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--prepare-example", action="store_true")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--automation-manifest", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/m8b"))
    parser.add_argument("--local-config", type=Path, help="Ignored machine-local LocalFluentConfig JSON")
    parser.add_argument("--launch-contract", type=Path, help="Reviewed local launch and adapter JSON")
    parser.add_argument("--fluent-executable")
    parser.add_argument("--working-directory")
    parser.add_argument("--processes", type=int)
    parser.add_argument("--declared-release")
    parser.add_argument("--input-artifact", type=Path)
    parser.add_argument("--input-mode", choices=["existing_mesh", "existing_case", "geometry_artifact"], default="existing_mesh")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args(argv)
    if args.prepare_example:
        if args.package or args.automation_manifest:
            parser.error("Preparation cannot overwrite existing packages")
        package, automation = prepare_example(args.output_root)
        print(json.dumps({"package": str(package), "automation_manifest": str(automation),
                          "execution_allowed": False, "attempt_created": False}, indent=2))
        return 0
    if not args.package or not args.automation_manifest:
        parser.error("Provide --package and --automation-manifest, or --prepare-example")
    local = json.loads(args.local_config.read_text(encoding="utf-8")) if args.local_config else {}
    if not isinstance(local, dict) or set(local) - {f.name for f in fields(LocalFluentConfig)}:
        parser.error("Unknown local configuration fields")
    for name, value in {"executable": args.fluent_executable, "working_directory": args.working_directory,
                        "processes": args.processes, "declared_release": args.declared_release}.items():
        if value is not None:
            local[name] = value
    config = LocalFluentConfig.from_environment(**local)
    inputs = {"input_artifact": args.input_artifact, "input_mode": args.input_mode,
              "journal": args.journal, "timeout_seconds": args.timeout_seconds,
              "contract": json.loads(args.launch_contract.read_text(encoding="utf-8")) if args.launch_contract else None}
    try:
        if args.execute:
            report = execute(args.package, args.automation_manifest, config, authorize=True, **inputs)
        else:
            report = prepare(args.package, args.automation_manifest, config, **inputs).to_dict()
    except ExecutionRefused as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 3 if args.execute and report["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
