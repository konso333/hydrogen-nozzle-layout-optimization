"""Published example, unequal refinement, oscillation and field evidence guards."""
import copy
import math

import numpy as np
import pytest

from nozzle_platform.grid_convergence import richardson, assess_profiles
from nozzle_platform.mesh_study import normalize, compare
from nozzle_platform.workflows import normalize_mesh, normalize_flow
from tests.test_platform_mesh_study import request, rows


def uniform_rows():
    data=rows()
    for i,row in enumerate(data):
        row['solver_cells']=1000*2**i
        row['mesh_config']={'min_size_mm':.04,'size_factor':1.25**(2-i)}
        row['conditions']['convergence_mode']='grid-verification'
        h=(1/row['solver_cells'])**(1/3)
        row['pressure_drop_Pa']={'fuel_inlet':10+h,'oxidizer_inlet':20+2*h}
        row['outlet_profile']={'points_m':[[0,0],[.01,0]],'columns':['u','v','w','h2'],
                               'values':[[0,0,1+.001*h,.03],[0,0,.5+.001*h,.031]]}
        row['interior_profile']=copy.deepcopy(row['outlet_profile'])
    return data


def test_nasa_nparc_ramp_example():
    r=richardson([.96178,.96854,.97050],[4.,2.,1.])
    assert r['available']
    assert r['observed_order']==pytest.approx(1.78616959)
    assert r['extrapolated']==pytest.approx(.97130033333)
    assert r['fine_gci_percent']==pytest.approx(.10308260347)


def test_unequal_ratios_manufactured_power_law():
    h=[.3,.2,.1];values=[7+2*x**1.3 for x in h]
    r=richardson(values,h)
    assert r['observed_order']==pytest.approx(1.3)
    assert r['extrapolated']==pytest.approx(7.)


@pytest.mark.parametrize('values',[[1,2,1.9],[1,1,1],[1,1.0000000000001,1],[0,0,0],[1,float('nan'),2]])
def test_gci_rejects_oscillation_and_noise(values):
    assert not richardson(values,[4,2,1])['available']


def test_uniform_plan_scales_whole_field_keeps_reference_regions_and_legacy_mode():
    source=request(refinement_mode='uniform',refinement_ratio=1.25)
    source['mesh']['size_factor']=.64
    before=copy.deepcopy(source)
    plan=normalize(source)
    assert source==before
    assert [p['mesh']['size_factor'] for p in plan['plan']]==pytest.approx([1,.8,.64])
    for p in plan['plan']:
        for key in source['mesh'].keys()-{'size_factor'}:assert p['mesh'][key]==source['mesh'][key]
    assert plan['flow']['convergence_mode']=='grid-verification'
    assert normalize(request())['refinement_mode']=='chamber'


@pytest.mark.parametrize('changes',[{'refinement_mode':{}},{'refinement_mode':'invalid'},
    {'mesh':dict(request()['mesh'],size_factor=2)},
    {'flow':dict(request()['flow'],iterations=100)}])
def test_invalid_uniform_plans_fail_early(changes):
    value=request(refinement_mode='uniform');value.update(changes)
    with pytest.raises(ValueError):normalize(value)


def test_scoped_convergence_never_claims_full_field_or_physical_validation():
    r=compare(uniform_rows(),2.,'uniform')
    assert r['scoped_grid_convergence_passed'] and r['recommended_level']=='fine'
    assert all(g['passed'] for g in r['gci'].values())
    assert r['outlet_fields']['passed']
    assert not r['grid_independence_verified'] and not r['scientific_eligible']


def test_incomplete_uniform_study_keeps_its_actual_scope():
    r=compare([],2.,'uniform')
    assert r['status']=='incomplete'
    assert '整体细化' in r['scope'] and '固定小孔入口' not in r['scope']


@pytest.mark.parametrize('outlet,expected',[(58.5,6.),(4.5,4.5)])
def test_refinement_covers_protruding_tip_without_including_chamber_wall(outlet,expected):
    from nozzle_platform.volume_mesh import passage_refinement_zmax
    domain={'config':{'outlet_z_mm':outlet,'chamber_start_z_mm':-1.5},
            'faces':[{'role':'wall','bounds_mm':[-42,-42,-1.5,42,42,outlet]},
                     {'role':'wall','bounds_mm':[-5,-5,-3,5,5,4]},
                     {'role':'outlet','bounds_mm':[-42,-42,outlet,42,42,outlet]}]}
    assert passage_refinement_zmax(domain)==pytest.approx(expected)


@pytest.mark.parametrize('problem',['oscillation','noise','field','points','family','strict','missing'])
def test_scoped_claim_requires_all_evidence(problem):
    data=uniform_rows()
    if problem=='oscillation':data[1]['pressure_drop_Pa']['fuel_inlet']=9.9
    elif problem=='noise':data[0]['pressure_drift_Pa']['fuel_inlet']=.01
    elif problem=='field':data[1]['outlet_profile']['values'][0][3]=.06
    elif problem=='points':data[1]['outlet_profile']['points_m'][0][0]=.001
    elif problem=='family':data[1]['mesh_config']['min_size_mm']=.03
    elif problem=='strict':data[1]['conditions']['convergence_mode']='trial'
    elif problem=='missing':del data[1]['outlet_profile']
    r=compare(data,2.,'uniform')
    assert not r['scoped_grid_convergence_passed'] and r['recommended_level'] is None


@pytest.mark.parametrize('key,value',[('size_factor',True),('size_factor',.3),('size_factor',float('nan')),
    ('cell_budget',True),('cell_budget',3000001),('cell_budget',1499999)])
def test_resource_and_scale_limits(key,value):
    with pytest.raises(ValueError):normalize_mesh(dict(request()['mesh'],domain_id='d',scheme_id='s',**{key:value}))


def test_strict_flow_has_separate_budget_and_rejects_unknown_mode():
    flow=dict(request()['flow'],domain_id='d',scheme_id='s',mesh_id='m',iterations=1000,convergence_mode='grid-verification')
    assert normalize_flow(flow)['iterations']==1000
    for mode in ['trial',{},'unknown']:
        with pytest.raises(ValueError):normalize_flow(dict(flow,convergence_mode=mode))


def test_saved_outlet_interpolation_reproduces_known_linear_field(tmp_path):
    import h5py
    from nozzle_platform.grid_convergence import outlet_profile
    axis=np.array([-.03,-.01,.01,.03])
    xyz=np.array([(x,y,.1) for x in axis for y in axis])
    polygons=np.array([[4*i+j,4*(i+1)+j,4*(i+1)+j+1,4*i+j+1] for i in range(3) for j in range(3)])
    centres=xyz[polygons].mean(axis=1)
    with h5py.File(tmp_path/'final.cas.h5','w') as f:
        mesh=f.create_group('meshes/1')
        mesh['nodes/coords/1']=xyz
        mesh['faces/nodes/1/nnodes']=np.full(9,4)
        mesh['faces/nodes/1/nodes']=polygons.flatten()+1
        mesh['faces/zoneTopology/name']=np.array([b'outlet'])
        mesh['faces/zoneTopology/minId']=[1];mesh['faces/zoneTopology/maxId']=[9]
    with h5py.File(tmp_path/'final.dat.h5','w') as f:
        fields={'SV_U':1+2*centres[:,0],'SV_V':3*centres[:,1],'SV_W':np.full(9,2.),
                'SV_Y':np.column_stack([.05+.1*centres[:,0],np.full((9,3),.1)])}
        for name,values in fields.items():
            dataset=f.create_dataset('results/1/phase-1/faces/'+name+'/1',data=values)
            dataset.attrs['minId']=np.array([1])
    profile=outlet_profile(tmp_path,{'config':{'chamber_radius_mm':20}})
    xy=np.array(profile['points_m']);actual=np.array(profile['values'])
    assert len(xy)==197
    expected=np.column_stack([1+2*xy[:,0],3*xy[:,1],np.full(len(xy),2.),.05+.1*xy[:,0]])
    np.testing.assert_allclose(actual,expected,atol=1e-14)
    assert profile['h2_area_mean']==pytest.approx(.05)
    with pytest.raises(ValueError,match='采样点'):
        outlet_profile(tmp_path,{'config':{'chamber_radius_mm':200}})


def test_verification_requires_tighter_checks_and_three_full_blocks():
    from nozzle_platform.convergence import VERIFICATION_CRITERIA, checks, assessment
    from tests.test_platform_convergence import point
    history=[]
    for iteration in [150,175,200]:
        p=point(iteration)
        p.update(relative_mass_imbalance=1e-5,h2_relative_convective_imbalance=1e-5,
                 relative_pressure_drop_change=1e-6,outlet_profile_relative_drift=1e-6,
                 interior_profile_relative_drift=1e-6,
                 block_maximum_residuals={k:1e-9 for k in p['residuals']})
        p['criteria_met']=all(c['passed'] for c in checks(p,VERIFICATION_CRITERIA));history.append(p)
    assert assessment(history,VERIFICATION_CRITERIA,200,3)['numerical_criteria_met']
    assert not assessment(history[:2],VERIFICATION_CRITERIA,200,3)['numerical_criteria_met']
    history[-1]['outlet_profile_relative_drift']=None
    history[-1]['criteria_met']=all(c['passed'] for c in checks(history[-1],VERIFICATION_CRITERIA))
    result=assessment(history,VERIFICATION_CRITERIA,200,3)
    assert not result['numerical_criteria_met']
    assert 'outlet_profile_relative_drift' in result['unmet_checks']


def test_volume_moments_recover_polyhedral_cube_centroid(tmp_path):
    import h5py
    from nozzle_platform.field_profiles import cell_centres
    xyz=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]],float)
    polygons=np.array([[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]])
    path=tmp_path/'cube.cas.h5'
    with h5py.File(path,'w') as f:
        mesh=f.create_group('meshes/1')
        mesh['nodes/coords/1']=xyz
        mesh['cells/zoneTopology/maxId']=[1]
        mesh['faces/nodes/1/nnodes']=np.full(6,4)
        mesh['faces/nodes/1/nodes']=polygons.flatten()+1
        mesh['faces/c0/1']=np.ones(6,dtype=int)
        mesh['faces/c1/1']=np.array([],dtype=int)
        mesh['faces/c1/1'].attrs['minId']=[1]
    centres,volume=cell_centres(path)
    np.testing.assert_allclose(centres,[[.5,.5,.5]],atol=1e-14)
    assert volume==pytest.approx(1.)


def test_interior_reconstruction_reproduces_linear_velocity_and_species(tmp_path,monkeypatch):
    import h5py
    from nozzle_platform import field_profiles
    axis=np.linspace(-.001,.001,11)
    centres=np.array([(x,y,z) for x in axis for y in axis for z in np.linspace(.0001,.0019,11)])
    monkeypatch.setattr(field_profiles,'cell_centres',lambda path:(centres,1.))
    domain={'config':{'chamber_radius_mm':1.,'chamber_start_z_mm':0.,'chamber_length_mm':2.},
            'coordinates_mm':[[0,0]],'faces':[{'role':'wall','bounds_mm':[-1,-1,-1,1,1,0]}]}
    sampler=field_profiles.prepare(tmp_path/'case',domain)
    def analytic(xyz):
        return np.column_stack([1+40*xyz[:,0],30*xyz[:,1],2+20*xyz[:,2],.05+.5*xyz[:,0]+.1*xyz[:,2]])
    values=analytic(centres)
    path=tmp_path/'linear.dat.h5'
    with h5py.File(path,'w') as f:
        for i,name in enumerate(['SV_U','SV_V','SV_W','SV_Y']):
            v=values[:,i] if i<3 else np.column_stack([values[:,i],np.zeros((len(values),3))])
            dataset=f.create_dataset('results/1/phase-1/cells/'+name+'/1',data=v)
            dataset.attrs['minId']=[1]
    profile=field_profiles.sample(path,sampler)
    np.testing.assert_allclose(profile['values'],analytic(np.array(profile['points_m'])),atol=1e-13)
