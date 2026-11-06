"""Independently inspect M24 files and optionally install a read-only report."""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import sha256,save
from nozzle_platform.species_flux_benchmark import saved_state,saved_passed,parse_report,assess_report,classification,build_report,EXPECTED_RATE,iteration_count,physical_signature,state_differences,saved_diffusion_flags


def main():
    import psutil
    from coldflow_trial.solver import saved_iteration,mesh_fingerprint,parse_residual_history
    from nozzle_platform.review_reports import install_review
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu',type=Path,required=True);p.add_argument('--gpu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--install-root',type=Path);p.add_argument('--report-id',default='m24_review_20261104');a=p.parse_args()
    out=a.output.resolve()
    if out.exists():raise ValueError('Preserve old audit; choose a new output')
    if any((r.info['name'] or '').lower() in ['fluent.exe','cx2520.exe','fl2520.exe','cortex.exe'] or (r.info['name'] or '').lower().startswith('fl_mpi') for r in psutil.process_iter(['name'])):raise ValueError('Native solver still active')
    results={};raw=[]
    for backend,folder in [('cpu',a.cpu.resolve()),('gpu',a.gpu.resolve())]:
        spec=json.loads((folder/'spec.json').read_text());result=json.loads((folder/'result.json').read_text())
        early_protocol_failure=(result['status']=='failed' and result.get('error')=='Actual iterations differ from request')
        if (result['status']!='completed' and not early_protocol_failure) or spec['backend']!=backend:raise ValueError('Native protocol did not complete')
        if not json.loads((folder/'cleanup.json').read_text())['all_tracked_processes_exited']:raise ValueError('Native cleanup incomplete')
        if any(sha256(folder/'source_snapshot'/n)!=h for n,h in spec['implementation_sha256'].items()):raise ValueError('Frozen implementation changed')
        text=(folder/'fluent.trn').read_text(encoding='utf-8',errors='replace');raw.append(backend.upper()+'\n'+text)
        initial=saved_state(folder/'initial.cas.h5',folder/'initial.dat.h5');state=saved_state(folder/'final.cas.h5',folder/'final.dat.h5')
        if not initial['uniform_composition_passed'] or not saved_passed(state):raise ValueError('Analytic state mismatch')
        actual_count=iteration_count(folder/'final.dat.h5')
        if iteration_count(folder/'initial.dat.h5')!=0 or not 1<=actual_count<=5:raise ValueError('Unexpected iteration count')
        if actual_count<5 and f'!    {actual_count} solution is converged' not in text:raise ValueError('Early stop is not independently explained')
        parse_residual_history(text,0,actual_count)
        if mesh_fingerprint(folder/'initial.cas.h5')!=mesh_fingerprint(folder/'final.cas.h5'):raise ValueError('Mesh changed')
        actual=json.loads((folder/'actual_configured.json').read_text());reference=json.loads((folder/'configured.json').read_text())
        flags=saved_diffusion_flags(folder/'final.cas.h5')
        if any(flags.values()) or physical_signature(actual,flags)!=physical_signature(reference):raise ValueError('Effective physical settings changed')
        mix=actual['materials']['mixture']['hydrogen-air']
        if mix['density']['option']!='incompressible-ideal-gas':raise ValueError('Analytic ideal-gas density model not verified')
        h2=mix['species']['volumetric_species']['h2']['molecular_weight']
        if h2!={'option':'value','value':2.01594}:raise ValueError('Hydrogen molecular weight differs from analytic reference')
        if actual['models']['species']['reactions']['enable_volumetric_reactions'] is not False:raise ValueError('Unexpected reaction model')
        reports={}
        for label in ['fresh_legacy','fresh_explicit']+(['read_legacy','read_explicit'] if backend=='gpu' else []):
            parsed=parse_report(text,label);reports[label]=dict(raw=parsed,assessment=assess_report(parsed,EXPECTED_RATE))
        item=dict(actual_added_iterations=actual_count,requested_maximum_iterations=5,native_protocol_status=result['status'],
            native_protocol_error=result.get('error'),early_convergence=actual_count<5,saved_fields_passed=True,saved_state=state,reports=reports,
            snapshot_differences=state_differences(reference,actual),active_physics_preserved=True,
            fresh_report_passed=all(reports[n]['assessment']['passed'] for n in ['fresh_legacy','fresh_explicit']),
            compute_wall_seconds=result['compute_wall_seconds'],files_sha256={n:sha256(folder/n) for n in ['initial.cas.h5','initial.dat.h5','final.cas.h5','final.dat.h5','fluent.trn','spec.json','configured.json','actual_configured.json']})
        if backend=='cpu' and item['compute_wall_seconds']>20:raise ValueError('CPU computation exceeded budget')
        if backend=='gpu':
            for name in ['initial.cas.h5','initial.dat.h5','configured.json','mesh.json']:
                if sha256(a.cpu/name)!=sha256(folder/name) or sha256(folder/name)!=spec['input_sha256'][name]:raise ValueError('CPU/GPU did not use identical initial files')
        results[backend]=item
    audit=dict(status='pure_channel_cpu_gpu_independently_audited',backends=results,classification=classification(results['cpu'],results['gpu']),
        same_initial_files_verified=True,no_native_processes_remaining=True,formal_physics_validated=False,scientific_eligible=False,
        grid_independence_verified=False,full_mixed_nozzle_flux_verified=False)
    out.mkdir(parents=True);(out/'native_reports.txt').write_text('\n\n'.join(raw),encoding='utf-8');save(out/'audit.json',audit)
    if a.install_root:
        report,files=build_report(audit,out/'audit.json',a.report_id);install_review(a.install_root,report,files)
    print(json.dumps(dict(audit=str(out/'audit.json'),classification=audit['classification'],cpu_report_passed=results['cpu']['fresh_report_passed'],gpu_report_passed=results['gpu']['fresh_report_passed'],cpu_compute_seconds=results['cpu']['compute_wall_seconds']),ensure_ascii=False))


if __name__=='__main__':main()
