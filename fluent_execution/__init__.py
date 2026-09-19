"""M8B opt-in execution. Importing this package never starts Fluent."""

from fluent_execution.baseline import baseline_spec, load_baseline, prepare_example
from fluent_execution.plan import ExecutionPlan, prepare, inspect, dry_run
from fluent_execution.execution import ExecutionRefused, execute

__all__ = ["baseline_spec", "load_baseline", "prepare_example", "ExecutionPlan",
           "prepare", "inspect", "dry_run", "ExecutionRefused", "execute"]
