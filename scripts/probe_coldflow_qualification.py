"""Bounded M23 diagnostic: one GPU step and explicit-domain species reports.

This does not enable production GPU solving, change physics, initialize the
saved solution, or consume the budgets of older milestones.
"""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import argparse,json,shutil,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256,IMPLEMENTATION


def worker(folder):
    from nozzle_platform.solver_device import launch
    from nozzle_platform.flow_solver import close_flow_session
    from nozzle_platform.species_controls import saved,verify_live
    from nozzle_platform.coldflow_qualification import report_commands,assess_report,continuation_decision,report_domain
    from nozzle_platform.nozzle_gpu_probe import read_saved
    from nozzle_platform.restart_check import compare
    from nozzle_platform.field_health import inspect,require_healthy
    from coldflow_trial.solver import snapshot,saved_iteration,parse_residual_history
    from coldflow_trial.processes import OwnedProcesses
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'))
    result=dict(status='starting',added_gpu_iterations=None,cpu_solver_iterations=0,
        scientific_eligible=False,formal_physics_validated=False,numerical_criteria_met=False,
        grid_independence_verified=False,cpu_fallback=False)
    session=None;tracker=OwnedProcesses(spec['payload']['fluent']['executable'],folder)
    try:
        if any(sha256(ROOT/n)!=h for n,h in spec['implementation_sha256'].items()):raise ValueError('Implementation changed')
        for name,item in spec['inputs'].items():
            source=Path(item['path'])
            if sha256(source)!=item['sha256']:raise ValueError('Source changed: '+name)
            shutil.copyfile(source,folder/name)
        first=saved_iteration(folder/'input.dat.h5');controls=saved(folder/'input.cas.h5',spec['payload']['config'])
        reference=json.loads((folder/'reference.json').read_text(encoding='utf-8'))
        if reference['models']['species']['options']['inlet_diffusion'] is not False:raise ValueError('Sentinel requires inlet diffusion disabled')
        save(folder/'stage.json',dict(stage='before_launch',added_gpu_iterations=0))
        session=launch(spec['payload'],folder);tracker.capture(session)
        session.settings.file.start_transcript(file_name='fluent.trn')
        session.settings.file.read_case_data(file_name='input.cas.h5')
        if snapshot(session)!=reference:raise ValueError('Reading changed original physical/numerical settings')
        verify_live(session,controls)
        command=session.settings.results.report.flow.species_mass_flow
        domains=list(command.domain.allowed_values())
        selected=report_domain(domains,(folder/'fluent.trn').read_text(encoding='utf-8',errors='replace'))
        result['report_domains']=domains;result['explicit_report_domain']=selected
        def report(label,explicit):
            begin,end=report_commands(label)
            session.scheme_eval.eval(begin)
            if explicit:command(domain=selected)
            else:session.tui.report.species_mass_flow()
            session.scheme_eval.eval(end)
        report('read_legacy',False);report('read_explicit',True)
        session.settings.file.write_data(file_name='readback.dat.h5')
        restored=compare(folder/'input.cas.h5',folder/'input.dat.h5',folder/'readback.dat.h5')
        result['initial_read_preservation']=restored
        if not restored['field_preservation_passed']:raise ValueError('Input was not strictly preserved on read')
        # Exactly one explicit new iteration, never initialization or a batch.
        save(folder/'stage.json',dict(stage='before_only_iteration',initial_iteration=first,maximum_added_gpu_iterations=1))
        session.settings.solution.run_calculation.iterate(iter_count=1)
        save(folder/'stage.json',dict(stage='iteration_returned',added_gpu_iterations=1))
        report('fresh_legacy',False);report('fresh_explicit',True)
        session.settings.file.write_data(file_name='final.dat.h5')
        if snapshot(session)!=reference:raise ValueError('Settings changed during diagnostic')
        verify_live(session,controls)
        shutil.copyfile(folder/'input.cas.h5',folder/'final.cas.h5')
        session.settings.file.stop_transcript()
        if saved_iteration(folder/'final.dat.h5')!=first+1:raise ValueError('Actual added iteration is not one')
        transcript=(folder/'fluent.trn').read_text(encoding='utf-8',errors='replace')
        rows=parse_residual_history(transcript,first,first+1)
        result['actual_residual_iterations']=list(rows);result['added_gpu_iterations']=1
        health=inspect(folder);require_healthy(health);save(folder/'field_health.json',health)
        before=read_saved(folder/'input.cas.h5',folder/'readback.dat.h5')['summary']
        after=read_saved(folder/'final.cas.h5',folder/'final.dat.h5')['summary']
        reports={}
        for label,values in [('read_legacy',before),('read_explicit',before),('fresh_legacy',after),('fresh_explicit',after)]:
            reports[label]=assess_report(transcript,label,values,inlet_diffusion_enabled=False)
        consistent=all(reports[n]['assessment']['report_consistent_with_saved_boundary'] for n in ['fresh_legacy','fresh_explicit'])
        result.update(status='completed_evidence_pending',initial_iteration=first,final_iteration=first+1,
            field_health=health,saved_before=before,saved_after=after,reports=reports,
            decision=continuation_decision(saved_fields_healthy=True,transport_qualified=False,
                fresh_reports_consistent=consistent,independent_diffusion_verified=False))
    except BaseException as error:
        result.update(status='failed',error=str(error) or type(error).__name__)
        import traceback;traceback.print_exc()
    finally:
        try:result['cleanup']=close_flow_session(session,tracker,folder)
        except Exception as error:result.update(status='failed',cleanup_error=str(error))
        for name,item in spec['inputs'].items():
            if sha256(Path(item['path']))!=item['sha256']:result.update(status='failed',source_changed=name)
        save(folder/'result.json',result)
    return 0 if result['status']=='completed_evidence_pending' else 1


def main():
    import psutil,subprocess
    from nozzle_platform.bounded import run
    from nozzle_platform.layered_mesh import cleanup_native_mapping
    from coldflow_trial.processes import cleanup_records
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--budget',type=Path,required=True,help='New M23 ledger, not an older milestone budget')
    a=parser.parse_args();source=a.source.resolve();folder=a.output.resolve();budget=a.budget.resolve()
    if folder.exists():raise ValueError('Choose a new output directory; retain all failures')
    if not budget.is_relative_to(ROOT/'.local') or not budget.name.startswith('m23_'):raise ValueError('Explicit new M23 local budget required')
    if psutil.virtual_memory().available<4*2**30:raise ValueError('4 GiB available RAM required; no native started')
    if shutil.disk_usage(folder.anchor).free<5*2**30:raise ValueError('5 GiB free output disk required')
    if any((p.info['name'] or '').lower() in ['fluent.exe','cx2520.exe','fl2520.exe','cortex.exe'] or (p.info['name'] or '').lower().startswith('fl_mpi') for p in psutil.process_iter(['name'])):raise ValueError('Native solver already active')
    gpu=subprocess.run(['nvidia-smi','--id=0','--query-gpu=memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    if gpu.returncode or float(gpu.stdout.strip())<4096:raise ValueError('4 GiB free GPU VRAM required')
    payload=json.loads((source/'job.json').read_text(encoding='utf-8'))
    if payload['config'].get('species_solver_control')!='strict-absolute' or payload['config'].get('compute_device')!='gpu':raise ValueError('Explicit healthy M22 strict-absolute GPU source required')
    report=json.loads((source/'report.json').read_text(encoding='utf-8'))
    paths={'input.cas.h5':source/'final.cas.h5','input.dat.h5':source/'final.dat.h5','reference.json':source/'configured.json'}
    for name,path in paths.items():
        if report['artifact_sha256'][path.name]!=sha256(path):raise ValueError('Source artifact differs from its workflow report')
    ledger=json.loads(budget.read_text()) if budget.exists() else dict(milestone='M23',maximum_gpu_iterations=200,attempts=[],automatic_continuation_allowed=False)
    if ledger.get('milestone')!='M23' or sum(x['requested_gpu_iterations'] for x in ledger['attempts'])+1>ledger['maximum_gpu_iterations']:raise ValueError('M23 budget unavailable')
    # A third launch is permitted only after independently confirmed zero-step
    # license/metadata failures. It is not an extra compute experiment.
    if len(ledger['attempts'])>=3:raise ValueError('M23 report diagnostic attempt limit reached')
    if len(ledger['attempts'])==2 and not all(x.get('independently_confirmed_added_iterations')==0 for x in ledger['attempts']):
        raise ValueError('A corrected retry requires proven zero iterations in both prior attempts')
    names=tuple(dict.fromkeys(IMPLEMENTATION+('nozzle_platform/coldflow_qualification.py','nozzle_platform/bounded.py','scripts/probe_coldflow_qualification.py')))
    folder.mkdir(parents=True)
    spec=dict(payload=payload,maximum_added_gpu_iterations=1,cpu_solver_iterations=0,native_wall_limit_seconds=240,
        inputs={name:dict(path=str(path),sha256=sha256(path)) for name,path in paths.items()},
        implementation_sha256={n:sha256(ROOT/n) for n in names},source_report_sha256=sha256(source/'report.json'))
    for name in names:
        target=folder/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    save(folder/'spec.json',spec);ledger['attempts'].append(dict(mode='explicit_domain_fresh_report',folder=str(folder),requested_gpu_iterations=1,started_epoch=time.time()));save(budget,ledger)
    try:
        code=run([payload['fluent']['python'],'-X','utf8','-u',str(Path(__file__)), '--worker',str(folder)],cwd=ROOT,log=folder/'worker.log',timeout=240,cancel=threading.Event())
    finally:
        records=folder/'owned_processes.json'
        if records.exists():
            clean=cleanup_records(json.loads(records.read_text()));save(folder/'cleanup.json',clean)
            if not clean['all_tracked_processes_exited']:raise RuntimeError('Native cleanup not verified')
        if (folder/'native_drive.json').exists():cleanup_native_mapping(folder)
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    print(json.dumps({k:result.get(k) for k in ['status','error','added_gpu_iterations','report_domains','explicit_report_domain','decision']},ensure_ascii=False))
    return code


if __name__=='__main__':
    raise SystemExit(worker(Path(sys.argv[2])) if len(sys.argv)>1 and sys.argv[1]=='--worker' else main())
