"""Explicit, sustained numerical checks for the nonreacting engineering trial."""

import math

from coldflow_trial.config import EQUATIONS


CRITERIA = {
    'scaled_residuals': {name: 1e-6 if name == 'energy' else 1e-4 for name in EQUATIONS},
    'relative_mass_imbalance': .005,
    'h2_relative_convective_imbalance': .005,
    'relative_pressure_drop_change': .01,
}
MINIMUM_ITERATIONS = 100
CONSECUTIVE_BLOCKS = 2
BLOCK_SIZE = 25


def checks(point):
    """Check every step in the block, rather than only a favorable last step."""
    residuals = point['block_maximum_residuals']
    result = []
    values = [('residual:' + name, residuals[name], limit, True)
              for name, limit in CRITERIA['scaled_residuals'].items()]
    values += [(name, point[name], CRITERIA[name], False) for name in
               ('relative_mass_imbalance', 'h2_relative_convective_imbalance', 'relative_pressure_drop_change')]
    for name, value, limit, inclusive in values:
        passed = (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
                  and (value <= limit if inclusive else value < limit))
        result.append(dict(name=name, value=value, limit=limit, inclusive=inclusive, passed=passed))
    return result


def assessment(history):
    consecutive = 0
    # A short final block cannot replace a full 25-step stability interval.
    for point in reversed(history):
        if point.get('block_steps', 0) != BLOCK_SIZE or not point['criteria_met']:
            break
        consecutive += 1
    completed = history[-1]['iteration'] if history else 0
    latest = checks(history[-1]) if history else []
    sustained = completed >= MINIMUM_ITERATIONS and consecutive >= CONSECUTIVE_BLOCKS
    return dict(numerical_criteria_met=sustained, minimum_iterations=MINIMUM_ITERATIONS,
                consecutive_passing_blocks=consecutive, required_consecutive_blocks=CONSECUTIVE_BLOCKS,
                block_size=BLOCK_SIZE, checks=latest,
                unmet_checks=[item['name'] for item in latest if not item['passed']],
                minimum_iterations_met=completed >= MINIMUM_ITERATIONS,
                sustained_checks_met=consecutive >= CONSECUTIVE_BLOCKS)
