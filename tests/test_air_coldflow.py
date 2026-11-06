"""Synthetic file/controller checks, not CFD evidence."""
import copy,json
from pathlib import Path
import h5py,numpy as np,pytest
from nozzle_platform import air_model as air
from nozzle_platform.workflows import normalize_flow
from nozzle_platform.pipelines import normalize as normalize_pipeline

def request(**changes):
    r=dict(mesh_id='m',domain_id='d',scheme_id='s',physical_model=air.MODEL,fuel_velocity_m_s=1.,oxidizer_velocity_m_s=1.,temperature_K=300.,iterations=100,compute_device='gpu',convergence_mode=air.MODE,transport_model=air.TRANSPORT,time_budget_seconds=300)
    r.update(changes);return r

def test_air_properties_are_explicit_fixed_and_do_not_change_original_request():
    r=request();before=copy.deepcopy(r);n=normalize_flow(r);p=air.properties(n['temperature_K'])
    assert r==before and p['density_kg_m3']==pytest.approx(101325/(287.05*300))
    assert 1.84e-5<p['viscosity_Pa_s']<1.86e-5
    assert p['energy_equation'] is False and p['species_transport'] is False

@pytest.mark.parametrize('changes',[{'physical_model':'unknown'},{'transport_model':'mass-weighted-mixing-law'},{'convergence_mode':'grid-verification'},{'oxidizer_o2_mass_fraction':.233},{'species_solver_control':'strict-absolute'},{'iterations':501},{'compute_device':'cpu','iterations':26},{'temperature_K':float('nan')},{'time_budget_seconds':1800},{'iterations':True}])
def test_air_rejects_invalid_or_misleading_models(changes):
    with pytest.raises(ValueError):normalize_flow(request(**changes))

def test_cpu_air_is_bounded_and_legacy_hydrogen_request_remains_legacy():
    assert normalize_flow(request(compute_device='cpu',iterations=25))['iterations']==25
    from tests.test_platform_workflows import flow_request
    legacy=normalize_flow(flow_request());assert 'physical_model' not in legacy

def test_air_cannot_be_compared_by_fuel_heat_input():
    from tests.test_pipelines import request as original
    r=original();r['flow']={k:v for k,v in request().items() if k not in ('mesh_id','scheme_id','domain_id')}
    assert normalize_pipeline(r)['flow']['physical_model']==air.MODEL
    r['comparison'].update(basis='same-heat',fuel_heat_input_kW=.08,cases=[dict(N=2,family='rectangular')])
    with pytest.raises(ValueError,match='空气'):normalize_pipeline(r)

def history_point(i,**changes):
    p=dict(iteration=i,block_steps=25,block_maximum_residuals={n:1e-7 for n in air.EQUATIONS},relative_mass_imbalance=1e-7,relative_pressure_drop_change=.001,criteria_met=True)
    p.update(changes);return p

def test_air_convergence_needs_minimum_and_complete_continuous_blocks():
    assert not air.assessment([history_point(25),history_point(50)])['numerical_criteria_met']
    h=[history_point(i) for i in (25,50,75,100)];assert air.assessment(h)['numerical_criteria_met']
    h[-1]['block_steps']=24;assert not air.assessment(h)['numerical_criteria_met']
    assert not any('h2' in x['name'] or 'energy' in x['name'] for x in air.assessment(h)['checks'])

def test_air_residuals_reject_missing_steps_and_unexpected_species():
    s='iter continuity x-velocity y-velocity z-velocity time/iter\n1 1e-5 1e-6 1e-6 1e-6 0:00:01 0\n'
    assert set(air.residuals(s,0,1)[1])==set(air.EQUATIONS)
    with pytest.raises(ValueError):air.residuals(s,0,2)
    with pytest.raises(ValueError):air.residuals(s.replace('z-velocity','h2'),0,1)

def test_air_convergence_checks_whole_blocks_even_if_stored_flag_claims_pass():
    h=[history_point(i) for i in (25,50,75,100)];h[-2]['block_maximum_residuals']['continuity']=1e-3
    assert h[-2]['criteria_met'] is True and not air.assessment(h)['numerical_criteria_met']

def saved(tmp_path):
    p=tmp_path/'air.dat.h5';rho=air.properties(300)['density_kg_m3'];geom=dict(cells=2,ranges={'fuel_inlet':(0,1),'oxidizer_inlet':(1,2),'outlet':(2,3),'wall':(3,4)},areas=np.ones(4))
    with h5py.File(p,'w') as h:
        for name,values in {'SV_U':[1,1],'SV_V':[0,0],'SV_W':[0,0],'SV_P':[10,0]}.items():
            ds=h.create_dataset('results/1/phase-1/cells/'+name+'/1',data=np.array(values,dtype=float));ds.attrs['minId']=[1];ds.attrs['maxId']=[2]
        for name,values in {'SV_U':[1,1,2,0],'SV_V':[0,0,0,0],'SV_W':[0,0,0,0],'SV_P':[10,10,0,0],'SV_FLUX':[-rho,-rho,2*rho,0]}.items():
            ds=h.create_dataset('results/1/phase-1/faces/'+name+'/1',data=np.array(values,dtype=float));ds.attrs['minId']=[1];ds.attrs['maxId']=[4]
    return p,geom

def test_air_uses_saved_mass_flux_without_species_or_temperature(tmp_path):
    p,g=saved(tmp_path);point=air.saved_point(None,p,request(),geometry=g)
    assert point['relative_mass_imbalance']==0 and point['inlet_mass_crosscheck']['fuel_inlet']['passed']
    assert 'species_convective_flow_kg_s' not in point and point['cell_density_stored_and_checked'] is False
    assert point['area_weighted_total_pressure_drop_Pa']['fuel_inlet']==pytest.approx(10-1.5*air.properties(300)['density_kg_m3'])

@pytest.mark.parametrize('fault',['inlet','nonfinite','coverage'])
def test_air_saved_data_corruption_is_rejected(tmp_path,fault):
    p,g=saved(tmp_path)
    with h5py.File(p,'r+') as h:
        if fault=='inlet':h['results/1/phase-1/faces/SV_FLUX/1'][0]*=1.1
        elif fault=='nonfinite':h['results/1/phase-1/cells/SV_P/1'][0]=float('nan')
        else:h['results/1/phase-1/faces/SV_P/1'].attrs['maxId']=[3]
    with pytest.raises(ValueError):air.saved_point(None,p,request(),geometry=g)

def test_air_maps_contain_only_computed_fields(tmp_path):
    from tests.test_field_maps import cubes
    from nozzle_platform.field_maps import extract,render
    case,data=cubes(tmp_path)
    with h5py.File(data,'r+') as h:
        del h['results/1/phase-1/cells/SV_Y'];del h['results/1/phase-1/cells/SV_T']
    folder=tmp_path/'maps';r=extract(case,data,folder,axis='x',position_mm=.5,physical_model=air.MODEL)
    assert r['field_names']==['speed','pressure'] and set(r['bounds'])=={'speed','pressure'}
    render([folder]);m=json.loads((folder/'maps.json').read_text(encoding='utf-8'));assert set(m['images_sha256'])=={'speed.png','pressure.png'}
    assert (folder/'section_values.csv').read_text(encoding='utf-8-sig').splitlines()[0]=='cell_id,speed,pressure'

def configured_air():
    props=air.properties(300);value=lambda x:dict(option='value',value=x)
    inlet=dict(momentum=dict(velocity_specification_method='Magnitude, Normal to Boundary',velocity_magnitude=value(1.)))
    return dict(general=dict(operating_conditions=dict(operating_pressure=101325.,gravity={'enable':False})),models=dict(species={'model':{'option':'off'}},energy={'enabled':False},viscous={'model':'laminar'},multiphase={'model':'none'},radiation={'model':'none'}),materials=dict(fluid={'air':dict(density=value(props['density_kg_m3']),viscosity=value(props['viscosity_Pa_s']))}),cell_zones=dict(fluid={'fluid':{'general':{'material':'air'}}}),boundary_conditions=dict(velocity_inlet={'fuel_inlet':copy.deepcopy(inlet),'oxidizer_inlet':copy.deepcopy(inlet)},pressure_outlet={'outlet':{'momentum':{'gauge_pressure':value(0.)}}},wall={'wall':{'momentum':{'wall_motion':'Stationary Wall','shear_condition':'No Slip'}}}),methods={'spatial_discretization':{'discretization_scheme':{'mom':'second-order-upwind','pressure':'second-order'}}})

def test_air_verifies_actual_typed_fluent_constants():
    from nozzle_platform.air_solver import verify
    verify(configured_air(),request())

def test_air_counts_all_active_equation_histories_when_legacy_counters_are_zero(tmp_path):
    p=tmp_path/'data.h5'
    with h5py.File(p,'w') as h:
        for n in air.EQUATIONS:h['results/residuals/phase-1/'+n+'/iterations']=np.arange(1,26,dtype=float)
        h['settings/Data Variables']=np.array([b'((residuals/wb-iteration-count 0) (numerics/flexible/iteration-count 0))'])
    assert air.iteration_count(p)==25
    with h5py.File(p,'r+') as h:h['results/residuals/phase-1/z-velocity/iterations'][-1]=26
    with pytest.raises(ValueError):air.iteration_count(p)

def test_mesh_reuse_only_ignores_air_solver_files_not_meshing_or_workflow_changes():
    from nozzle_platform.mesh_reuse import compatibility,MESH_REQUIRED,AIR_SOLVER_ONLY
    old={n:'a'*64 for n in MESH_REQUIRED|AIR_SOLVER_ONLY};new=dict(old);new['nozzle_platform/air_model.py']='b'*64
    assert compatibility(old,new)['compatible'] and compatibility(old,new)['changed_files']==['nozzle_platform/air_model.py']
    new['nozzle_platform/layered_mesh.py']='c'*64;assert not compatibility(old,new)['compatible']
    new=dict(old);new.pop('nozzle_platform/workflows.py');assert not compatibility(old,new)['compatible']

def test_air_mesh_resume_can_only_reduce_steps_and_preserves_source_request(tmp_path,monkeypatch):
    from tests.test_pipelines import store as fixture_store
    from tests.test_pipeline_mesh_resume import blocked
    # Use the existing explicitly synthetic controller fixture, not CFD output.
    generator=fixture_store.__wrapped__(tmp_path,monkeypatch)
    try:
        s=next(generator) if hasattr(generator,'__next__') else generator
        old=blocked(s,monkeypatch);raw=s.workflows.jobs[old['job_id']]
        raw['config']['flow']={k:v for k,v in request().items() if k not in ('mesh_id','domain_id','scheme_id')}
        captured={}
        monkeypatch.setattr(s,'start',lambda r,**kw:captured.update(request=r,extra=kw) or {'planned':True})
        assert s.start_from_mesh(old['job_id'],iterations=75)=={'planned':True}
        assert captured['request']['flow']['iterations']==75 and raw['config']['flow']['iterations']==100
        assert captured['request']['flow']['fuel_velocity_m_s']==1.
        for bad in (101,0,True):
            with pytest.raises(ValueError):s.start_from_mesh(old['job_id'],iterations=bad)
    finally:
        if hasattr(generator,'close'):generator.close()

@pytest.mark.parametrize('fault',['speed','expression','pressure','wall','species','gravity'])
def test_air_rejects_actual_physical_setting_changes(fault):
    from nozzle_platform.air_solver import verify
    c=configured_air()
    if fault=='speed':c['boundary_conditions']['velocity_inlet']['fuel_inlet']['momentum']['velocity_magnitude']['value']=2.
    elif fault=='expression':c['boundary_conditions']['velocity_inlet']['fuel_inlet']['momentum']['velocity_magnitude']['option']='expression'
    elif fault=='pressure':c['boundary_conditions']['pressure_outlet']['outlet']['momentum']['gauge_pressure']['value']=1.
    elif fault=='wall':c['boundary_conditions']['wall']['wall']['momentum']['shear_condition']='Specified Shear'
    elif fault=='species':c['models']['species']['model']['option']='species-transport'
    else:c['general']['operating_conditions']['gravity']['enable']=True
    with pytest.raises(ValueError):verify(c,request())
