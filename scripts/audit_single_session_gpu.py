"""Independent M17 single-session audit and comparison with frozen M16 data."""
import argparse,json,os,re,sys
from pathlib import Path
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256
from nozzle_platform.nozzle_gpu_probe import read_saved,saved_solver_controls,compare_compositions,iteration_plan
from nozzle_platform.native_reports import total_pressure
from nozzle_platform.transport_audit import components
from nozzle_platform.gpu_stability import FLAGS,relative_drift,study_assessment
from nozzle_platform.convergence import VERIFICATION_CRITERIA,checks
from nozzle_platform import field_profiles,outlet_sampling
from scripts.audit_gpu_continuation import profile_change
from coldflow_trial.solver import saved_iteration,mesh_fingerprint,parse_residual_history


def main():
    import h5py,numpy as np,csv
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['candidate','m16-audit','m16-manifest','domain-job','output']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();folder=a.candidate.resolve();out=a.output.resolve()
    if out.exists():raise ValueError('Preserve previous audit; use a new output directory')
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'));result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    assert spec['added_iterations']==100 and spec['capture_reports'] and spec['capture_startup'] and spec['restart']
    assert result['status']=='completed_pending_independent_property_audit'
    assert json.loads((folder/'controller_outcome.json').read_text(encoding='utf-8'))['status']=='completed'
    assert json.loads((folder/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
    command=json.loads((folder/'command.json').read_text(encoding='utf-8'))['argv'];assert all(s in command for s in ['3ddp','-t1','-gpu=0'])
    assert sha256(folder/'controller_source.py')==spec['controller_sha256']
    for n,d in spec['implementation_sha256'].items():assert sha256(folder/'source_snapshot'/n)==d
    for n,r in spec['inputs'].items():assert sha256(Path(r['path']))==r['sha256']==sha256(folder/n)
    for n,d in result['files_sha256'].items():assert sha256(folder/n)==d
    for n,d in spec['restart_code_sha256'].items():assert sha256(folder/n)==d
    journal=(folder/'probe.jou').read_text(encoding='ascii');assert 'gpuapp-add-udf' not in journal
    assert journal.index('/report/surface-integrals')>journal.index('/file/write-case final.cas.h5')
    m16=json.loads(a.m16_audit.read_text(encoding='utf-8'));manifest=json.loads(a.m16_manifest.read_text(encoding='utf-8'))
    assert sha256(a.m16_audit)==manifest['continuation_audit_sha256']
    assert sha256(folder/'input.dat.h5')==m16['source_audit']['files_sha256']['final.dat.h5']
    assert sha256(folder/'input.cas.h5')==m16['source_audit']['files_sha256']['final.cas.h5']
    fingerprint=mesh_fingerprint(folder/'input.cas.h5');assert fingerprint==mesh_fingerprint(folder/'final.cas.h5')==spec['mesh_fingerprint']
    controls=saved_solver_controls(folder/'final.cas.h5')
    for k,v in [('species_relax',.1),('species_scheme',1),('species_amg_alpha',1e-6),('species_iter_tolerance',1e-6),('species_max_iter',100)]:assert controls[k]==[v]*3
    assert controls['linear_absolute_tolerance_double']==1e-20
    reference=json.loads((folder/'reference.json').read_text(encoding='utf-8'));material=components(reference)
    assert reference['models']['species']['options']['inlet_diffusion'] is False
    with h5py.File(folder/'final.cas.h5') as f:rp=f['settings/Rampant Variables'][0].decode()
    assert re.findall(r'\(species/inlet-diffusion\?\s+(#t|#f)\)',rp)==['#f']
    start=saved_iteration(folder/'input.dat.h5');end=saved_iteration(folder/'final.dat.h5');assert start==2050 and end==2150
    transcript=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
    assert all(s in transcript for s in ['Preparing GPU solver','NVIDIA','GPU_NUMBA_ABI_FIX','udf_ideal_mu','udf_ideal_k'])
    residuals=parse_residual_history(transcript,start,end);assert len(residuals)==100
    stages={};offset=0
    for count,name in iteration_plan(True,100):
        offset+=count;assert saved_iteration(folder/(name+'.dat.h5'))==start+offset
        stages[name]=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'))['summary']
    startup_mu={}
    for name,previous_name in [('first','input'),('second','first'),('third','second')]:
        current=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'))
        previous=read_saved(folder/'input.cas.h5' if previous_name=='input' else folder/'final.cas.h5',folder/(previous_name+'.dat.h5'))
        check=compare_compositions(current,previous,material)
        assert check['current_composition_matches_ideal'] or check['previous_composition_matches_ideal']
        startup_mu[name]=check;del current,previous
    job=json.loads(a.domain_job.read_text(encoding='utf-8'));assert sha256(a.domain_job.parent/'configured.json')==spec['inputs']['reference.json']['sha256']
    inside=field_profiles.prepare(folder/'final.cas.h5',job['domain']);outside=outlet_sampling.prepare(folder/'final.cas.h5',job['domain'])
    def profiles(path):return dict(interior=field_profiles.sample(path,inside),outlet=outlet_sampling.sample(path,outside))
    old_profiles=profiles(folder/'input.dat.h5');old_pressure=total_pressure(folder,'input');history=[];rows=[];pressure={'input':old_pressure}
    for index,(step,prev,name) in enumerate([(25,'step_24','step_25'),(50,'step_49','step_50'),(75,'step_74','step_75'),(100,'penultimate','final')]):
        current=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'));previous=read_saved(folder/'final.cas.h5',folder/(prev+'.dat.h5'))
        mu=compare_compositions(current,previous,material);assert mu['current_composition_matches_ideal'] or mu['previous_composition_matches_ideal']
        stored=m16['batches'][index];other=Path(stored['audit']['directory'])
        assert sha256(other/'final.dat.h5')==stored['audit']['files_sha256']['final.dat.h5']
        assert mesh_fingerprint(other/'final.cas.h5')==fingerprint
        restart=read_saved(other/'final.cas.h5',other/'final.dat.h5')
        comparison=dict(h2_relative_l2=relative_drift(current['y'][:,0],restart['y'][:,0]),
            velocity_relative_l2=relative_drift(np.column_stack([current['fields'][n] for n in ['SV_U','SV_V','SV_W']]),np.column_stack([restart['fields'][n] for n in ['SV_U','SV_V','SV_W']])),
            maximum_absolute_field_differences={n:float(abs(current['fields'][n]-restart['fields'][n]).max()) for n in current['fields']},
            maximum_absolute_species_difference=float(abs(current['y']-restart['y']).max()))
        prof=profiles(folder/(name+'.dat.h5'));drift={k:profile_change(prof[k],old_profiles[k]) for k in prof}
        cross={k:profile_change(prof[k],stored['common_profiles'][k]) for k in prof}
        native=total_pressure(folder,step);pressure[str(step)]=native
        drops=native['area_weighted_total_pressure_drop_Pa'];old_drops=old_pressure['area_weighted_total_pressure_drop_Pa']
        point=dict(iteration=start+step,block_steps=25,block_maximum_residuals={n:max(residuals[i][n] for i in range(start+step-24,start+step+1)) for n in VERIFICATION_CRITERIA['scaled_residuals']},
            relative_mass_imbalance=current['summary']['relative_mass_imbalance'],h2_relative_convective_imbalance=current['summary']['h2_relative_convective_imbalance'],
            area_weighted_total_pressure_drop_Pa=drops,relative_pressure_drop_change=max(abs(drops[k]-old_drops[k])/max(abs(drops[k]),1.) for k in drops),
            outlet_profile_relative_drift=drift['outlet'],interior_profile_relative_drift=drift['interior'])
        point['criteria_met']=all(c['passed'] for c in checks(point,VERIFICATION_CRITERIA));history.append(point)
        rows.append(dict(added_steps=step,saved_iteration=start+step,summary=current['summary'],point=point,viscosity=mu,common_profiles=prof,
            comparison_with_restarts=comparison,common_profile_difference_from_m16=cross,strict_assessment=study_assessment(history)))
        old_profiles=prof;old_pressure=native;del current,previous,restart
    match=re.search(r'^BEGIN_SPECIES_MASS_FLOW_DIAGNOSTIC[^\n]*\n(.*?)^> \(display "END_SPECIES_MASS_FLOW_DIAGNOSTIC',transcript,re.M|re.S)
    if not match:raise ValueError('Native diagnostic species report markers missing')
    raw_species=match[1]
    report=dict(status='single_session_100_steps_independently_audited',actual_native_added_iterations=100,saved_checkpoints_verified=len(stages),
        single_native_session_verified=True,source_identical_to_m16=True,restart_source_unchanged=True,native_processes_exited=True,cpu_fallback=False,
        saved_solver_controls=controls,checkpoints=stages,startup_viscosity_checks=startup_mu,rows=rows,native_total_pressure_reports=pressure,
        native_total_pressure_reports_available=True,inlet_diffusion_enabled=False,
        full_species_flux_verified=False,full_species_flux_kg_s=None,
        full_species_flux_reason='Legacy species report contradicted saved fuel composition in zero-step probes; field integral verifies convection only. Inlet diffusion is disabled in this case; domain diffusion remains enabled. No independent complete boundary flux evidence.',
        diagnostic_species_report_raw=raw_species,strict_convergence_verified=False,
        candidate=str(folder),source_case_sha256=sha256(folder/'input.cas.h5'),source_data_sha256=sha256(folder/'input.dat.h5'),
        m16_audit_sha256=sha256(a.m16_audit),source_sha256={n:sha256(ROOT/n) for n in ['scripts/audit_single_session_gpu.py','nozzle_platform/native_reports.py','nozzle_platform/nozzle_gpu_probe.py']},**FLAGS)
    out.mkdir(parents=True)
    with (out/'single_session_comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['added_steps','saved_iteration','total_pressure_drop_fuel_Pa','total_pressure_drop_oxidizer_Pa','mass_imbalance','h2_convective_imbalance','outlet_temporal_drift','interior_temporal_drift','h2_L2_difference_from_m16','velocity_L2_difference_from_m16','outlet_difference_from_m16','interior_difference_from_m16'])
        for r in rows:
            p=r['point'];writer.writerow([r['added_steps'],r['saved_iteration'],p['area_weighted_total_pressure_drop_Pa']['fuel_inlet'],p['area_weighted_total_pressure_drop_Pa']['oxidizer_inlet'],
                p['relative_mass_imbalance'],p['h2_relative_convective_imbalance'],p['outlet_profile_relative_drift'],p['interior_profile_relative_drift'],
                r['comparison_with_restarts']['h2_relative_l2'],r['comparison_with_restarts']['velocity_relative_l2'],r['common_profile_difference_from_m16']['outlet'],r['common_profile_difference_from_m16']['interior']])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(11,8),layout='constrained');x=[r['added_steps'] for r in rows]
    axes[0,0].semilogy(x,[r['point']['h2_relative_convective_imbalance'] for r in rows],'.-',label='One session')
    axes[0,0].semilogy(x,[b['observation']['point']['h2_relative_convective_imbalance'] for b in m16['batches']],'.--',label='M16: four restarts')
    axes[0,0].axhline(.001,color='gray',ls=':');axes[0,0].set(ylabel='H2 convective imbalance');axes[0,0].legend(fontsize=8)
    for k,label in [('outlet','Outlet'),('interior','Interior')]:
        axes[0,1].semilogy(x,[r['point'][k+'_profile_relative_drift'] for r in rows],'.-',label='One session: '+label)
        axes[0,1].semilogy(x,[b['common_profile_drift'][k] for b in m16['batches']],'.--',label='Restarts: '+label)
    axes[0,1].axhline(.0001,color='gray',ls=':');axes[0,1].set(ylabel='Common-profile temporal relative drift');axes[0,1].legend(fontsize=7)
    for k in ['fuel_inlet','oxidizer_inlet']:axes[1,0].plot(x,[r['point']['area_weighted_total_pressure_drop_Pa'][k] for r in rows],'.-',label=k)
    axes[1,0].set(ylabel='Native area-weighted TOTAL pressure drop [Pa]');axes[1,0].legend(fontsize=8)
    for k,label in [('h2_relative_l2','H2'),('velocity_relative_l2','Velocity')]:axes[1,1].plot(x,[r['comparison_with_restarts'][k]*100 for r in rows],'.-',label=label)
    axes[1,1].set(ylabel='Full-cell difference from M16 [%]');axes[1,1].legend(fontsize=8)
    for ax in axes.flat:ax.set_xlabel('Added GPU iterations');ax.grid(alpha=.2)
    fig.suptitle('Same saved input, original transport, relaxation 0.1: one session vs four restarts')
    fig.savefig(out/'single_session_comparison.png',dpi=160);plt.close(fig)
    report['artifacts_sha256']={p.name:sha256(p) for p in out.iterdir() if p.is_file()}
    save(out/'audit.json',report);print(report['status'],out)


if __name__=='__main__':main()
