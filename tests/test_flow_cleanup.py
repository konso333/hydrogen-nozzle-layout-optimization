"""SDK exit exceptions never certify cleanup or mask an original field rejection."""
from types import SimpleNamespace
import pytest
from nozzle_platform.flow_solver import close_flow_session


@pytest.mark.parametrize('exited',[True,False])
def test_failed_sdk_exit_requires_independent_process_cleanup(tmp_path,monkeypatch,exited):
    from nozzle_platform import layered_mesh
    def fail(**kwargs):raise RuntimeError('SDK cleanup batch failed')
    session=SimpleNamespace(settings=SimpleNamespace(file=SimpleNamespace(stop_transcript=lambda:None)),exit=fail)
    tracker=SimpleNamespace(capture=lambda session:None,owned={})
    monkeypatch.setattr('coldflow_trial.processes.cleanup_records',lambda records:dict(all_tracked_processes_exited=exited))
    removed=[];monkeypatch.setattr(layered_mesh,'cleanup_native_mapping',lambda folder:removed.append(folder))
    if exited:
        result=close_flow_session(session,tracker,tmp_path)
        assert result['session_exit_warning']=='SDK cleanup batch failed' and removed==[tmp_path]
    else:
        with pytest.raises(ValueError,match='进程退出尚未核实'):close_flow_session(session,tracker,tmp_path)
        assert not removed


def test_original_saved_field_error_survives_sdk_and_transcript_errors(tmp_path,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('server already disconnected')
    session=SimpleNamespace(settings=SimpleNamespace(file=SimpleNamespace(stop_transcript=fail)),exit=fail)
    tracker=SimpleNamespace(capture=lambda session:None,owned={})
    monkeypatch.setattr('coldflow_trial.processes.cleanup_records',lambda records:dict(all_tracked_processes_exited=True))
    monkeypatch.setattr('nozzle_platform.layered_mesh.cleanup_native_mapping',lambda folder:None)
    with pytest.raises(ValueError,match='saved species rejected'):
        try:raise ValueError('saved species rejected')
        finally:close_flow_session(session,tracker,tmp_path)
