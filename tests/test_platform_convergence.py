"""Numerical acceptance rejects transient low residuals and short histories."""
import copy
import pytest

from coldflow_trial.config import EQUATIONS
from coldflow_trial.solver import parse_residual_history, parse_residuals
from nozzle_platform.convergence import checks, assessment


def point(iteration=100):
    result=dict(iteration=iteration,block_steps=25,residuals={k:1e-8 for k in EQUATIONS},
                block_maximum_residuals={k:1e-7 for k in EQUATIONS},relative_mass_imbalance=.001,
                h2_relative_convective_imbalance=.001,relative_pressure_drop_change=.001)
    result['criteria_met']=all(c['passed'] for c in checks(result))
    return result


def test_two_full_blocks_and_minimum_time_are_both_required():
    assert not assessment([point(25),point(50)])['numerical_criteria_met']
    assert not assessment([point(100)])['numerical_criteria_met']
    assert assessment([point(75),point(100)])['numerical_criteria_met']
    short=point(110);short['block_steps']=10
    assert not assessment([point(100),short])['numerical_criteria_met']


def test_low_last_residual_cannot_hide_oscillations_within_block():
    unstable=point(100);unstable['block_maximum_residuals']['continuity']=.01
    unstable['criteria_met']=all(c['passed'] for c in checks(unstable))
    report=assessment([point(75),unstable])
    assert not report['numerical_criteria_met']
    assert report['unmet_checks']==['residual:continuity']
    assert report['consecutive_passing_blocks']==0


@pytest.mark.parametrize('name,value',[
    ('relative_mass_imbalance',.005),('h2_relative_convective_imbalance',.005),
    ('relative_pressure_drop_change',.01),('relative_pressure_drop_change',None),
    ('relative_mass_imbalance',float('nan')),('h2_relative_convective_imbalance',-.1)])
def test_each_conservation_and_stability_check_is_required(name,value):
    p=point();p[name]=value
    assert name in [c['name'] for c in checks(p) if not c['passed']]


def test_zero_nonreacting_water_residual_is_valid():
    p=point();p['block_maximum_residuals']['h2o']=0
    assert all(c['passed'] for c in checks(p))


def test_all_residual_steps_are_retained_and_incomplete_history_rejected():
    header='iter '+ ' '.join(EQUATIONS)+' time/iter'
    rows=['1 '+ ' '.join(['1e-3']*len(EQUATIONS))+' 0:00:01 1',
          '2 '+ ' '.join(['1e-8']*len(EQUATIONS))+' 0:00:01 0']
    text='\n'.join([header,*rows])
    assert parse_residual_history(text,0,2)[1]['continuity']==.001
    assert parse_residuals(text,0,2)['continuity']==1e-8
    with pytest.raises(ValueError,match='Incomplete'):
        parse_residual_history('\n'.join([header,rows[1]]),0,2)
