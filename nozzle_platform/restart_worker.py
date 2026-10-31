"""Server-generated, bounded GPU file reader. No initialization, SVAR or iteration."""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).absolute().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256


def main(folder):
    from nozzle_platform.solver_device import launch
    from nozzle_platform.species_controls import saved,verify_live
    from nozzle_platform.restart_check import compare
    from nozzle_platform.flow_solver import close_flow_session
    from coldflow_trial.processes import OwnedProcesses
    from coldflow_trial.solver import snapshot
    spec=json.loads((folder/'restart_job.json').read_text(encoding='utf-8'));tracker=OwnedProcesses(spec['fluent']['executable'],folder)
    session=None;report=dict(native_read_completed=False,field_preservation_passed=False,added_iterations=0,
        formal_physics_validated=False,numerical_criteria_met=False,grid_independence_verified=False)
    try:
        if not all(sha256(ROOT/n)==h for n,h in spec['implementation_sha256'].items()):raise ValueError('重读实现已改变。')
        for kind,name in [('case','input.cas.h5'),('data','input.dat.h5')]:
            source=Path(spec[kind]);assert sha256(source)==spec['input_sha256'][kind];shutil.copyfile(source,folder/name)
        controls=saved(folder/'input.cas.h5',spec['config'])
        session=launch(spec,folder);tracker.capture(session);session.settings.file.start_transcript(file_name='fluent.trn')
        session.settings.file.read_case_data(file_name='input.cas.h5')
        before=snapshot(session);verify_live(session,controls)
        if before!=spec['reference_settings']:raise ValueError('重读后的物理或数值设置与原始配置不一致。')
        session.settings.file.write_data(file_name='reopened.dat.h5')
        if snapshot(session)!=before:raise ValueError('重读保存期间设置改变。')
        report=compare(folder/'input.cas.h5',folder/'input.dat.h5',folder/'reopened.dat.h5')
        report['actual_controls']=controls
        report['configuration_preserved']=True
    except BaseException as error:report.update(native_read_completed=False,field_preservation_passed=False,error=str(error) or type(error).__name__)
    finally:
        try:report['cleanup']=close_flow_session(session,tracker,folder)
        except Exception as error:report.update(native_read_completed=False,field_preservation_passed=False,cleanup_error=str(error))
        save(folder/'restart_check.json',report)
    return 0 if report['native_read_completed'] else 1


if __name__=='__main__':raise SystemExit(main(Path(sys.argv[1])))
