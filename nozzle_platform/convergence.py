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
VERIFICATION_CRITERIA = {
    'scaled_residuals': {name: 1e-8 if name == 'energy' else 1e-5 for name in EQUATIONS},
    'relative_mass_imbalance': .001,
    'h2_relative_convective_imbalance': .001,
    'relative_pressure_drop_change': .00005,
    'outlet_profile_relative_drift': .0001,
    'interior_profile_relative_drift': .0001,
}


def checks(point, criteria=None):
    """Check every step in the block, rather than only a favorable last step."""
    criteria = CRITERIA if criteria is None else criteria
    residuals = point['block_maximum_residuals']
    result = []
    values = [('residual:' + name, residuals[name], limit, True)
              for name, limit in criteria['scaled_residuals'].items()]
    values += [(name, point[name], criteria[name], False) for name in
               ('relative_mass_imbalance', 'h2_relative_convective_imbalance', 'relative_pressure_drop_change')]
    if 'outlet_profile_relative_drift' in criteria:
        values.append(('outlet_profile_relative_drift',point.get('outlet_profile_relative_drift'),criteria['outlet_profile_relative_drift'],False))
    if 'interior_profile_relative_drift' in criteria:
        values.append(('interior_profile_relative_drift',point.get('interior_profile_relative_drift'),criteria['interior_profile_relative_drift'],False))
    for name, value, limit, inclusive in values:
        passed = (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
                  and (value <= limit if inclusive else value < limit))
        result.append(dict(name=name, value=value, limit=limit, inclusive=inclusive, passed=passed))
    return result


def assessment(history, criteria=None, minimum_iterations=MINIMUM_ITERATIONS, consecutive_blocks=CONSECUTIVE_BLOCKS):
    consecutive = 0
    # A short final block cannot replace a full 25-step stability interval.
    for point in reversed(history):
        if point.get('block_steps', 0) != BLOCK_SIZE or not point['criteria_met']:
            break
        consecutive += 1
    completed = history[-1]['iteration'] if history else 0
    latest = checks(history[-1],criteria) if history else []
    sustained = completed >= minimum_iterations and consecutive >= consecutive_blocks
    return dict(numerical_criteria_met=sustained, minimum_iterations=minimum_iterations,
                consecutive_passing_blocks=consecutive, required_consecutive_blocks=consecutive_blocks,
                block_size=BLOCK_SIZE, checks=latest,
                unmet_checks=[item['name'] for item in latest if not item['passed']],
                minimum_iterations_met=completed >= minimum_iterations,
                sustained_checks_met=consecutive >= consecutive_blocks)
