"""Internal subprocess entry; the public CLI owns timeout and final reporting."""

import json
from pathlib import Path
import sys

from coldflow_trial.solver import run_session


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    state = run_session(config, directory)
    raise SystemExit(0 if state["status"] in {"numerical_criteria_met", "iteration_budget_reached"} else 1)
