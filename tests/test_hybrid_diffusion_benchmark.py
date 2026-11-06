import numpy as np
import pytest
from nozzle_platform.hybrid_diffusion_benchmark import profile,reference_rates,C,A,B,D,L,AREA,Q,mesh,render_udf

def test_profile_is_nonuniform_bounded_and_composition_closed():
    y=profile(np.linspace(0,L,1001))
    assert y.shape==(1001,4) and np.all(y>=0) and np.all(y<=1)
    np.testing.assert_allclose(y.sum(axis=1),1,atol=2e-16,rtol=0)
    np.testing.assert_allclose(y[[0,-1],0],[.1,.15],atol=1e-15,rtol=0)

def test_analytic_flux_independent_of_position_and_bulk_flow_zero():
    x=np.linspace(.001,.009,11);h=1e-8
    derivative=(profile(x+h)[:,0]-profile(x-h)[:,0])/(2*h)
    rho=C/(A+B*profile(x)[:,0])
    np.testing.assert_allclose(D*rho*derivative*AREA,Q,rtol=1e-8,atol=0)
    rates=reference_rates()
    np.testing.assert_allclose(np.sum(list(rates.values()),axis=0),0,atol=1e-25)
    assert sum(rates['inlet'])==0 and rates['inlet'][0]<0 and rates['outlet'][0]>0

@pytest.mark.parametrize('x',[-.001,.011,float('nan')])
def test_invalid_coordinates_rejected(x):
    with pytest.raises(ValueError):profile(x)

def test_mesh_has_two_velocity_inlets_and_preserves_existing_file(tmp_path):
    p=tmp_path/'slab.msh';m=mesh(p);text=p.read_text()
    assert m['cells']==256 and '(39 (5 velocity-inlet outlet 1)())' in text
    assert 'pressure-outlet' not in text and 'expected_mass_flow_kg_s' not in m
    with pytest.raises(ValueError):mesh(p)

def test_seed_is_separate_from_readonly_probe():
    c=render_udf();probe=c.split('DEFINE_ON_DEMAND(m27_probe)')[1]
    assert 'C_YI(c,t,0)=h' in c
    assert 'Alloc_Storage' not in probe and 'C_YI(c,t,0)=' not in probe
    assert 'C_R(c,tc)*di*' in probe and 'SV_ALLOCATED_P' in probe

def test_zero_mass_balance_cannot_hide_missing_diffusion_flux():
    from nozzle_platform.hybrid_diffusion_benchmark import assess_report
    assert not assess_report({'boundary_rates_kg_s':{'inlet':[0.]*4,'outlet':[0.]*4}})['passed']
    assert assess_report({'boundary_rates_kg_s':reference_rates()})['passed']

def test_two_inlet_signature_preserves_boundary_values():
    import copy
    from nozzle_platform.hybrid_diffusion_benchmark import physical_signature
    bc=dict(momentum=dict(velocity_specification_method='normal',reference_frame='Absolute',velocity_magnitude=0),thermal={},species={'h2':.1})
    s=dict(general={},materials={},models=dict(viscous={'model':'laminar'},energy={},species=dict(model={},reactions={'enable_volumetric_reactions':False},options=dict(inlet_diffusion=True,diffusion_energy_source=False,thermal_diffusion=False,multi_component_diffusion=False))),boundary_conditions=dict(velocity_inlet={'inlet':bc,'outlet':copy.deepcopy(bc)},symmetry={'sides':{}}))
    flags=dict(thermal_diffusion=False,multi_component_diffusion=False)
    a=physical_signature(s,flags);changed=copy.deepcopy(s);changed['boundary_conditions']['velocity_inlet']['outlet']['species']['h2']=.15
    assert physical_signature(changed,flags)!=a
    hidden=copy.deepcopy(s)
    for key in flags:hidden['models']['species']['options'].pop(key)
    assert physical_signature(hidden,flags)==a
    with pytest.raises(ValueError):physical_signature(hidden,dict(thermal_diffusion=True,multi_component_diffusion=False))

@pytest.mark.parametrize('covered',[2,3])
def test_boundary_density_reports_adjacent_cell_convention_and_missing_zone(tmp_path,covered):
    import h5py
    from nozzle_platform.hybrid_diffusion_benchmark import boundary_density_diagnostic,MW,C
    case=tmp_path/'case.h5';data=tmp_path/'data.h5'
    y=np.array([[.1,.1,.1,.7],[.15,.1,.1,.65],[.12,.1,.1,.68]])
    neighbour=np.array([.48,.38,.44])
    with h5py.File(case,'w') as h:
        top=h.create_group('meshes/1/faces/zoneTopology');top['name']=np.array([b'inlet;outlet;sides']);top['minId']=[1,2,3];top['maxId']=[1,2,3]
        h['meshes/1/faces/c0/1']=[1,2,3]
    with h5py.File(data,'w') as h:
        for name,values in [('SV_Y',y[:covered]),('SV_DENSITY',neighbour[:covered])]:
            ds=h.create_dataset('results/1/phase-1/faces/'+name+'/1',data=values);ds.attrs['minId']=[1]
        h['results/1/phase-1/cells/SV_DENSITY/1']=neighbour
    rows=boundary_density_diagnostic(case,data)
    assert rows[0]['maximum_absolute_adjacent_cell_density_difference']==0
    assert rows[0]['maximum_relative_formula_error']>.001
    if covered==2:assert rows[2]['status']=='not_covered' and 'maximum_relative_formula_error' not in rows[2]
    else:assert rows[2]['maximum_absolute_adjacent_cell_density_difference']==0
