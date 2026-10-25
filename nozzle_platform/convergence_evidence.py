"""Sustained GPU-chain checks; old CPU iteration numbers cannot supply a budget."""
from nozzle_platform.convergence import VERIFICATION_CRITERIA, checks, assessment


def decide(history, *, full_species_flux_verified, budget_exhausted, execution_blocked=False):
    if any(type(v) is not bool for v in [full_species_flux_verified,budget_exhausted,execution_blocked]):
        raise ValueError('Explicit evidence and budget booleans required')
    local = []; added = 0; previous = None
    for point in history:
        iteration = point.get('iteration')
        if type(iteration) is not int or iteration < 25 or point.get('block_steps') != 25:
            raise ValueError('A complete 25-step saved interval is required')
        if previous is not None and iteration != previous + 25:
            raise ValueError('Non-contiguous GPU iteration history')
        previous = iteration; added += 25
        # Recompute from measurements. Never trust a stored criteria_met flag.
        measured = checks(point, VERIFICATION_CRITERIA)
        local.append(dict(point,iteration=added,criteria_met=all(c['passed'] for c in measured)))
    if added > 200: raise ValueError('M17 plus M18 cannot exceed the authorized 200-step chain')
    budget_exhausted = budget_exhausted or added == 200
    result = assessment(local,VERIFICATION_CRITERIA,200,3)
    numerical = result['numerical_criteria_met']
    if execution_blocked: status = 'execution_blocked'
    elif numerical and full_species_flux_verified: status = 'numerical_and_flux_checks_met_pending_physics'
    elif numerical: status = 'numerical_checks_met_flux_unverified'
    elif budget_exhausted: status = 'stopped_at_iteration_budget'
    else: status = 'numerical_checks_not_met'
    return dict(status=status, added_gpu_iterations=added, numerical_assessment=result,
                strict_convergence_verified=numerical and full_species_flux_verified,
                full_species_flux_verified=full_species_flux_verified,
                execution_blocked=execution_blocked,budget_exhausted=budget_exhausted,
                automatic_next_batch_allowed=not execution_blocked and not budget_exhausted and not (numerical and full_species_flux_verified),
                formal_physics_validated=False, scientific_eligible=False,
                grid_independence_verified=False, cpu_fallback=False)
