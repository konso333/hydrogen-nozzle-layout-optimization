"""Reuse verified mesh before solver launch without overwriting the failed task."""
import copy,time
import pytest
from tests.test_pipelines import store,request,finished
from nozzle_platform.pipelines import ResourcesUnavailable
from nozzle_platform.workflows import ROOT,IMPLEMENTATION,sha256,save
from tests.test_platform import server,call


def blocked(store,monkeypatch):
    original=store.workflows._start
    def start(kind,*a,**kw):
        if kind=='flow':raise ResourcesUnavailable('SYNTHETIC resource gate before solver')
        child=original(kind,*a,**kw)
        store.workflows.results[child['job_id']]['implementation_sha256']={n:sha256(ROOT/n) for n in IMPLEMENTATION}
        return child
    monkeypatch.setattr(store.workflows,'_start',start)
    task=finished(store,request());monkeypatch.setattr(store.workflows,'_start',original)
    return task


def test_resume_starts_flow_without_a_second_mesh_and_preserves_original_failure(store,monkeypatch):
    old=blocked(store,monkeypatch);before=(store.folders[old['job_id']]/'task.json').read_bytes()
    task=store.start_from_mesh(old['job_id']);end=time.monotonic()+8
    while store.workflows.reservation and time.monotonic()<end:time.sleep(.01)
    task=store.status(task['job_id']);assert task['status']=='completed'
    assert [kind for kind,_ in store.workflows.calls]==['mesh','flow']
    assert task['cases'][0]['mesh_id']==old['cases'][0]['mesh_id']
    assert task['cases'][0]['mesh_reused_from_job']==old['job_id']
    assert (store.folders[old['job_id']]/'task.json').read_bytes()==before


@pytest.mark.parametrize('fault',['hash','implementation','started_solver','uncertain_failure'])
def test_resume_rejects_changed_or_already_started_inputs(store,monkeypatch,fault):
    old=blocked(store,monkeypatch);row=old['cases'][0]
    if fault=='hash':(store.workflows.folders[row['mesh_id']]/'flow.msh').write_text('CHANGED')
    elif fault=='implementation':store.workflows.results[row['mesh_id']]['implementation_sha256']={}
    elif fault=='started_solver':
        raw=store.workflows.jobs[old['job_id']];raw['cases'][0]['flow_job_id']='a'*32
    else:
        raw=store.workflows.jobs[old['job_id']];raw['cases'][0].pop('resource_gate_stopped_before_solver',None);raw['cases'][0]['error']='unknown failure'
    before=len(store.folders)
    with pytest.raises(ValueError):store.start_from_mesh(old['job_id'])
    assert len(store.folders)==before and [k for k,_ in store.workflows.calls]==['mesh']


def test_resume_endpoint_rejects_foreign_origins_and_parameter_overrides(server):
    headers={'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json'}
    assert call(server,'POST','/api/pipeline-from-mesh',b'{"job_id":"unknown","flow":{}}',headers)[0]==400
    headers['Origin']='https://elsewhere.example'
    assert call(server,'POST','/api/pipeline-from-mesh',b'{"job_id":"unknown"}',headers)[0]==403
    assert not server.pipelines.folders


@pytest.mark.parametrize('tamper',[False,True])
def test_mesh_restore_after_server_restart_validates_lineage_before_running(store,monkeypatch,tamper):
    old=blocked(store,monkeypatch);row=old['cases'][0];mid=row['mesh_id'];mesh=store.workflows.results[mid]
    domain,source=store.workflows.domain(row['domain_id'],row['scheme_id'])
    config=dict(old['config']['mesh'],domain_id=row['domain_id'],scheme_id=row['scheme_id'])
    version={n:sha256(ROOT/n) for n in IMPLEMENTATION}
    mesh.update(config=config,engine='mesh-flow-1',source_sha256=old['model_reference']['source_id'],domain_artifact_sha256=domain['artifact_sha256'])
    folder=store.workflows.folders[mid];save(folder/'report.json',mesh)
    save(folder/'job.json',dict(kind='mesh',config=config,domain=domain,engine='mesh-flow-1',implementation_sha256=version))
    if tamper:
        mesh['config']['size_factor']=1.75;save(folder/'report.json',mesh)
    store.workflows.results.clear();store.workflows.folders.clear();store.domains.domains.clear();store.domains.directories.clear()
    if tamper:
        with pytest.raises(ValueError,match='配置或程序版本'):store.start_from_mesh(old['job_id'])
        assert not store.workflows.results
    else:
        new=store.start_from_mesh(old['job_id']);end=time.monotonic()+8
        while store.workflows.reservation and time.monotonic()<end:time.sleep(.01)
        assert store.status(new['job_id'])['status']=='completed'
        assert [kind for kind,_ in store.workflows.calls]==['mesh','flow']
