"""Saved-file audit must reject incomplete or mismatched evidence."""
import copy
import json

import h5py
import numpy as np
import pytest

from nozzle_platform.transport_audit import components, evaluate
from scripts.audit_gpu_transport import load, run


@pytest.fixture
def folders(tmp_path):
    data={'h2':(2.01594,8.411e-6,.1672), 'o2':(31.9988,1.919e-5,.0246),
          'h2o':(18.01534,1.34e-5,.0261), 'n2':(28.0134,1.663e-5,.0242)}
    species={name:{key:{'option':'value','value':value} for key,value in
                  zip(('molecular_weight','viscosity','thermal_conductivity'),values)} for name,values in data.items()}
    paths=[]
    for name,law in [('cpu','ideal-gas-mixing-law'),('gpu','mass-weighted-mixing-law')]:
        folder=tmp_path/name;folder.mkdir();paths.append(folder)
        state={'materials':{'mixture':{'hydrogen-air':{'species':{
            'volumetric':list(data),'volumetric_species':copy.deepcopy(species)},
            'viscosity':{'option':law},'thermal_conductivity':{'option':law}}}},
            'models':{'species':{'reactions':{'enable_volumetric_reactions':False}}}}
        (folder/'configured.json').write_text(json.dumps(state),encoding='utf-8')
        (folder/'job.json').write_text(json.dumps({'config':{'oxidizer_o2_mass_fraction':.233}}),encoding='utf-8')
        y=np.array([[1,0,0,0],[0,.233,0,.767],[.1,.2097,0,.6903]])
        calculated=evaluate(y,components(state))
        with h5py.File(folder/'final.cas.h5','w') as case:
            case['meshes/1/cells/zoneTopology/minId']=[1]
            case['meshes/1/cells/zoneTopology/maxId']=[3]
        with h5py.File(folder/'final.dat.h5','w') as saved:
            key='ideal_viscosity' if name=='cpu' else 'mass_viscosity'
            for field,value in [('SV_Y',y),('SV_MU_LAM',calculated[key]),('SV_T',np.full(3,300.))]:
                ds=saved.create_dataset('results/1/phase-1/cells/'+field+'/1',data=value)
                ds.attrs['minId']=[1];ds.attrs['maxId']=[3]
    return paths


def test_audit_artifacts_and_scientific_flags(folders,tmp_path):
    report=run(*folders,tmp_path/'out')
    assert report['new_fluent_iterations']==0
    assert report['dry_h2_air_sweep']['conductivity']['absolute_relative_max']>.58
    assert not any(report[key] for key in ['scientific_eligible','grid_independence_verified','numerical_criteria_met','formal_physics_validated'])
    assert (tmp_path/'out/mixture_sweep.csv').exists()
    assert report['original_saved_case']['files_sha256']['final.dat.h5']
    assert set(report['artifacts_sha256'])=={'mixture_sweep.csv'}
    with pytest.raises(ValueError,match='new directory'):run(*folders,tmp_path/'out')


@pytest.mark.parametrize('fault',['chunk','cell_ids','cell_count','bad_mu','wrong_law','reaction'])
def test_incomplete_or_wrong_saved_evidence_is_rejected(folders,tmp_path,fault):
    cpu,gpu=folders
    if fault in {'chunk','cell_ids','bad_mu'}:
        with h5py.File(cpu/'final.dat.h5','a') as saved:
            cells=saved['results/1/phase-1/cells']
            if fault=='chunk':cells['SV_Y/2']=np.zeros((1,4))
            elif fault=='cell_ids':cells['SV_Y/1'].attrs['maxId']=[2]
            else:cells['SV_MU_LAM/1'][0]=99.
    elif fault=='cell_count':
        with h5py.File(cpu/'final.cas.h5','a') as case:case['meshes/1/cells/zoneTopology/maxId'][0]=4
    else:
        state=json.loads((cpu/'configured.json').read_text(encoding='utf-8'))
        if fault=='wrong_law':state['materials']['mixture']['hydrogen-air']['viscosity']['option']='value'
        else:state['models']['species']['reactions']['enable_volumetric_reactions']=True
        (cpu/'configured.json').write_text(json.dumps(state),encoding='utf-8')
    with pytest.raises(ValueError):run(cpu,gpu,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_mismatched_component_constants_rejected(folders,tmp_path):
    cpu,gpu=folders
    state=json.loads((gpu/'configured.json').read_text(encoding='utf-8'))
    state['materials']['mixture']['hydrogen-air']['species']['volumetric_species']['h2']['viscosity']['value']*=2
    (gpu/'configured.json').write_text(json.dumps(state),encoding='utf-8')
    with pytest.raises(ValueError,match='Component properties/order differ'):
        run(cpu,gpu,tmp_path/'mismatched')
