"""Windows publication contention and useful, bounded failure diagnostics."""
import json
import threading
from pathlib import Path
import pytest

from nozzle_platform.workflows import save, WorkflowStore
from tests.test_platform_domains import stores, config


def locked():
    error=PermissionError('Windows reader holds destination')
    error.winerror=5
    return error


def test_atomic_save_retries_short_windows_sharing_violation(tmp_path,monkeypatch):
    real=Path.replace;calls=[]
    def replace(self,target):
        calls.append(self)
        if len(calls)<3:raise locked()
        return real(self,target)
    monkeypatch.setattr(Path,'replace',replace)
    monkeypatch.setattr('nozzle_platform.workflows.time.sleep',lambda _:None)
    path=tmp_path/'progress.json';path.write_text('{"old":true}')
    save(path,{'message':'新进度'})
    assert len(calls)==3 and json.loads(path.read_text(encoding='utf-8'))=={'message':'新进度'}
    assert not path.with_suffix('.tmp').exists()


def test_persistent_permission_error_still_fails_with_old_record_intact(tmp_path,monkeypatch):
    attempts=[]
    def replace(self,target):attempts.append(1);raise locked()
    monkeypatch.setattr(Path,'replace',replace)
    monkeypatch.setattr('nozzle_platform.workflows.time.sleep',lambda _:None)
    path=tmp_path/'task.json';path.write_text('{"old":true}')
    with pytest.raises(PermissionError):save(path,{'new':True})
    assert len(attempts)==8 and json.loads(path.read_text())=={'old':True}


def test_other_permission_errors_are_not_retried(tmp_path,monkeypatch):
    calls=[]
    def replace(self,target):calls.append(1);raise PermissionError('ACL denied')
    monkeypatch.setattr(Path,'replace',replace)
    with pytest.raises(PermissionError):save(tmp_path/'task.json',{})
    assert len(calls)==1


def test_failed_worker_keeps_only_whitelisted_diagnostics(stores,tmp_path,monkeypatch):
    _,_,scheme,domains=stores
    domains.create(config(scheme))
    workflows=WorkflowStore(tmp_path/'jobs',domains)
    folder=tmp_path/'failed';folder.mkdir()
    save(folder/'result.json',dict(ok=False,error='质量未通过',error_code='mesh_quality_rejected'))
    save(folder/'mesh_quality.json',dict(mesh_quality_accepted=False,minimum_fluent_orthogonal_quality=.07,
        maximum_fluent_aspect_ratio=80,mesh_quality_limits={},secret='never expose'))
    save(folder/'partial.json',dict(iterations_completed=25,convergence_assessment={'numerical_criteria_met':False},secret='never expose'))
    workflows.jobs['job']={'status':'queued','result':None};workflows.active='job'
    class Process:
        _handle=0
        returncode=1
        def poll(self):return 1
        def wait(self,**kw):return 1
    class Job:
        def assign(self,p):pass
        def close(self):pass
    monkeypatch.setattr('nozzle_platform.workflows.WindowsJob',Job)
    monkeypatch.setattr('nozzle_platform.workflows.subprocess.Popen',lambda *a,**kw:Process())
    workflows._run('job',folder,{'kind':'flow'},threading.Event())
    task=workflows.status('job')
    assert task['status']=='failed' and task['error_code']=='mesh_quality_rejected'
    assert task['diagnostics']['iterations_completed']==25
    assert task['diagnostics']['maximum_fluent_aspect_ratio']==80
    assert 'secret' not in task['diagnostics']
    assert task['result'] is None and not workflows.results and workflows.active is None
