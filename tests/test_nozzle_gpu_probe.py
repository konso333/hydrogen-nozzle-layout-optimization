"""Saved-file completeness, independent flux sums and composition-lag rejection."""
import h5py
import numpy as np
import pytest
from nozzle_platform.nozzle_gpu_probe import read_saved,compare_compositions,diagnose_species,saved_solver_controls
from nozzle_platform.transport_audit import evaluate


@pytest.fixture
def material():
    return dict(names=['h2','o2','h2o','n2'],molecular_weight=[2.01594,31.9988,18.01534,28.0134],
        viscosity=[8.411e-6,1.919e-5,1.34e-5,1.663e-5],thermal_conductivity=[.1672,.0246,.0261,.0242])


@pytest.fixture
def pair(tmp_path,material):
    case,data=tmp_path/'c.cas.h5',tmp_path/'d.dat.h5'
    with h5py.File(case,'w') as f:
        f['meshes/1/cells/zoneTopology/minId']=[1];f['meshes/1/cells/zoneTopology/maxId']=[2]
        top=f.create_group('meshes/1/faces/zoneTopology')
        top['name']=[b'interior;fuel_inlet;oxidizer_inlet;outlet;wall']
        top['minId']=[1,2,3,4,5];top['maxId']=[1,2,3,4,5]
    def field(f,name,values,first,last):
        d=f.create_dataset(name+'/1',data=np.asarray(values,dtype=float));d.attrs['minId']=[first];d.attrs['maxId']=[last]
    y=np.array([[.1,.2097,0,.6903],[.5,.1165,0,.3835]])
    with h5py.File(data,'w') as f:
        cell=f.create_group('results/1/phase-1/cells')
        for n,values in dict(SV_U=[0,1],SV_V=[0,0],SV_W=[0,1],SV_P=[1,2],SV_T=[300,301],SV_DENSITY=[1,1],SV_MU_LAM=evaluate(y,material)['ideal_viscosity'],SV_Y=y).items():
            field(cell,n,values,1,2)
        face=f.create_group('results/1/phase-1/faces')
        field(face,'SV_FLUX',[0,-1,-1,2,0],1,5)
        field(face,'SV_Y',[[1,0,0,0],[0,.233,0,.767],[.5,.1165,0,.3835],[0,.233,0,.767]],2,5)
    return case,data


def test_saved_fluxes_cover_interior_and_non_one_boundary_ids(pair):
    result=read_saved(*pair)['summary']
    assert result['cells']==2
    assert result['relative_mass_imbalance']==0 and result['h2_relative_convective_imbalance']==0
    assert result['boundary_rates']['outlet']['species_convective_flow_kg_s'][0]==-1
    assert not result['formal_physics_validated'] and not result['numerical_criteria_met']


@pytest.mark.parametrize('damage',['chunk','ids','missing_ids','nan','species_sum','negative_species','density','face_chunk','face_range','flow_direction','extra_zone'])
def test_invalid_saved_evidence_cannot_be_accepted(pair,damage):
    case,data=pair
    with h5py.File(data,'r+') as f:
        c=f['results/1/phase-1/cells'];faces=f['results/1/phase-1/faces']
        if damage=='chunk':c['SV_Y/2']=c['SV_Y/1'][()]
        if damage=='ids':c['SV_MU_LAM/1'].attrs['maxId']=[1]
        if damage=='missing_ids':del c['SV_T/1'].attrs['minId']
        if damage=='nan':c['SV_U/1'][0]=np.nan
        if damage=='species_sum':c['SV_Y/1'][0,0]=.2
        if damage=='negative_species':c['SV_Y/1'][0,2]=-.01
        if damage=='density':c['SV_DENSITY/1'][0]=0
        if damage=='face_chunk':faces['SV_Y/2']=faces['SV_Y/1'][()]
        if damage=='face_range':faces['SV_Y/1'].attrs['minId']=[3]
        if damage=='flow_direction':faces['SV_FLUX/1'][1]=1
    if damage=='extra_zone':
        with h5py.File(case,'r+') as f:f['meshes/1/faces/zoneTopology/name'][0]=b'unknown;fuel_inlet;oxidizer_inlet;outlet;wall'
    with pytest.raises(ValueError):read_saved(*pair)


def test_previous_composition_match_is_not_current_composition_validation(material):
    previous=dict(y=np.array([[.1,.2097,0,.6903],[.5,.1165,0,.3835]]))
    current=dict(y=np.array([[.2,.1864,0,.6136],[.6,.0932,0,.3068]]))
    current['mu']=evaluate(previous['y'],material)['ideal_viscosity']
    r=compare_compositions(current,previous,material)
    assert r['previous_composition_matches_ideal']
    assert not r['current_composition_matches_ideal']
    assert not r['conductivity_independently_verified']


def test_substituted_mass_weighted_viscosity_is_detected(material):
    y=np.array([[.1,.2097,0,.6903],[.5,.1165,0,.3835]])
    current=dict(y=y,mu=evaluate(y,material)['mass_viscosity'])
    r=compare_compositions(current,current,material)
    assert not r['current_composition_matches_ideal']
    assert not r['previous_composition_matches_ideal']
    assert r['mass_current']['absolute_relative_max']==0


def test_rejected_species_are_reported_without_changing_or_accepting_them(pair):
    case,data=pair
    with h5py.File(data,'r+') as f:f['results/1/phase-1/cells/SV_Y/1'][0]=[1.,2e-7,0,0]
    before=data.read_bytes()
    r=diagnose_species(case,data)
    assert r['cells_above_sum_limit']==r['bad_cells_with_zero_n2']==1
    assert r['cell_sum_max_error']==pytest.approx(2e-7)
    assert r['worst_cells'][0]['cell_id']==1
    assert not r['accepted'] and not r['formal_physics_validated']
    assert data.read_bytes()==before
    with pytest.raises(ValueError):read_saved(case,data)


@pytest.mark.parametrize('order,tolerance',[(0,1e-16),(1,1e-20)])
def test_saved_controls_read_actual_case_not_report(pair,order,tolerance):
    case,_=pair
    with h5py.File(case,'r+') as f:
        f['settings/Rampant Variables']=[f'(species/scheme ({order} {order} {order}))\n(gpuapp/solver/absolute-tolerance-double {tolerance})'.encode()]
    assert saved_solver_controls(case)==dict(species_scheme=[order]*3,linear_absolute_tolerance_double=tolerance)


@pytest.mark.parametrize('text',[
    '(species/scheme (1 1 1))',
    '(species/scheme (1 1 1)) (species/scheme (0 0 0)) (gpuapp/solver/absolute-tolerance-double 1e-16)',
    '(species/scheme (1 1 1)) (gpuapp/solver/absolute-tolerance-double nan)',
    '(species/scheme (1 1 1)) (gpuapp/solver/absolute-tolerance-double 0)',
    '(species/scheme (1 1)) (gpuapp/solver/absolute-tolerance-double 1e-16)'])
def test_missing_or_ambiguous_saved_controls_reject(pair,text):
    case,_=pair
    with h5py.File(case,'r+') as f:f['settings/Rampant Variables']=[text.encode()]
    with pytest.raises(ValueError):saved_solver_controls(case)


def test_strict_species_linear_values_are_read_from_saved_case(pair):
    case,_=pair
    with h5py.File(case,'r+') as f:
        f['settings/Rampant Variables']=[b'(species/scheme (1 1 1))\n(gpuapp/solver/absolute-tolerance-double 1e-20)\n(species/amg-alpha (1e-6 1e-6 1e-6))\n(species/iter-tolerance (1e-6 1e-6 1e-6))\n(species/max-iter (100 100 100))']
    r=saved_solver_controls(case)
    assert r['species_amg_alpha']==r['species_iter_tolerance']==[1e-6]*3
    assert r['species_max_iter']==[100]*3


@pytest.mark.parametrize('tail',[
    '(species/amg-alpha (1e-6 1e-6))',
    '(species/iter-tolerance (1e-6 nan 1e-6))',
    '(species/max-iter (100 0 100))',
    '(species/amg-alpha (1e-6 1e-6 1e-6)) (species/amg-alpha (1e-6 1e-6 1e-6))'])
def test_partial_or_invalid_species_linear_controls_reject(pair,tail):
    case,_=pair
    with h5py.File(case,'r+') as f:
        f['settings/Rampant Variables']=[('(species/scheme (1 1 1)) (gpuapp/solver/absolute-tolerance-double 1e-20) '+tail).encode()]
    with pytest.raises(ValueError):saved_solver_controls(case)
