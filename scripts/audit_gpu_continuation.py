"""Independently re-read bounded GPU batches and plot explicitly scoped trends."""
import argparse,csv,json,os,sys
from pathlib import Path
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256
from nozzle_platform.gpu_stability import require_batch,observe_batch,batch_budget,FLAGS
from scripts.audit_nozzle_gpu_transport import audit,audit_rejected
from coldflow_trial.solver import saved_iteration


def profile_change(current,previous):
    import numpy as np
    if current['points_m']!=previous['points_m'] or current['columns']!=previous['columns']:raise ValueError('Common profile positions changed')
    a,b=np.asarray(current['values'],float),np.asarray(previous['values'],float)
    if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=4 or len(a)!=len(current['points_m']) or not len(a) or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Incomplete common profile values')
    return max(float(np.linalg.norm(a[:,part]-b[:,part])/max(np.linalg.norm(a[:,part]),1e-12)) for part in [slice(0,3),slice(3,4)])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--domain-job',type=Path,required=True)
    a=p.parse_args();study=a.study.resolve();out=a.output.resolve()
    if out.exists():raise ValueError('Use a new output directory')
    r=json.loads((study/'continuation.json').read_text(encoding='utf-8'))
    if r['status'] in ['running','stopped_error']:raise ValueError('Study incomplete or controller error; inspect the preserved record')
    assert r['maximum_added_iterations']==batch_budget(r['requested_batches'])
    assert len(r['batches'])<=r['requested_batches'] and r['all_native_processes_exited'] and not r['cpu_fallback']
    for n,d in r['implementation_sha256'].items():assert sha256(study/'source_snapshot'/n)==d
    geometry=Path(r['geometry_review']);assert sha256(geometry/'audit.json')==r['geometry_audit_sha256']
    assert sha256(geometry/'geometry.npz')==r['geometry_sha256']
    source=Path(r['source']);assert sha256(source/'final.dat.h5')==r['source_final_data_sha256']
    initial=audit(source);require_batch(initial,sha256(source/'input.dat.h5'),saved_iteration(source/'input.dat.h5'))
    source_audit=json.loads((study/'source_audit.json').read_text(encoding='utf-8'));assert initial==source_audit
    from nozzle_platform import field_profiles,outlet_sampling
    job=json.loads(a.domain_job.read_text(encoding='utf-8'))
    source_spec=json.loads((source/'spec.json').read_text(encoding='utf-8'))
    assert sha256(a.domain_job.parent/'configured.json')==source_spec['inputs']['reference.json']['sha256']
    assert job['domain']['array_specification']['N']==1 and job['mesh']['cells']==851803
    interior=field_profiles.prepare(source/'final.cas.h5',job['domain']);outlet=outlet_sampling.prepare(source/'final.cas.h5',job['domain'])
    geometry_info=json.loads((geometry/'audit.json').read_text(encoding='utf-8'))
    assert abs(interior['volume_m3']/geometry_info['volume_m3']-1)<.001
    old_profiles={'interior':field_profiles.sample(source/'final.dat.h5',interior),'outlet':outlet_sampling.sample(source/'final.dat.h5',outlet)}
    initial_profiles=old_profiles
    history=[];batches=[];rejected=False;native_added=0
    for row in r['batches']:
        assert not rejected,'A new batch was launched after rejection'
        folder=Path(row['directory']);assert row['source']==str(source)
        assert row['source_final_data_sha256']==sha256(source/'final.dat.h5')
        if row.get('accepted'):
            assert row['returncode']==0
            checked=audit(folder);require_batch(checked,sha256(source/'final.dat.h5'),saved_iteration(source/'final.dat.h5'))
            assert checked['files_sha256']['input.cas.h5']==sha256(source/'final.cas.h5')
            artifact=study/('batch_'+str(row['index'])+'_audit.json')
            assert sha256(artifact)==row['audit_sha256'] and checked==json.loads(artifact.read_text(encoding='utf-8'))
            observation=observe_batch(folder,source,geometry/'geometry.npz',history)
            assert observation==row['observation'],'Independent trend calculation changed'
            profiles={'interior':field_profiles.sample(folder/'final.dat.h5',interior),'outlet':outlet_sampling.sample(folder/'final.dat.h5',outlet)}
            changes={k:profile_change(profiles[k],old_profiles[k]) for k in profiles};old_profiles=profiles
            history.append(observation['point']);native_added+=25
            batches.append(dict(index=row['index'],accepted=True,audit=checked,observation=observation,common_profiles=profiles,common_profile_drift=changes))
            source=folder
        else:
            rejected=True
            if (folder/'command.json').exists():
                checked=audit_rejected(folder);native_added+=checked['complete_added_iterations']
                artifact=study/('batch_'+str(row['index'])+'_rejected_audit.json')
                assert sha256(artifact)==row['rejected_audit_sha256'] and checked==json.loads(artifact.read_text(encoding='utf-8'))
                batches.append(dict(index=row['index'],accepted=False,audit=checked))
            else:
                assert r['status'] in ['resource_blocked','stopped_preflight_error']
                batches.append(dict(index=row['index'],accepted=False,native_started=False))
    assert r['accepted_added_iterations']==25*len(history) and r['history']==history
    assert native_added<=100
    if r['status']=='bounded_budget_completed_pending_review':assert len(history)==r['requested_batches']
    report=dict(status='bounded_gpu_continuation_independently_audited',controller_status=r['status'],
        accepted_added_iterations=25*len(history),actual_native_added_iterations=native_added,
        initial_iteration=initial['saved_iteration'],source_audit=initial,batches=batches,
        batch_acceptance_gating_verified=True,original_inputs_and_mesh_unchanged=True,all_native_processes_exited=True,cpu_fallback=False,
        source_iterations_not_counted_toward_study_minimum=True,strict_convergence_verified=False,
        missing_formal_metrics=['Fluent area-weighted total-pressure change'],
        initial_common_profiles=initial_profiles,common_profile_method='Existing M13 common outlet and interior samplers; finite values, same points, no clipping',
        domain_job_sha256=sha256(a.domain_job),
        trend_scope='Area-weighted STATIC gauge pressure drops and unweighted SAME-MESH full-cell L2 changes; not formal total pressure/profile metrics',
        study_record_sha256=sha256(study/'continuation.json'),geometry_sha256=r['geometry_sha256'],
        reader_sha256=sha256(ROOT/'nozzle_platform/gpu_stability.py'),auditor_sha256=sha256(Path(__file__).resolve()),**FLAGS)
    out.mkdir(parents=True)
    with (out/'batch_trends.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['iteration','relative_mass_imbalance','h2_relative_convective_imbalance','fuel_static_pressure_drop_Pa','oxidizer_static_pressure_drop_Pa','relative_static_pressure_change','cell_H2_relative_L2_drift','cell_velocity_relative_L2_drift','maximum_cell_species_sum_error','common_outlet_profile_drift','common_interior_profile_drift'])
        for batch in batches:
            if not batch['accepted']:continue
            row=batch['observation']['point'];errors=batch['audit']['saved_fields']
            writer.writerow([row['iteration'],row['relative_mass_imbalance'],row['h2_relative_convective_imbalance'],
                row['area_weighted_static_pressure_drop_Pa']['fuel_inlet'],row['area_weighted_static_pressure_drop_Pa']['oxidizer_inlet'],
                row['relative_static_pressure_drop_change'],row['cell_h2_relative_l2_drift'],row['cell_velocity_relative_l2_drift'],max(s['species_sum_max_error'] for s in errors.values()),
                batch['common_profile_drift']['outlet'],batch['common_profile_drift']['interior']])
    if history:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(2,2,figsize=(11,8),layout='constrained');x=[p['iteration'] for p in history]
        for name in history[0]['block_maximum_residuals']:
            values=[p['block_maximum_residuals'][name] for p in history]
            if max(values)==0:axes[0,0].plot([],[],label=name+' = 0')
            else:axes[0,0].semilogy(x,[v if v>0 else float('nan') for v in values],'.-',label=name)
        axes[0,0].axhline(1e-5,c='gray',ls='--');axes[0,0].axhline(1e-8,c='red',ls=':')
        axes[0,0].set(ylabel='Maximum residual within each 25-step batch');axes[0,0].legend(fontsize=7,ncol=2)
        for key,label in [('relative_mass_imbalance','Mass'),('h2_relative_convective_imbalance','H2 convective only')]:
            axes[0,1].semilogy(x,[max(p[key],1e-30) for p in history],'.-',label=label)
        axes[0,1].axhline(.001,c='gray',ls='--',label='Existing strict balance limit')
        axes[0,1].set(ylabel='Relative imbalance');axes[0,1].legend(fontsize=8)
        for role in ['fuel_inlet','oxidizer_inlet']:axes[1,0].plot(x,[p['area_weighted_static_pressure_drop_Pa'][role] for p in history],'.-',label=role)
        axes[1,0].set(ylabel='Area-weighted STATIC pressure drop [Pa]');axes[1,0].legend(fontsize=8)
        for key,label in [('cell_h2_relative_l2_drift','Full-cell H2 L2'),('cell_velocity_relative_l2_drift','Full-cell velocity L2'),('relative_static_pressure_drop_change','Static pressure change, 1 Pa floor')]:
            axes[1,1].semilogy(x,[max(p[key],1e-30) if p[key] is not None else float('nan') for p in history],'.-',label=label)
        accepted=[b for b in batches if b['accepted']]
        for key in ['outlet','interior']:axes[1,1].semilogy(x,[max(b['common_profile_drift'][key],1e-30) for b in accepted],'.--',label='Common '+key+' profile drift')
        axes[1,1].set(ylabel='Change from preceding accepted endpoint');axes[1,1].legend(fontsize=7)
        for ax in axes.flat:ax.set_xlabel('Saved global iteration');ax.grid(alpha=.2)
        fig.suptitle('Original-law GPU, species relaxation 0.1: bounded trends; not a convergence certificate')
        fig.savefig(out/'continuation_trends.png',dpi=160);plt.close(fig)
    report['artifacts_sha256']={p.name:sha256(p) for p in out.iterdir() if p.is_file()};save(out/'audit.json',report)
    print(report['status'],report['accepted_added_iterations'],out)


if __name__=='__main__':main()
