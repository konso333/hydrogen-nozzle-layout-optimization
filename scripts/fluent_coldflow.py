"""Inspect by default; --execute explicitly launches a local laminar cold-flow trial."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coldflow_trial.config import inspect, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Ignored local JSON configuration")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true", help="Read-only checks; no solver process")
    mode.add_argument("--execute", action="store_true", help="Authorize one bounded local Fluent trial")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if not args.execute:
            report = inspect(config)
        else:
            from coldflow_trial.execution import execute
            report = execute(config, authorize=True)
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error), "execution_allowed": False}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    if not args.execute:
        return 0
    return {"numerical_criteria_met": 0, "iteration_budget_reached": 3, "interrupted": 130}.get(report["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
