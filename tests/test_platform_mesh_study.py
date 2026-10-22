"""Controlled grid comparisons, evidence rejection and serial reservation."""
import copy
import threading
import time

import pytest

from nozzle_platform.mesh_study import normalize, compare
from nozzle_platform.workflows import WorkflowStore
from tests.test_platform_domains import stores, config


def request(**changes):
    value=dict(domain_id='domain',scheme_id='scheme',
        mesh=dict(min_size_mm=.04,max_size_mm=4,inlet_size_mm=.08,refine_distance_mm=2,passage_size_mm=.5,curvature_samples=20,algorithm='delaunay'),
        flow=dict(fuel_velocity_m_s=1.,oxidizer_velocity_m_s=1.,temperature_K=300.,oxidizer_o2_mass_fraction=.233,iterations=500),
        refinement_ratio=1.4,tolerance_percent=2.)
    value.update(changes)
    return value


def rows():
    # Volume fixed, cell counts double at each refinement: effective r=2**(1/3).
    return [dict(level=name,status='completed',numerical_criteria_met=True,saved_data_checked=True,
        conditions={'T':300},implementation_sha256={'code':'same'},domain_artifact_sha256={'brep':'same'},
        solver_cells=count,solver_volume_m3=1.,cell_type='polyhedral',
        pressure_drop_Pa={'fuel_inlet':p,'oxidizer_inlet':2*p},pressure_drift_Pa={'fuel_inlet':1e-5,'oxidizer_inlet':2e-5})
        for name,count,p in [('coarse',100,10.1),('medium',200,10.02),('fine',400,10.)]]


def test_plan_scales_chamber_but_keeps_passages_ports_regions_and_curvature_fixed():
    original=request();before=copy.deepcopy(original)
    result=normalize(original)
    assert original==before
    assert [p['level'] for p in result['plan']]==['coarse','medium','fine']
    for p in result['plan']:
        for key in ['max_size_mm']:
            assert p['mesh'][key]==pytest.approx(original['mesh'][key]*p['factor'])
        for key in ['min_size_mm','inlet_size_mm','passage_size_mm']:
            assert p['mesh'][key]==original['mesh'][key]
        assert p['mesh']['refine_distance_mm']==2 and p['mesh']['curvature_samples']==20


@pytest.mark.parametrize('changes',[{'refinement_ratio':1.1},{'refinement_ratio':True},{'refinement_ratio':2.1},
    {'tolerance_percent':float('nan')},{'tolerance_percent':0},{'tolerance_percent':10},{'command':'shell'},
    {'mesh':{'path':'evil'}},{'flow':{'iterations':50}},{'flow':dict(request()['flow'],iterations=50)},
    {'mesh':dict(request()['mesh'],domain_id='different')},{'mesh':dict(request()['mesh'],max_size_mm=20)}])
def test_invalid_plans_are_rejected_before_launch(changes):
    with pytest.raises(ValueError):normalize(request(**changes))


def test_pressure_sensitivity_pass_does_not_claim_global_independence():
    result=compare(rows(),2.)
    assert result['pressure_sensitivity_passed'] and result['recommended_level']=='medium'
    assert result['effective_refinement_ratios']==pytest.approx([2**(1/3)]*2)
    assert not result['grid_independence_verified'] and not result['scientific_eligible']
    assert result['gci'] is None


@pytest.mark.parametrize('change',['not_converged','failed','same_counts','decreasing_counts','conditions','domain','code','cell_type','drift','large_change','nan','zero','missing'])
def test_insufficient_or_inconsistent_evidence_cannot_pass(change):
    data=rows()
    if change=='not_converged':data[1]['numerical_criteria_met']=False
    elif change=='failed':data[1]['status']='flow_failed'
    elif change=='same_counts':data[1]['solver_cells']=data[0]['solver_cells']
    elif change=='decreasing_counts':data[1]['solver_cells']=50
    elif change=='conditions':data[1]['conditions']['T']=310
    elif change=='domain':data[1]['domain_artifact_sha256']['brep']='other'
    elif change=='code':data[1]['implementation_sha256']['code']='other'
    elif change=='cell_type':data[1]['cell_type']='tetrahedral'
    elif change=='drift':data[1]['pressure_drift_Pa']['fuel_inlet']=1.
    elif change=='large_change':data[1]['pressure_drop_Pa']['fuel_inlet']=20.
    elif change=='nan':data[1]['pressure_drop_Pa']['fuel_inlet']=float('nan')
    elif change=='zero':data[1]['pressure_drop_Pa']['fuel_inlet']=0.
    elif change=='missing':del data[1]['solver_cells']
    assert not compare(data,2.)['pressure_sensitivity_passed']


def test_study_reservation_blocks_other_jobs_and_stop_signals_child(stores,tmp_path):
    _,_,scheme,domains=stores
    domain=domains.create(config(scheme))
    store=WorkflowStore(tmp_path/'w',domains)
    store.reservation='study';store.cancel_events['study']=threading.Event()
    store.jobs['study']={'message':'running'}
    store.active='child';store.cancel_events['child']=threading.Event()
    with pytest.raises(ValueError,match='已有'):
        store._start('mesh',{},domain,tmp_path)
    store.stop('study')
    assert store.cancel_events['study'].is_set() and store.cancel_events['child'].is_set()


def test_cancel_before_first_child_preserves_record_and_releases_reservation(stores,tmp_path):
    from nozzle_platform.mesh_study import run
    _,_,scheme,domains=stores
    domain=domains.create(config(scheme));store=WorkflowStore(tmp_path/'w',domains)
    store.reservation='study';store.jobs['study']={}
    cancel=threading.Event();cancel.set()
    folder=tmp_path/'study';folder.mkdir()
    value=request();value.update(domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'])
    run(store,'study',folder,normalize(value),domain,cancel)
    assert store.reservation is None and store.active is None
    assert store.status('study')['status']=='cancelled' and store.status('study')['result'] is None
    assert (folder/'task.json').exists()
