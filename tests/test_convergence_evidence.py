import pytest
from nozzle_platform.convergence import VERIFICATION_CRITERIA
from nozzle_platform.convergence_evidence import decide


def history(count):
    points=[]
    for i in range(count):
        p={k:0. for k in VERIFICATION_CRITERIA if k!='scaled_residuals'}
        p.update(iteration=2075+i*25,block_steps=25,criteria_met=True,
                 block_maximum_residuals={k:0. for k in VERIFICATION_CRITERIA['scaled_residuals']})
        points.append(p)
    return points


def test_existing_cpu_steps_do_not_fill_the_gpu_minimum():
    r=decide(history(4),full_species_flux_verified=False,budget_exhausted=True)
    assert r['added_gpu_iterations']==100
    assert not r['numerical_assessment']['minimum_iterations_met']
    assert not r['automatic_next_batch_allowed']


def test_missing_flux_cannot_pass_strict_even_after_numerical_convergence():
    r=decide(history(8),full_species_flux_verified=False,budget_exhausted=True)
    assert r['numerical_assessment']['numerical_criteria_met']
    assert not r['strict_convergence_verified'] and not r['scientific_eligible']


def test_numerical_and_flux_checks_do_not_certify_physics_or_grid():
    r=decide(history(8),full_species_flux_verified=True,budget_exhausted=True)
    assert r['strict_convergence_verified']
    assert not r['formal_physics_validated'] and not r['grid_independence_verified']


def test_favorable_saved_flag_does_not_override_bad_measurements():
    h=history(8);h[-1]['outlet_profile_relative_drift']=.15
    r=decide(h,full_species_flux_verified=True,budget_exhausted=True)
    assert not r['strict_convergence_verified']
    assert 'outlet_profile_relative_drift' in r['numerical_assessment']['unmet_checks']


@pytest.mark.parametrize('fault',['gap','duplicate','partial','wrong_type'])
def test_chain_gaps_and_partial_intervals_refused(fault):
    h=history(8)
    if fault=='gap':h[4]['iteration']+=25
    elif fault=='duplicate':h[4]['iteration']=h[3]['iteration']
    elif fault=='partial':h[4]['block_steps']=24
    else:h[4]['iteration']=True
    with pytest.raises(ValueError):decide(h,full_species_flux_verified=False,budget_exhausted=True)


def test_three_consecutive_blocks_required():
    h=history(8);h[-3]['relative_mass_imbalance']=1
    r=decide(h,full_species_flux_verified=False,budget_exhausted=True)
    assert r['numerical_assessment']['consecutive_passing_blocks']==2
    assert not r['numerical_assessment']['numerical_criteria_met']


def test_reader_plan_has_no_initialization_or_iteration_commands():
    from scripts.probe_species_flux_report import reader_journal
    text=reader_journal()
    assert '/solve/' not in text and '/initialize' not in text
    assert text.count('/report/species-mass-flow')==1


def test_license_or_resource_block_is_not_mistaken_for_exhausted_iteration_budget():
    r=decide(history(4),full_species_flux_verified=False,budget_exhausted=False,execution_blocked=True)
    assert r['status']=='execution_blocked' and not r['budget_exhausted']
    assert not r['automatic_next_batch_allowed']


def test_numeric_budget_cannot_be_bypassed_by_a_saved_boolean():
    r=decide(history(8),full_species_flux_verified=False,budget_exhausted=False)
    assert r['budget_exhausted'] and not r['automatic_next_batch_allowed']
    with pytest.raises(ValueError):decide(history(9),full_species_flux_verified=False,budget_exhausted=False)
