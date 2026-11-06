"""Read-only M23 transport/flux audit; never initializes or runs Fluent."""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import argparse,json,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import sha256,save
from nozzle_platform.coldflow_qualification import transport_review,assess_report,continuation_decision


def main():
    import psutil
    from nozzle_platform.nozzle_gpu_probe import read_saved
    from nozzle_platform.restart_check import compare
    from nozzle_platform.field_health import inspect,require_healthy
    from coldflow_trial.solver import saved_iteration,parse_residual_history,mesh_fingerprint
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--native',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    source=a.source.resolve();native=a.native.resolve();out=a.output.resolve()
    if out.exists():raise ValueError('Use a new audit directory')
    if any((x.info['name'] or '').lower() in ['fluent.exe','fl2520.exe','cx2520.exe','cortex.exe'] or (x.info['name'] or '').lower().startswith('fl_mpi') for x in psutil.process_iter(['name'])):raise ValueError('Wait for native processes to exit')
    report=json.loads((source/'report.json').read_text(encoding='utf-8'))
    inputs={n:sha256(source/n) for n in ['final.cas.h5','final.dat.h5','configured.json']}
    if any(report['artifact_sha256'][n]!=h for n,h in inputs.items()):raise ValueError('Source hashes changed')
    current=read_saved(source/'final.cas.h5',source/'final.dat.h5')
    reference=json.loads((source/'configured.json').read_text(encoding='utf-8'))
    if reference['models']['species']['options']['inlet_diffusion'] is not False:raise ValueError('Inlet diffusion sentinel is not applicable')
    model=transport_review(reference,current['y']);del current
    spec=json.loads((native/'spec.json').read_text(encoding='utf-8'))
    if any(sha256(Path(item['path']))!=item['sha256'] for item in spec['inputs'].values()):raise ValueError('Native input changed')
    if any(sha256(native/'source_snapshot'/n)!=h for n,h in spec['implementation_sha256'].items()):raise ValueError('Frozen source snapshot changed')
    outcome=json.loads((native/'result.json').read_text(encoding='utf-8'))
    cleanup=json.loads((native/'cleanup.json').read_text(encoding='utf-8'))
    if not cleanup['all_tracked_processes_exited']:raise ValueError('Native cleanup unverified')
    text=(native/'fluent.trn').read_text(encoding='utf-8',errors='replace') if (native/'fluent.trn').exists() else ''
    first=saved_iteration(source/'final.dat.h5');added=None;healthy=False;reports={};readcheck=None
    if (native/'final.dat.h5').exists():
        added=saved_iteration(native/'final.dat.h5')-first
        if added!=1 or spec['maximum_added_gpu_iterations']!=1:raise ValueError('Native budget mismatch')
        parse_residual_history(text,first,first+1)
        if mesh_fingerprint(native/'final.cas.h5')!=mesh_fingerprint(source/'final.cas.h5'):raise ValueError('Mesh changed')
        require_healthy(inspect(native))
        before=read_saved(native/'input.cas.h5',native/'readback.dat.h5')['summary']
        after=read_saved(native/'final.cas.h5',native/'final.dat.h5')['summary'];healthy=True
        for label,values in [('read_legacy',before),('read_explicit',before),('fresh_legacy',after),('fresh_explicit',after)]:
            reports[label]=assess_report(text,label,values,inlet_diffusion_enabled=False)
        readcheck=compare(native/'input.cas.h5',native/'input.dat.h5',native/'readback.dat.h5')
        if not readcheck['field_preservation_passed']:raise ValueError('Initial read not preserved')
    else:
        stage=json.loads((native/'stage.json').read_text()) if (native/'stage.json').exists() else {}
        # Only a proven pre-iteration stage can be audited as zero. Unknown
        # execution is retained as unknown, never refunded or called zero.
        if stage.get('stage')=='before_launch':added=0
    if any(sha256(source/n)!=h for n,h in inputs.items()):raise ValueError('Source changed during audit')
    consistent=bool(reports) and all(reports[n]['assessment']['report_consistent_with_saved_boundary'] for n in ['fresh_legacy','fresh_explicit'])
    audit=dict(status='coldflow_qualification_independently_audited',source=str(source),source_sha256=inputs,
        source_initial_iteration=first,native_protocol_status=outcome['status'],native_protocol_error=outcome.get('error'),actual_added_gpu_iterations=added,cpu_solver_iterations=0,
        saved_fields_healthy=healthy,transport_review=model,reports=reports,initial_read_preservation=readcheck,
        decision=continuation_decision(saved_fields_healthy=healthy,transport_qualified=False,fresh_reports_consistent=consistent,independent_diffusion_verified=False),
        native_sources_unchanged=True,no_native_processes_remaining=True,formal_physics_validated=False,scientific_eligible=False,
        numerical_criteria_met=False,grid_independence_verified=False,artifacts_sha256={},
        audit_implementation_sha256={n:sha256(ROOT/n) for n in ['scripts/review_coldflow_model.py','nozzle_platform/coldflow_qualification.py']})
    out.mkdir(parents=True)
    (out/'native_reports.txt').write_text(text or 'Native launch failed before a report was available.\n',encoding='utf-8')
    audit['artifacts_sha256']['native_reports.txt']=sha256(out/'native_reports.txt')
    save(out/'audit.json',audit)
    print(json.dumps(dict(audit=str(out/'audit.json'),native_protocol_status=outcome['status'],actual_added_gpu_iterations=added,
        transport_differences=model['differences_on_saved_composition'],fresh_reports_consistent=consistent,decision=audit['decision']),ensure_ascii=False))


if __name__=='__main__':main()
