"""Independent geometry limits and analytic heat-rate acceptance checks."""
import numpy as np
import pytest
from nozzle_platform.transport_benchmark import slab_mesh,assess_heat,saved_fields


def test_non_cubic_mesh_has_correct_volume_areas_and_deterministic_export(tmp_path):
    path=tmp_path/'slab.msh'
    mesh=slab_mesh(path,divisions=(8,3,2),lengths=(.02,.03,.04))
    assert mesh['cells']==48 and mesh['nodes']==108
    assert mesh['volume_m3']==pytest.approx(.02*.03*.04,rel=1e-13)
    assert mesh['heated_area_m2']==pytest.approx(.03*.04)
    for name in ['cold','hot']:assert mesh['boundary_area_m2'][name]==pytest.approx(.03*.04)
    assert mesh['boundary_area_m2']['insulated']==pytest.approx(2*.02*(.03+.04))
    assert mesh['max_area_closure_error_m2']<1e-16
    before=path.read_bytes();slab_mesh(path,divisions=(8,3,2),lengths=(.02,.03,.04))
    assert path.read_bytes()==before
    centres=np.asarray(mesh['cell_centres_m'])
    np.testing.assert_allclose(centres[0],[.02/16,.03/6,.04/4])


@pytest.mark.parametrize('divisions,lengths',[
    ((1,4,4),(.01,.01,.01)),((33,4,4),(.01,.01,.01)),((2.5,4,4),(.01,.01,.01)),
    ((True,4,4),(.01,.01,.01)),((4,4),(.01,.01,.01)),((4,4,4),(0,.01,.01)),
    ((4,4,4),(.01,float('nan'),.01)),((4,4,4),(.01,.01))])
def test_invalid_mesh_configuration_rejected(tmp_path,divisions,lengths):
    with pytest.raises(ValueError):slab_mesh(tmp_path/'bad.msh',divisions,lengths)


def test_analytic_heat_and_conservation_accept_correct_rate():
    mesh={'heated_area_m2':.0001,'lengths_m':[.01,.01,.01]}
    report=assess_heat({'cold':-.0093,'hot':.0093,'insulated':0},.093,mesh)
    assert report['passed']
    assert report['expected_heat_W']==pytest.approx(.0093)


@pytest.mark.parametrize('heat',[
    {'cold':.0093,'hot':.0093,'insulated':0},
    {'cold':-.0093,'hot':.0094,'insulated':0},
    {'cold':-.0093,'hot':.0093,'insulated':.0001},
    {'cold':-.00386,'hot':.00386,'insulated':0}])
def test_formula_substitution_or_heat_imbalance_cannot_pass(heat):
    mesh={'heated_area_m2':.0001,'lengths_m':[.01,.01,.01]}
    assert not assess_heat(heat,.093,mesh)['passed']


@pytest.mark.parametrize('heat',[
    {'cold':0,'hot':1}, {'cold':float('nan'),'hot':1,'insulated':0}])
def test_invalid_reports_are_rejected(heat):
    with pytest.raises(ValueError):assess_heat(heat,.093,{'heated_area_m2':.0001,'lengths_m':[.01]*3})


@pytest.fixture
def saved_cube(tmp_path):
    import h5py
    case,data=tmp_path/'cube.cas.h5',tmp_path/'cube.dat.h5'
    points=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]])*.1
    faces=np.array([[0,3,7,4],[1,5,6,2],[0,4,5,1],[3,2,6,7],[0,1,2,3],[4,7,6,5]])+1
    with h5py.File(case,'w') as f:
        f['meshes/1/cells/zoneTopology/minId']=[1];f['meshes/1/cells/zoneTopology/maxId']=[1]
        f['meshes/1/faces/zoneTopology/name']=[b'interior;cold;hot;insulated']
        f['meshes/1/faces/zoneTopology/minId']=[1,1,2,3]
        f['meshes/1/faces/zoneTopology/maxId']=[0,1,2,6]
        f['meshes/1/nodes/coords/1']=points
        f['meshes/1/faces/nodes/1/nnodes']=np.full(6,4)
        f['meshes/1/faces/nodes/1/nodes']=faces.reshape(-1)
        f['meshes/1/faces/c0/1']=np.ones(6,dtype=int)
        ds=f.create_dataset('meshes/1/faces/c1/1',data=np.array([],dtype=int));ds.attrs['minId']=[1]
    with h5py.File(data,'w') as f:
        for name,values in [('SV_Y',[[.1,.2,0,.7]]),('SV_MU_LAM',[1e-5]),('SV_T',[305.]),
                            ('SV_U',[0.]),('SV_V',[0.]),('SV_W',[0.])]:
            ds=f.create_dataset('results/1/phase-1/cells/'+name+'/1',data=values)
            ds.attrs['minId']=[1];ds.attrs['maxId']=[1]
        ds=f.create_dataset('results/1/phase-1/faces/SV_HEAT_FLUX/1',data=[-.125,.125,0.,0.,0.,0.])
        ds.attrs['minId']=[1];ds.attrs['maxId']=[6]
    return case,data


def test_saved_reader_preserves_integrated_heat_and_derives_real_cell_centres(saved_cube):
    result=saved_fields(*saved_cube)
    assert result['cells']==1
    assert result['heat_W']=={'cold':-.125,'hot':.125,'insulated':0.}
    np.testing.assert_allclose(result['cell_centres_m'],[[.05,.05,.05]])
    np.testing.assert_array_equal(result['fields']['SV_Y'],[[.1,.2,0,.7]])


@pytest.mark.parametrize('fault',['field_chunk','cell_ids','nonfinite','heat_ids','connectivity','unknown_boundary'])
def test_saved_reader_rejects_partial_or_invalid_evidence(saved_cube,fault):
    import h5py
    case,data=saved_cube
    if fault in {'field_chunk','cell_ids','nonfinite','heat_ids'}:
        with h5py.File(data,'a') as f:
            if fault=='field_chunk':f['results/1/phase-1/cells/SV_Y/2']=[[.1,.2,0,.7]]
            elif fault=='cell_ids':f['results/1/phase-1/cells/SV_Y/1'].attrs['minId']=[2]
            elif fault=='nonfinite':f['results/1/phase-1/cells/SV_T/1'][0]=float('nan')
            else:f['results/1/phase-1/faces/SV_HEAT_FLUX/1'].attrs['minId']=[2]
    else:
        with h5py.File(case,'a') as f:
            if fault=='connectivity':f['meshes/1/faces/c0/1'][0]=0
            else:f['meshes/1/faces/zoneTopology/name'][0]=b'interior;other;hot;insulated'
    with pytest.raises(ValueError):saved_fields(case,data)
