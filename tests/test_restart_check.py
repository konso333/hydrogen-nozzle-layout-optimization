"""Reading a file and preserving every checked field are distinct outcomes."""
import shutil
import h5py
import numpy as np
import pytest
from nozzle_platform.restart_check import compare,message,journal
from tests.test_pipelines import store,request,finished


def pair(folder):
    case=folder/'final.cas.h5';before=folder/'final.dat.h5';after=folder/'reopened.dat.h5'
    with h5py.File(case,'w') as f:
        f['meshes/1/cells/zoneTopology/minId']=[1];f['meshes/1/cells/zoneTopology/maxId']=[2]
    with h5py.File(before,'w') as f:
        f['results/residuals/phase-1/h2/iterations']=[99,100]
        for part,fields in [('cells',dict(SV_U=[1.,2.],SV_V=[0.,0.],SV_W=[1.,3.],SV_P=[-1.,2.],SV_T=[300.,300.],SV_DENSITY=[.1,1.],SV_MU_LAM=[1e-5,2e-5],SV_Y=[[1.,0,0,0],[0,.233,0,.767]])),('faces',dict(SV_FLUX=[1e-5,-1e-5],SV_Y=[[1.,0,0,0],[0,.233,0,.767]]))]:
            for key,values in fields.items():
                d=f.create_dataset('results/1/phase-1/'+part+'/'+key+'/1',data=np.array(values,float));d.attrs['minId']=[1];d.attrs['maxId']=[2]
    shutil.copyfile(before,after);return case,before,after


def test_equal_fields_do_not_claim_convergence(tmp_path):
    result=compare(*pair(tmp_path))
    assert result['native_read_completed'] and result['field_preservation_passed']
    assert result['added_iterations']==0 and result['saved_iteration']==100
    assert not result['numerical_criteria_met'] and not result['formal_physics_validated']


@pytest.mark.parametrize('part,key',[('cells','SV_U'),('cells','SV_T'),('cells','SV_Y'),('faces','SV_FLUX'),('faces','SV_Y')])
def test_readability_cannot_hide_changed_fields(tmp_path,part,key):
    paths=pair(tmp_path)
    with h5py.File(paths[2],'r+') as f:f['results/1/phase-1/'+part+'/'+key+'/1'][0]=0
    result=compare(*paths)
    assert result['native_read_completed'] and not result['field_preservation_passed']
    assert '未通过' in message(result)


def test_native_species_correction_does_not_weaken_existing_preservation_tolerance(tmp_path):
    paths=pair(tmp_path)
    with h5py.File(paths[2],'r+') as f:f['results/1/phase-1/cells/SV_Y/1'][0]=[1-6e-11,6e-11,0,0]
    result=compare(*paths)
    assert not result['field_preservation_passed']
    c=next(c for c in result['checks'] if c['part']=='cells' and c['field']=='SV_Y')
    assert c['absolute_tolerance']==1e-12


@pytest.mark.parametrize('fault',['iteration','ids','missing','nonfinite'])
def test_incomplete_or_advanced_reader_is_rejected(tmp_path,fault):
    paths=pair(tmp_path)
    with h5py.File(paths[2],'r+') as f:
        d=f['results/1/phase-1/cells/SV_U/1']
        if fault=='iteration':f['results/residuals/phase-1/h2/iterations'][-1]=101
        elif fault=='ids':d.attrs['maxId']=[3]
        elif fault=='missing':del f['results/1/phase-1/cells/SV_U']
        else:d[0]=np.nan
    with pytest.raises((ValueError,KeyError)):compare(*paths)


def test_restart_journal_does_not_initialize_or_iterate():
    text=journal('case1_final.cas.h5')
    assert '/file/read-case-data case1_final.cas.h5' in text and '/solve/' not in text
    for name in ['../file.cas.h5','file.cas.h5\n/solve/iterate 10','a b.cas.h5']:
        with pytest.raises(ValueError):journal(name)


def test_pipeline_displays_readable_but_changed_without_claiming_restart_pass(store,monkeypatch):
    config=request();config['flow']['species_solver_control']='strict-absolute'
    monkeypatch.setattr('nozzle_platform.restart_check.run_native',lambda *a:dict(native_read_completed=True,field_preservation_passed=False,added_iterations=0))
    task=finished(store,config);row=task['cases'][0]
    assert row['status']=='not_converged' and not row['restart_check']['field_preservation_passed']
    assert '未通过' in row['restart_message'] and not task['comparison_eligible']
    assert '/solve/' not in store.file(task['job_id'],'case1_reopen_gpu.jou').read_text()
    assert '重读有变化' in store.file(task['job_id'],'report.md').read_text(encoding='utf-8')


def test_unverified_reader_cleanup_stops_pipeline_before_maps(store,monkeypatch):
    from nozzle_platform.restart_check import RestartCleanupError
    config=request();config['flow']['species_solver_control']='strict-absolute'
    def fail(*a):raise RestartCleanupError('cleanup unverified')
    monkeypatch.setattr('nozzle_platform.restart_check.run_native',fail)
    task=finished(store,config)
    assert task['cases'][0]['status']=='failed' and not any(f['name']=='case1_speed.png' for f in task['files'])
