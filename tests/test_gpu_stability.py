import copy
import numpy as np
import pytest
from nozzle_platform.gpu_stability import alignment_bins,relative_drift,require_batch,batch_budget,static_pressure_drops,saved_geometry


@pytest.fixture
def accepted():
    return dict(status='saved_probe_audited',restart=True,nonuniform_original_viscosity_evidence_verified=True,
        initial_iteration=2050,saved_iteration=2075,complete_added_residuals=25,files_sha256={'input.dat.h5':'source'},
        saved_solver_controls=dict(species_relax=[.1]*3,species_scheme=[1]*3,linear_absolute_tolerance_double=1e-20,
            species_amg_alpha=[1e-6]*3,species_iter_tolerance=[1e-6]*3,species_max_iter=[100.]*3),
        saved_fields={n:dict(cells=851803,all_required_cell_fields_finite=True,species_sum_max_error=0.) for n in ['first','second','third','penultimate','final']})


def test_complete_saved_batch_can_advance(accepted):require_batch(accepted,'source',2050)


@pytest.mark.parametrize('fault',['first','second','third','penultimate','final','missing','chain','iterations','relax','viscosity','nan','partial','linear'])
def test_a_bad_early_checkpoint_or_wrong_chain_blocks_next_batch(accepted,fault):
    a=copy.deepcopy(accepted)
    if fault in a['saved_fields']:a['saved_fields'][fault]['species_sum_max_error']=2e-8
    elif fault=='missing':del a['saved_fields']['third']
    elif fault=='chain':a['files_sha256']['input.dat.h5']='other'
    elif fault=='iterations':a['complete_added_residuals']=24
    elif fault=='relax':a['saved_solver_controls']['species_relax']=[1.]*3
    elif fault=='viscosity':a['nonuniform_original_viscosity_evidence_verified']=False
    elif fault=='nan':a['saved_fields']['first']['species_sum_max_error']=float('nan')
    elif fault=='partial':a['saved_fields']['first']['cells']=851802
    elif fault=='linear':a['saved_solver_controls']['species_max_iter']=[5.]*3
    with pytest.raises(ValueError):require_batch(a,'source',2050)


@pytest.mark.parametrize('n',[0,5,100,True,1.5])
def test_iteration_budget_cannot_exceed_authorized_limit(n):
    with pytest.raises(ValueError):batch_budget(n)


def test_small_reference_field_is_not_silently_assigned_zero_drift():
    assert relative_drift([1,2],[1,2])==0
    assert relative_drift([1,0],[0,0]) is None
    with pytest.raises(ValueError):relative_drift([1],[float('nan')])


def test_old_cpu_iteration_count_cannot_meet_new_study_minimum():
    from nozzle_platform.gpu_stability import study_assessment
    from nozzle_platform.convergence import VERIFICATION_CRITERIA
    row={k:0. for k in VERIFICATION_CRITERIA if k!='scaled_residuals'}
    row.update(iteration=2050,block_steps=25,criteria_met=True,block_maximum_residuals={k:0. for k in VERIFICATION_CRITERIA['scaled_residuals']})
    r=study_assessment([row]*4)
    assert not r['minimum_iterations_met'] and not r['numerical_criteria_met']


@pytest.mark.parametrize('native,log,expected',[(False,'4 GiB available RAM required','resource_blocked'),
    (False,'4 GiB free GPU VRAM required','resource_blocked'),(False,'case hash mismatch','stopped_preflight_error'),
    (True,'Invalid species','stopped_rejected_batch')])
def test_preflight_refusal_is_distinct_from_native_species_failure(native,log,expected):
    from scripts.continue_nozzle_gpu import failure_status
    assert failure_status(native,log)==expected


def test_proxy_histogram_counts_every_cell_and_preserves_rejections():
    rows=alignment_bins([.15,.5,.9,1.],np.array([False,True,False,True]))
    assert sum(r['cells'] for r in rows)==4
    assert sum(r['rejected_cells'] for r in rows)==2
    assert rows[0]['rejected_fraction'] is None


@pytest.fixture
def cube(tmp_path):
    import h5py
    xyz=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]],float)
    polys=np.array([[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]])
    path=tmp_path/'cube.h5'
    with h5py.File(path,'w') as f:
        m=f.create_group('meshes/1');m.attrs['dimension']=[3]
        m['nodes/coords/1']=xyz;m['cells/zoneTopology/maxId']=[1]
        m['faces/nodes/1/nnodes']=np.full(6,4);m['faces/nodes/1/nodes']=polys.flatten()+1
        m['faces/c0/1']=np.ones(6,dtype=int);m['faces/c1/1']=np.array([],dtype=int);m['faces/c1/1'].attrs['minId']=[1]
        m['faces/zoneTopology/id']=[1,2,3];m['faces/zoneTopology/minId']=[1,3,5];m['faces/zoneTopology/maxId']=[2,4,6]
        m['faces/zoneTopology/name']=[b'fuel_inlet;oxidizer_inlet;outlet']
    return path


def test_real_polygon_geometry_recovers_known_cube(cube):
    g=saved_geometry(cube)
    np.testing.assert_allclose(g['centres'],[[.5,.5,.5]])
    np.testing.assert_allclose(g['alignment'],[1.])
    assert g['volume_m3']==pytest.approx(1.)
    for boundary in g['boundary'].values():np.testing.assert_allclose(boundary['areas'],[1,1])


def test_pressure_uses_saved_face_ids_and_area_weights(cube,tmp_path):
    import h5py
    from nozzle_platform.gpu_stability import write_geometry
    g=saved_geometry(cube);g['boundary']['fuel_inlet']['areas']=np.array([1.,3.])
    geo=tmp_path/'geometry.npz';write_geometry(geo,g)
    data=tmp_path/'data.h5'
    with h5py.File(data,'w') as f:
        d=f.create_dataset('results/1/phase-1/faces/SV_P/1',data=[10.,20.,30.,50.,1.,3.]);d.attrs['minId']=[1];d.attrs['maxId']=[6]
    r=static_pressure_drops(cube,data,geo)
    assert r=={'fuel_inlet':15.5,'oxidizer_inlet':38.}
    with h5py.File(data,'r+') as f:f['results/1/phase-1/faces/SV_P/1'].attrs['minId']=[2]
    with pytest.raises(ValueError):static_pressure_drops(cube,data,geo)


@pytest.mark.parametrize('resource',[False,True])
def test_controller_really_stops_after_first_failure_even_with_four_batches(tmp_path,monkeypatch,accepted,resource):
    import json,sys,types,psutil
    from pathlib import Path
    import scripts.continue_nozzle_gpu as controller
    source=tmp_path/'source';source.mkdir();(source/'spec.json').write_text(json.dumps({'mesh_fingerprint':'mesh'}))
    geometry=tmp_path/'geometry';geometry.mkdir();(geometry/'audit.json').write_text(json.dumps({'geometry_sha256':'hash','mesh_fingerprint':'mesh'}))
    output=tmp_path/'out';accepted['files_sha256']['input.dat.h5']='hash'
    monkeypatch.setattr(controller,'sha256',lambda p:'hash')
    monkeypatch.setattr(controller,'audit',lambda p:accepted)
    monkeypatch.setattr(controller,'audit_rejected',lambda p:{'status':'rejected_species_closure'})
    monkeypatch.setattr(controller,'saved_iteration',lambda p:2050 if p.name=='input.dat.h5' else 2075)
    monkeypatch.setattr(controller,'observe_batch',lambda *args:pytest.fail('Rejected batch reached observation'))
    monkeypatch.setattr(psutil,'virtual_memory',lambda:types.SimpleNamespace(available=8*2**30))
    monkeypatch.setattr(psutil,'process_iter',lambda *args:[])
    monkeypatch.setattr(controller.shutil,'disk_usage',lambda p:types.SimpleNamespace(free=8*2**30))
    calls=[]
    class Process:
        returncode=1
        def __init__(self,command,**kwargs):
            calls.append(command)
            if not resource:
                folder=Path(command[command.index('--output')+1]);folder.mkdir();(folder/'command.json').write_text('{}')
            kwargs['stdout'].write(b'4 GiB available RAM required' if resource else b'Invalid species')
        def wait(self,timeout):return 1
    class Job:
        def assign(self,process):pass
        def close(self):pass
    monkeypatch.setattr(controller.subprocess,'Popen',Process);monkeypatch.setattr(controller,'WindowsJob',Job)
    monkeypatch.setattr(sys,'argv',['continue','--source',str(source),'--geometry-review',str(geometry),
        '--cpu',str(tmp_path/'cpu'),'--gpu',str(tmp_path/'gpu'),'--output',str(output),'--batches','4'])
    controller.main();r=json.loads((output/'continuation.json').read_text(encoding='utf-8'))
    assert len(calls)==1 and r['accepted_added_iterations']==0 and r['all_native_processes_exited']
    assert r['status']==('resource_blocked' if resource else 'stopped_rejected_batch')


def test_common_profile_change_checks_positions_and_velocity_and_hydrogen():
    from scripts.audit_gpu_continuation import profile_change
    p=dict(points_m=[[0.,0.,1.]],columns=['u_m_s','v_m_s','w_m_s','h2_mass_fraction'],values=[[6.,0.,0.,.1]])
    old=dict(p,values=[[3.,0.,0.,.05]])
    assert profile_change(p,old)==pytest.approx(.5)
    assert profile_change(p,p)==0.
    with pytest.raises(ValueError):profile_change(p,dict(old,points_m=[[0.,0.,2.]]))
    with pytest.raises(ValueError):profile_change(p,dict(old,values=[[float('nan'),0.,0.,.05]]))
    with pytest.raises(ValueError):profile_change(dict(p,points_m=[]),dict(old,points_m=[]))
