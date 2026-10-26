"""Finite/positive cell fields and native temperature clipping are separate gates."""
import h5py
import numpy as np
import pytest
from nozzle_platform.field_health import inspect,require_healthy


def fixture(directory):
    with h5py.File(directory/'final.cas.h5','w') as f:
        f['meshes/1/cells/zoneTopology/minId']=[1];f['meshes/1/cells/zoneTopology/maxId']=[2]
    with h5py.File(directory/'final.dat.h5','w') as f:
        for key,values in dict(SV_U=[-1,1],SV_V=[0,0],SV_W=[0,3],SV_P=[-2,2],
            SV_T=[299.9,300.1],SV_DENSITY=[.08,1.2],SV_MU_LAM=[8e-6,2e-5]).items():
            d=f.create_dataset('results/1/phase-1/cells/'+key+'/1',data=np.array(values,dtype=float));d.attrs['minId']=[1];d.attrs['maxId']=[2]
    (directory/'fluent.trn').write_text('100 complete iterations\n',encoding='utf-8')


def test_valid_fields_include_ranges_without_claiming_convergence(tmp_path):
    fixture(tmp_path);report=inspect(tmp_path);require_healthy(report)
    assert report['fields']['SV_P']=={'minimum':-2.,'maximum':2.}
    assert 'numerical_criteria_met' not in report


@pytest.mark.parametrize('field',['SV_U','SV_V','SV_W','SV_P','SV_T','SV_DENSITY','SV_MU_LAM'])
def test_nonfinite_field_is_rejected_even_if_species_sum_would_pass(tmp_path,field):
    fixture(tmp_path)
    with h5py.File(tmp_path/'final.dat.h5','r+') as f:f['results/1/phase-1/cells/'+field+'/1'][0]=np.nan
    with pytest.raises(ValueError,match='非有限'):inspect(tmp_path)


@pytest.mark.parametrize('field',['SV_T','SV_DENSITY','SV_MU_LAM'])
def test_nonpositive_thermodynamic_field_is_rejected(tmp_path,field):
    fixture(tmp_path)
    with h5py.File(tmp_path/'final.dat.h5','r+') as f:f['results/1/phase-1/cells/'+field+'/1'][0]=0
    with pytest.raises(ValueError,match='非正'):inspect(tmp_path)


@pytest.mark.parametrize('fault',['missing','chunk','ids','length'])
def test_partial_saved_arrays_cannot_pass(tmp_path,fault):
    fixture(tmp_path)
    with h5py.File(tmp_path/'final.dat.h5','r+') as f:
        group=f['results/1/phase-1/cells/SV_T'];d=group['1']
        if fault=='missing':del group['1']
        elif fault=='chunk':group['2']=[300.]
        elif fault=='ids':d.attrs['maxId']=[3]
        else:
            del group['1'];d=group.create_dataset('1',data=[300.]);d.attrs['minId']=[1];d.attrs['maxId']=[2]
    with pytest.raises(ValueError):inspect(tmp_path)


@pytest.mark.parametrize('bound',['minimum','maximum'])
def test_native_clipping_rejects_otherwise_finite_fields(tmp_path,bound):
    fixture(tmp_path)
    (tmp_path/'fluent.trn').write_text('Solution values for temperature have been clipped to their '+bound+' bound at 10 cells.\n',encoding='utf-8')
    report=inspect(tmp_path)
    with pytest.raises(ValueError,match='温度限幅'):require_healthy(report)


def test_inlet_temperature_limits_are_not_invented_as_field_bounds(tmp_path):
    fixture(tmp_path)
    with h5py.File(tmp_path/'final.dat.h5','r+') as f:f['results/1/phase-1/cells/SV_T/1'][:]=[199.9,600.1]
    require_healthy(inspect(tmp_path))
