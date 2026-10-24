import numpy as np
import pytest
from nozzle_platform.nonuniform_heat import band_indices,prescribed_y,face_heat_check,patch_schedule,energy_history,saved_energy_controls


def test_four_strips_include_pure_air_and_pure_hydrogen():
    p=np.column_stack([np.full(4,.005),[.00125,.00375,.00625,.00875],np.full(4,.005)])
    y=prescribed_y(p)
    np.testing.assert_array_equal(y[:,0],[0,.1,.5,1])
    np.testing.assert_allclose(y.sum(axis=1),1,rtol=0,atol=2e-16)
    np.testing.assert_array_equal(y[[0,3]],[[0,.233,0,.767],[1,0,0,0]])


@pytest.mark.parametrize('y',[[float('nan')],[-.001],[.011],[],[[.001]]])
def test_invalid_positions_reject(y):
    with pytest.raises(ValueError):band_indices(y)


@pytest.fixture
def heat():
    roles=np.array(['cold']*4+['hot']*4+['insulated'])
    areas=np.full(9,2.5e-5);bands=np.array([0,1,2,3,0,1,2,3,0])
    k=np.array([.02,.04,.08,.16]);q=np.r_[-k*.025,k*.025,0.]
    return roles,areas,bands,q,k


def test_physical_heat_identifies_each_strip_conductivity(heat):
    r=face_heat_check(*heat)
    assert r['passed'] and r['relative_heat_imbalance']==0
    np.testing.assert_allclose([x['hot_inferred_k_W_m_K'] for x in r['strips']],heat[-1])


def test_correct_total_heat_cannot_hide_wrong_local_conductivities(heat):
    roles,areas,bands,q,k=heat
    q[:4]=-q[4:8].mean();q[4:8]=q[4:8].mean()
    r=face_heat_check(roles,areas,bands,q,k)
    assert r['relative_heat_imbalance']==0
    assert not r['passed']


@pytest.mark.parametrize('fault',['area_twice','same_sign','leak','nonfinite','missing_strip','bad_units'])
def test_incorrect_heat_or_incomplete_evidence_cannot_pass(heat,fault):
    roles,areas,bands,q,k=heat
    if fault=='area_twice':q=q*areas
    if fault=='same_sign':q=abs(q)
    if fault=='leak':q[-1]=.0001
    if fault=='nonfinite':q[0]=np.nan
    if fault=='missing_strip':bands[7]=2
    if fault=='bad_units':areas=areas*1e6
    try:r=face_heat_check(roles,areas,bands,q,k)
    except ValueError:return
    assert not r['passed']


def test_native_patch_sequence_avoids_intermediate_species_normalization():
    # Native behavior observed during preparation: solved fractions are scaled
    # if their sum exceeds one, before the implicit N2 fraction is calculated.
    def native_patch(y,index,value):
        y[index]=value
        if y[:3].sum()>1:y[:3]/=y[:3].sum()
        y[3]=1-y[:3].sum()
    wrong=np.array([.1,.2097,0,.6903])
    for i,v in [(0,1.),(1,0.),(2,0.)]:native_patch(wrong,i,v)
    assert wrong[0]==pytest.approx(1/1.2097)
    for target in [0,.1,.5,1.]:
        y=np.array([.1,.2097,0,.6903])
        for i,v in patch_schedule(target):native_patch(y,i,v)
        np.testing.assert_allclose(y,[target,.233*(1-target),0,.767*(1-target)],rtol=0,atol=1e-15)


@pytest.mark.parametrize('ids,values',[
    ([1,2],np.zeros((2,3))),([1,1],np.zeros((2,4))),([1,2.5],np.zeros((2,4))),
    ([1,2],np.full((2,4),np.nan)),([],np.zeros((0,4)))])
def test_invalid_or_partial_energy_history_rejected(tmp_path,ids,values):
    import h5py
    p=tmp_path/'energy.h5'
    with h5py.File(p,'w') as f:
        f['results/residuals/phase-1/energy/iterations']=ids;f['results/residuals/phase-1/energy/data']=values
    with pytest.raises(ValueError):energy_history(p)


@pytest.mark.parametrize('fault',[None,'flow','species','energy','diffusion','duplicate','missing'])
def test_actual_saved_energy_controls_reject_unintended_physics(tmp_path,fault):
    import h5py
    text='(flow/solve? #f) (species/solve? (#f #f #f)) (temperature/solve? #t) (energy/species-diffusion? #f)'
    edits={'flow':('flow/solve? #f','flow/solve? #t'),
           'species':('(#f #f #f)','(#f #t #f)'),
           'energy':('temperature/solve? #t','temperature/solve? #f'),
           'diffusion':('energy/species-diffusion? #f','energy/species-diffusion? #t'),
           'missing':('(flow/solve? #f)','')}
    if fault in edits:text=text.replace(*edits[fault])
    if fault=='duplicate':text+=' (flow/solve? #f)'
    p=tmp_path/'controls.h5'
    with h5py.File(p,'w') as f:f['settings/Rampant Variables']=np.array([text.encode()])
    if fault is None:assert saved_energy_controls(p)['temperature/solve?']=='#t'
    else:
        with pytest.raises(ValueError):saved_energy_controls(p)
