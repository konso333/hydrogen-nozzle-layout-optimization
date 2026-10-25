"""Independent M18 species-report checks and bounded GPU-chain assessment."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'): os.environ[key]='1'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256
from nozzle_platform.species_reports import parse_species_report,assess_species_report,report_block,field_preservation
from nozzle_platform.convergence_evidence import decide
from nozzle_platform.nozzle_gpu_probe import read_saved,saved_solver_controls,iteration_plan,compare_compositions
from nozzle_platform.native_reports import total_pressure
from nozzle_platform.transport_audit import components
from nozzle_platform.convergence import VERIFICATION_CRITERIA,checks
from coldflow_trial.solver import saved_iteration,mesh_fingerprint,parse_residual_history


def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def verify_native(folder,steps):
    spec=load(folder/'spec.json');result=load(folder/'result.json')
    if result['status']!='completed_pending_independent_property_audit': raise ValueError('Native run did not complete')
    if load(folder/'controller_outcome.json')['status']!='completed': raise ValueError('Controller did not complete')
    if not load(folder/'cleanup.json')['all_tracked_processes_exited']: raise ValueError('Native process still active')
    if spec['added_iterations']!=steps or not spec['restart'] or not spec['capture_reports'] or not spec['capture_startup']:
        raise ValueError('Wrong native experiment')
    if sha256(folder/'controller_source.py')!=spec['controller_sha256']: raise ValueError('Controller changed')
    for n,d in spec['implementation_sha256'].items():
        if sha256(folder/'source_snapshot'/n)!=d: raise ValueError('Frozen implementation changed: '+n)
    for n,d in result['files_sha256'].items():
        if sha256(folder/n)!=d: raise ValueError('Native artifact changed: '+n)
    for n,row in spec['inputs'].items():
        if sha256(folder/n)!=row['sha256'] or sha256(Path(row['path']))!=row['sha256']:
            raise ValueError('Native input changed: '+n)
    if mesh_fingerprint(folder/'final.cas.h5')!=spec['mesh_fingerprint']: raise ValueError('Mesh changed')
    controls=saved_solver_controls(folder/'final.cas.h5')
    for key,value in [('species_scheme',1),('species_relax',.1),('species_amg_alpha',1e-6),
                      ('species_iter_tolerance',1e-6),('species_max_iter',100)]:
        if controls.get(key)!=[value]*3: raise ValueError('Saved control changed: '+key)
    if controls['linear_absolute_tolerance_double']!=1e-20: raise ValueError('Saved absolute tolerance changed')
    import h5py
    with h5py.File(folder/'final.cas.h5') as f: rp=f['settings/Rampant Variables'][0].decode()
    if re.findall(r'\(species/inlet-diffusion\?\s+(#t|#f)\)',rp)!=['#f']:
        raise ValueError('Actual saved inlet diffusion not disabled')
    argv=load(folder/'command.json')['argv']
    if any(x not in argv for x in ['3ddp','-t1','-gpu=0']): raise ValueError('Not a single-GPU double-precision run')
    journal=(folder/'probe.jou').read_text(encoding='ascii')
    if journal.index('/report/surface-integrals')<journal.index('/file/write-case final.cas.h5'):
        raise ValueError('Reports interrupted calculation trajectory')
    if 'gpuapp-add-udf' in journal: raise ValueError('Restart must preserve existing UDF hooks')
    text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
    if any(s not in text for s in ['Preparing GPU solver','NVIDIA','GPU_NUMBA_ABI_FIX','udf_ideal_mu','udf_ideal_k']):
        raise ValueError('Missing GPU/transport execution evidence')
    start=saved_iteration(folder/'input.dat.h5');end=saved_iteration(folder/'final.dat.h5')
    if end-start!=steps: raise ValueError('Incorrect added GPU iteration count')
    residuals=parse_residual_history(text,start,end)
    if len(residuals)!=steps: raise ValueError('Incomplete residual history')
    checkpoints={};offset=0
    for count,name in iteration_plan(True,steps):
        offset+=count
        if saved_iteration(folder/(name+'.dat.h5'))!=start+offset: raise ValueError('Checkpoint iteration mismatch')
        checkpoints[name]=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'))['summary']
    return spec,result,controls,residuals,checkpoints


def candidate_rows(folder,job):
    from nozzle_platform import field_profiles,outlet_sampling
    from scripts.audit_gpu_continuation import profile_change
    spec,result,controls,residuals,checkpoints=verify_native(folder,100)
    inside=field_profiles.prepare(folder/'final.cas.h5',job['domain'])
    outside=outlet_sampling.prepare(folder/'final.cas.h5',job['domain'])
    def profiles(path):return dict(interior=field_profiles.sample(path,inside),outlet=outlet_sampling.sample(path,outside))
    old_profiles=profiles(folder/'input.dat.h5');old_pressure=total_pressure(folder,'input')
    material=components(load(folder/'reference.json'));start=saved_iteration(folder/'input.dat.h5')
    viscosity={}
    for n,prev in [('first','input'),('second','first'),('third','second')]:
        current=read_saved(folder/'final.cas.h5',folder/(n+'.dat.h5'))
        previous=read_saved(folder/'input.cas.h5' if prev=='input' else folder/'final.cas.h5',folder/(prev+'.dat.h5'))
        check=compare_compositions(current,previous,material)
        if not(check['current_composition_matches_ideal'] or check['previous_composition_matches_ideal']): raise ValueError('Startup viscosity mismatch')
        viscosity[n]=check;del current,previous
    rows=[]
    for step,prev,name in [(25,'step_24','step_25'),(50,'step_49','step_50'),(75,'step_74','step_75'),(100,'penultimate','final')]:
        current=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'))
        previous=read_saved(folder/'final.cas.h5',folder/(prev+'.dat.h5'))
        mu=compare_compositions(current,previous,material)
        if not(mu['current_composition_matches_ideal'] or mu['previous_composition_matches_ideal']): raise ValueError('Endpoint viscosity mismatch')
        prof=profiles(folder/(name+'.dat.h5'));pressure=total_pressure(folder,step)
        drop=pressure['area_weighted_total_pressure_drop_Pa'];old=old_pressure['area_weighted_total_pressure_drop_Pa']
        point=dict(iteration=start+step,block_steps=25,block_maximum_residuals={k:max(residuals[i][k] for i in range(start+step-24,start+step+1)) for k in VERIFICATION_CRITERIA['scaled_residuals']},
                   relative_mass_imbalance=current['summary']['relative_mass_imbalance'],h2_relative_convective_imbalance=current['summary']['h2_relative_convective_imbalance'],
                   relative_pressure_drop_change=max(abs(drop[k]-old[k])/max(abs(drop[k]),1.) for k in drop),
                   area_weighted_total_pressure_drop_Pa=drop,outlet_profile_relative_drift=profile_change(prof['outlet'],old_profiles['outlet']),
                   interior_profile_relative_drift=profile_change(prof['interior'],old_profiles['interior']))
        point['criteria_met']=all(c['passed'] for c in checks(point,VERIFICATION_CRITERIA))
        rows.append(dict(phase='m18',point=point,summary=current['summary'],viscosity=mu,common_profiles=prof))
        old_profiles=prof;old_pressure=pressure;del current,previous
    return rows,checkpoints,viscosity,controls


def audit_fresh(folder,base,native):
    spec=load(folder/'spec.json');result=load(folder/'result.json')
    if not spec.get('fresh_gpu_step') or spec['backend']!='gpu' or spec['added_iterations']!=1:
        raise ValueError('Not the explicit one-step GPU diagnostic')
    if result['status']!='completed' or result['added_iterations']!=1 or result['cpu_solver_iterations']!=0:
        raise ValueError('Fresh GPU probe did not complete with preserved fields')
    if not load(folder/'cleanup.json')['all_tracked_processes_exited']:raise ValueError('Fresh GPU process active')
    if sha256(folder/'controller_source.py')!=spec['controller_sha256']:raise ValueError('Fresh controller changed')
    for n,d in result['files_sha256'].items():
        if not (folder/n).exists() and n=='native_drive.json':continue
        if sha256(folder/n)!=d:raise ValueError('Fresh artifact changed: '+n)
    for n,row in spec['inputs'].items():
        source='final.cas.h5' if n.endswith('.cas.h5') else 'final.dat.h5'
        if row['sha256']!=native['files_sha256'][source] or sha256(folder/n)!=row['sha256']:
            raise ValueError('Fresh diagnostic source chain changed')
    argv=load(folder/'command.json')['argv']
    if any(n not in argv for n in ['3ddp','-t1','-gpu=0']):raise ValueError('Fresh diagnostic was not GPU double precision')
    journal=(folder/'probe.jou').read_text(encoding='ascii')
    if journal.count('/solve/iterate 1\n')!=1 or journal.count('/solve/')!=1:raise ValueError('One-step budget changed')
    if not journal.index('/solve/iterate')<journal.index('BEGIN_SPECIES_fresh')<journal.index('/file/write-data fresh'):
        raise ValueError('Fresh report was taken after a solution write')
    text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
    if any(n not in text for n in ['Preparing GPU solver','NVIDIA','GPU_NUMBA_ABI_FIX','udf_ideal_mu','udf_ideal_k']):
        raise ValueError('Fresh GPU execution evidence missing')
    start=saved_iteration(folder/'input.dat.h5');end=saved_iteration(folder/'fresh.dat.h5')
    if start!=2150 or end!=2151 or saved_iteration(folder/'after_report.dat.h5')!=end:
        raise ValueError('Fresh GPU step count mismatch')
    if len(parse_residual_history(text,start,end))!=1:raise ValueError('Missing actual one-step residual')
    case=folder/'after_report.cas.h5'
    if mesh_fingerprint(case)!=mesh_fingerprint(base/'final.cas.h5'):raise ValueError('Fresh mesh changed')
    controls=saved_solver_controls(case)
    if controls!=saved_solver_controls(base/'final.cas.h5'):raise ValueError('Fresh solver controls changed')
    import h5py
    with h5py.File(case) as f:rp=f['settings/Rampant Variables'][0].decode()
    if re.findall(r'\(species/inlet-diffusion\?\s+(#t|#f)\)',rp)!=['#f']:raise ValueError('Fresh inlet diffusion changed')
    first=read_saved(case,folder/'fresh.dat.h5');after=read_saved(case,folder/'after_report.dat.h5')
    preserved=field_preservation(first,after)
    if not preserved['verified']:raise ValueError('Fresh/report re-read changed physical fields')
    old=read_saved(base/'final.cas.h5',base/'final.dat.h5')
    mu=compare_compositions(first,old,components(load(base/'reference.json')))
    if not(mu['current_composition_matches_ideal'] or mu['previous_composition_matches_ideal']):raise ValueError('Fresh viscosity mismatch')
    reports={stage:assess_species_report(parse_species_report(report_block(text,stage)),
                                       old['summary'] if stage=='input' else first['summary'],inlet_diffusion_enabled=False)
             for stage in ['input','fresh','reloaded']}
    return dict(directory=str(folder),result_sha256=sha256(folder/'result.json'),actual_added_gpu_iterations=1,
                saved_iteration=end,saved_summary=first['summary'],field_preservation=preserved,
                viscosity_check=mu,reports=reports,full_species_flux_verified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['source-audit','source-manifest','domain-job','output']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--candidate',type=Path)
    p.add_argument('--readers',type=Path,nargs='*',default=[])
    p.add_argument('--fresh-probes',type=Path,nargs='*',default=[])
    a=p.parse_args();out=a.output.resolve()
    if out.exists(): raise ValueError('Preserve old audits; choose a new directory')
    source=load(a.source_audit);manifest=load(a.source_manifest)
    if sha256(a.source_audit)!=manifest['independent_audit_sha256']: raise ValueError('M17 audit differs from frozen review')
    if source['status']!='single_session_100_steps_independently_audited' or source['actual_native_added_iterations']!=100:
        raise ValueError('Wrong baseline')
    base=Path(source['candidate']);spec,native,controls,residuals,base_checkpoints=verify_native(base,100)
    if saved_iteration(base/'input.dat.h5')!=2050 or saved_iteration(base/'final.dat.h5')!=2150: raise ValueError('Wrong frozen interval')
    for n,d in source['artifacts_sha256'].items():
        if sha256(a.source_audit.parent/n)!=d: raise ValueError('Baseline artifact changed')
    rows=[dict(r,phase='m17_baseline') for r in source['rows']]
    if [r['point']['iteration'] for r in rows]!=[2075,2100,2125,2150]: raise ValueError('Incomplete baseline observations')
    raw=source['diagnostic_species_report_raw'];parsed=parse_species_report(raw)
    report_assessment=assess_species_report(parsed,rows[-1]['summary'],inlet_diffusion_enabled=False)
    readers=[]
    for reader in a.readers:
        r=load(reader/'result.json');s=load(reader/'spec.json')
        if s['added_iterations']!=0 or s['cpu_solver_iterations']!=0 or r['added_iterations']!=0:
            raise ValueError('Reader was not zero-iteration')
        origin=s.get('data_origin','saved-gpu')
        expected=native['files_sha256']['final.dat.h5']
        if origin=='original-cpu':
            cpu=a.domain_job.parent;cpu_report=load(cpu/'report.json');expected=cpu_report['artifact_sha256']['final.dat.h5']
            if sha256(cpu/'final.dat.h5')!=expected: raise ValueError('Original CPU control data changed')
        elif origin!='saved-gpu':raise ValueError('Unknown reader data origin')
        if s['inputs']['input.dat.h5']['sha256']!=expected: raise ValueError('Reader used different saved data')
        if not load(reader/'cleanup.json')['all_tracked_processes_exited']: raise ValueError('Reader process active')
        for n,d in r['files_sha256'].items():
            if not (reader/n).exists() and n=='native_drive.json':continue
            if sha256(reader/n)!=d: raise ValueError('Reader artifact changed: '+n)
        launcher=(reader/'launcher.log').read_text(encoding='utf-8',errors='replace')
        readers.append(dict(directory=str(reader),data_origin=origin,result_sha256=sha256(reader/'result.json'),result=r,
                            license_rejected='Unexpected license problem' in launcher))
    if len(a.fresh_probes)>1:raise ValueError('Only one one-step fresh diagnostic is authorized')
    fresh=[audit_fresh(folder,base,native) for folder in a.fresh_probes]
    if fresh and a.candidate:raise ValueError('One diagnostic plus 100 continuation steps would exceed M18 budget')
    new_rows=[];new_checkpoints={};startup={};new_controls=None
    if a.candidate:
        folder=a.candidate.resolve();candidate_spec=load(folder/'spec.json')
        for n,src in [('input.cas.h5','final.cas.h5'),('input.dat.h5','final.dat.h5')]:
            if candidate_spec['inputs'][n]['sha256']!=native['files_sha256'][src]: raise ValueError('Continuation chain changed')
        job=load(a.domain_job)
        if sha256(a.domain_job.parent/'configured.json')!=candidate_spec['inputs']['reference.json']['sha256']:
            raise ValueError('Wrong reference physics')
        for n,d in spec['restart_code_sha256'].items():
            if sha256(folder/n)!=d: raise ValueError('Original UDF changed')
        new_rows,new_checkpoints,startup,new_controls=candidate_rows(folder,job)
        if new_rows[0]['point']['iteration']!=2175: raise ValueError('Wrong continuation start')
        rows+=new_rows
    decision=decide([r['point'] for r in rows],full_species_flux_verified=False,
                    budget_exhausted=bool(a.candidate),execution_blocked=not bool(a.candidate))
    report=dict(status='bounded_gpu_convergence_independently_audited',baseline_audit_sha256=sha256(a.source_audit),
                baseline_manifest_sha256=sha256(a.source_manifest),baseline_directory=str(base),candidate=str(a.candidate.resolve()) if a.candidate else None,
                baseline_added_gpu_iterations=100,new_added_gpu_iterations=100 if a.candidate else len(fresh),
                new_saved_checkpoints_verified=len(new_checkpoints) or len(fresh),baseline_saved_checkpoints_verified=len(base_checkpoints),
                new_continuation_gpu_iterations=100 if a.candidate else 0,diagnostic_added_gpu_iterations=len(fresh),
                actual_total_chain_gpu_iterations=100+(100 if a.candidate else len(fresh)),fresh_gpu_probes=fresh,
                baseline_native_species_report=parsed,baseline_species_report_assessment=report_assessment,
                reader_probes=readers,rows=rows,new_checkpoint_summaries=new_checkpoints,
                startup_viscosity_checks=startup,saved_solver_controls=new_controls or controls,
                decision=decision,full_species_flux_verified=False,full_species_flux_kg_s=None,
                strict_convergence_verified=decision['strict_convergence_verified'],formal_physics_validated=False,
                scientific_eligible=False,grid_independence_verified=False,cpu_fallback=False,
                CPU_GPU_converged_nozzle_agreement=False,real_nozzle_conductivity_field_validated=False,
                formal_study_started=False,native_processes_exited=True,
                continuation_state='bounded_100_steps_completed' if a.candidate else 'not_started',
                stop_reason='full_species_flux_unverified' if fresh else 'preflight_or_flux_evidence_pending',
                source_sha256={n:sha256(ROOT/n) for n in ['scripts/audit_gpu_convergence.py','nozzle_platform/species_reports.py','nozzle_platform/convergence_evidence.py']})
    out.mkdir(parents=True)
    with (out/'convergence_trends.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['phase','saved_iteration','added_gpu_iterations','fuel_total_pressure_drop_Pa','air_total_pressure_drop_Pa','h2_convective_imbalance','outlet_temporal_drift','interior_temporal_drift','full_h2_flux'])
        for i,r in enumerate(rows):
            q=r['point'];d=q['area_weighted_total_pressure_drop_Pa']
            writer.writerow([r['phase'],q['iteration'],(i+1)*25,d['fuel_inlet'],d['oxidizer_inlet'],q['h2_relative_convective_imbalance'],q['outlet_profile_relative_drift'],q['interior_profile_relative_drift'],None])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(11,8),layout='constrained');x=[(i+1)*25 for i in range(len(rows))]
    for name in ['outlet','interior']:
        axes[0,0].semilogy(x,[r['point'][name+'_profile_relative_drift'] for r in rows],'.-',label=name)
    axes[0,0].axhline(1e-4,color='gray',ls=':');axes[0,0].set_ylabel('Common-profile temporal relative drift')
    axes[0,1].semilogy(x,[r['point']['h2_relative_convective_imbalance'] for r in rows],'.-',label='H2 convection ONLY')
    axes[0,1].axhline(.001,color='gray',ls=':');axes[0,1].set_ylabel('Relative H2 convective imbalance')
    for role in ['fuel_inlet','oxidizer_inlet']:
        axes[1,0].plot(x,[r['point']['area_weighted_total_pressure_drop_Pa'][role] for r in rows],'.-',label=role)
    axes[1,0].set_ylabel('Native total pressure drop [Pa]')
    for name,limit in VERIFICATION_CRITERIA['scaled_residuals'].items():
        # Plotting floor only; raw residuals and acceptance checks stay intact.
        axes[1,1].semilogy(x,[max(r['point']['block_maximum_residuals'][name]/limit,1e-3) for r in rows],'.-',label=name)
    axes[1,1].axhline(1,color='gray',ls=':');axes[1,1].set_ylabel('Block max residual / threshold (plot floor 1e-3)')
    for ax in axes.flat:
        ax.set_xlabel('Verified GPU steps since saved iteration 2050');ax.legend(fontsize=7);ax.grid(alpha=.2)
        if new_rows:ax.axvline(100,color='black',ls='--',alpha=.4)
    fig.suptitle('M17 baseline + bounded M18 continuation' if new_rows else
                 'M17 baseline trends only; M18 added one report-diagnostic step' if fresh else
                 'M17 baseline only: M18 has added ZERO solver steps')
    fig.savefig(out/'convergence_trends.png',dpi=150);plt.close(fig)
    raw_text='M17 preserved legacy diagnostic (REJECTED):\n'+raw+'\n'
    for reader in readers:
        path=Path(reader['directory']);raw_text+='\nReader '+str(path)+'\n'
        log=path/('journal.trn' if (path/'journal.trn').is_file() else 'launcher.log')
        raw_text+=log.read_text(encoding='utf-8',errors='replace')+'\n'
    for probe in fresh:
        path=Path(probe['directory']);raw_text+='\nFresh one-step GPU diagnostic '+str(path)+'\n'
        raw_text+=(path/'journal.trn').read_text(encoding='utf-8',errors='replace')+'\n'
    (out/'native_reports.txt').write_text(raw_text,encoding='utf-8')
    report['artifacts_sha256']={p.name:sha256(p) for p in out.iterdir() if p.is_file()}
    save(out/'audit.json',report);print(decision['status'],out)


if __name__=='__main__': main()
