"""Analytical saved-field probes with reused geometry, not CFD solutions."""
import h5py
import numpy as np
import pytest

from nozzle_platform.outlet_sampling import prepare,sample


def saved_fields(tmp_path):
    centres=np.array([(x,y) for x in [-.02,0,.02] for y in [-.02,0,.02]])
    xyz=np.array([(x+dx,y+dy,.05) for x,y in centres for dx,dy in [(-.009,-.009),(.009,-.009),(.009,.009),(-.009,.009)]])
    case=tmp_path/'mesh.cas.h5';data=tmp_path/'flow.dat.h5'
    with h5py.File(case,'w') as f:
        mesh=f.create_group('meshes/1');mesh['nodes/coords/1']=xyz
        mesh['faces/nodes/1/nnodes']=[3,5]+[4]*9+[3]
        mesh['faces/nodes/1/nodes']=np.r_[[1,2,3,1,2,3,4,5],np.arange(1,37),[1,2,3]]
        top=mesh.create_group('faces/zoneTopology');top['name']=[b'wall;outlet;other-wall']
        top['minId']=[1,3,12];top['maxId']=[2,11,12]
    with h5py.File(data,'w') as f:
        values={'SV_U':1+2*centres[:,0],'SV_V':3*centres[:,1],'SV_W':np.full(9,2.),
                'SV_Y':np.column_stack([.03+centres[:,0],np.zeros((9,2)),.97-centres[:,0]])}
        for name,value in values.items():
            array=np.full((12,)+value.shape[1:],1e6);array[2:11]=value
            ds=f.create_dataset('results/1/phase-1/faces/'+name+'/1',data=array);ds.attrs['minId']=[1]
    return case,data,{'config':{'chamber_radius_mm':20}},centres


def test_cached_geometry_samples_analytical_fields_after_data_changes(tmp_path):
    case,data,domain,centres=saved_fields(tmp_path)
    sampler=prepare(case,domain);profile=sample(data,sampler)
    xy=np.array(profile['points_m']);expected=np.column_stack([1+2*xy[:,0],3*xy[:,1],np.full(len(xy),2.),.03+xy[:,0]])
    np.testing.assert_allclose(profile['values'],expected,rtol=1e-13,atol=1e-14)
    assert profile['h2_area_mean']==pytest.approx(.03)
    assert profile['velocity_area_rms']==pytest.approx(np.sqrt(np.mean((1+2*centres[:,0])**2+(3*centres[:,1])**2+4)))
    # Sampling subsequent data does not need another mesh file read.
    case.unlink()
    with h5py.File(data,'r+') as f:
        ds=f['results/1/phase-1/faces/SV_U/1'];values=ds[()];values[2:11]*=2;ds[:]=values
    updated=sample(data,sampler);expected[:,0]*=2
    np.testing.assert_allclose(updated['values'],expected,rtol=1e-13,atol=1e-14)


@pytest.mark.parametrize('problem',['range','species','nonfinite'])
def test_cached_outlet_rejects_incompatible_or_invalid_saved_fields(tmp_path,problem):
    case,data,domain,_=saved_fields(tmp_path);sampler=prepare(case,domain)
    with h5py.File(data,'r+') as f:
        if problem=='range':f['results/1/phase-1/faces/SV_U/1'].attrs['minId']=[4]
        if problem=='nonfinite':f['results/1/phase-1/faces/SV_W/1'][5]=float('nan')
        if problem=='species':
            del f['results/1/phase-1/faces/SV_Y/1']
            ds=f.create_dataset('results/1/phase-1/faces/SV_Y/1',data=np.zeros((12,3)));ds.attrs['minId']=[1]
    with pytest.raises(ValueError):sample(data,sampler)


def test_cached_outlet_rejects_probes_outside_saved_face_geometry(tmp_path):
    case,_,_,_=saved_fields(tmp_path)
    with pytest.raises(ValueError,match='插值范围外'):prepare(case,{'config':{'chamber_radius_mm':200}})
