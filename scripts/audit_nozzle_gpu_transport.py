"""Independently audit saved nonuniform nozzle GPU probes; never launches Fluent."""
import argparse,csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import sha256,save
from nozzle_platform.transport_audit import components
from nozzle_platform.nozzle_gpu_probe import read_saved,compare_compositions,diagnose_species,_field,saved_solver_controls
from coldflow_trial.solver import saved_iteration,mesh_fingerprint,parse_residual_history


def audit(folder):
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'))
    assert json.loads((folder/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
    assert json.loads((folder/'controller_outcome.json').read_text(encoding='utf-8'))['status']=='completed'
    assert spec['cpu_setup_processes']==1 and not spec['cpu_fallback'] and spec['added_iterations']==25
    command=json.loads((folder/'command.json').read_text(encoding='utf-8'))
    assert '-gpu=0' in command['argv'] and '-t1' in command['argv'] and '3ddp' in command['argv']
    for name,record in spec['inputs'].items():
        assert sha256(Path(record['path']))==record['sha256'],'original input changed'
        assert sha256(folder/name)==record['sha256'],'copied input changed'
    assert sha256(folder/'controller_source.py')==spec['controller_sha256']
    for name,digest in spec['implementation_sha256'].items():assert sha256(folder/'source_snapshot'/name)==digest
    if spec['restart']:
        assert 'gpuapp-add-udf' not in (folder/'probe.jou').read_text(encoding='ascii')
        for name,digest in spec['restart_code_sha256'].items():assert sha256(folder/name)==digest
    material=components(json.loads((folder/'reference.json').read_text(encoding='utf-8')))
    fingerprint=mesh_fingerprint(folder/'final.cas.h5')
    assert fingerprint==spec['mesh_fingerprint']==mesh_fingerprint(folder/'input.cas.h5')
    controls=saved_solver_controls(folder/'final.cas.h5')
    assert controls['species_scheme']==([0]*3 if spec.get('species_order','second')=='first' else [1]*3)
    assert controls['linear_absolute_tolerance_double']==spec.get('linear_absolute_tolerance_double',1e-16)
    if spec.get('strict_species_linear'):
        for k,v in [('species_amg_alpha',1e-6),('species_iter_tolerance',1e-6),('species_max_iter',100.)]:assert controls[k]==[v]*3
    baseline=saved_iteration(folder/'input.dat.h5')
    stages={}
    for name,offset in [('first',1),('penultimate',24),('final',25)]:
        assert saved_iteration(folder/(name+'.dat.h5'))==baseline+offset
        stages[name]=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'))
        assert stages[name]['summary']['cells']==851803
    initial=read_saved(folder/'input.cas.h5',folder/'input.dat.h5')
    first=compare_compositions(stages['first'],initial,material)
    final=compare_compositions(stages['final'],stages['penultimate'],material)
    text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
    assert all(s in text for s in ['Preparing GPU solver','NVIDIA','GPU_NUMBA_ABI_FIX','udf_ideal_mu','udf_ideal_k'])
    history=parse_residual_history(text,baseline,baseline+25)
    assert len(history)==25
    verdict=all(x['current_composition_matches_ideal'] or x['previous_composition_matches_ideal'] for x in [first,final])
    return dict(directory=str(folder),status='saved_probe_audited',saved_solver_controls=controls,restart=spec['restart'],initial_iteration=baseline,
        saved_iteration=baseline+25,complete_added_residuals=len(history),final_residuals=history[baseline+25],
        nonuniform_original_viscosity_evidence_verified=verdict,first_step_viscosity=first,last_step_viscosity=final,
        saved_fields={k:v['summary'] for k,v in stages.items()},conductivity_independently_verified=False,
        numerical_criteria_met=False,formal_physics_validated=False,scientific_eligible=False,grid_independence_verified=False,
        source_files_unchanged=True,mesh_unchanged=True,CPU_fallback=False,
        files_sha256={n:sha256(folder/n) for n in ['spec.json','controller_source.py','command.json','ideal_transport.py','gpu_numba_runtime_fix.py',
            'input.cas.h5','input.dat.h5','final.cas.h5','first.dat.h5','penultimate.dat.h5','final.dat.h5','journal.trn','cleanup.json','controller_outcome.json']})


def audit_rejected(folder):
    """Reproduce rejection and limited forensic evidence; never accept a subset."""
    import h5py,numpy as np
    from nozzle_platform.transport_audit import evaluate,differences
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'))
    outcome=json.loads((folder/'controller_outcome.json').read_text(encoding='utf-8'))
    assert outcome['status']=='worker_failed'
    assert json.loads((folder/'result.json').read_text(encoding='utf-8'))['status']=='failed'
    assert json.loads((folder/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
    for name,record in spec['inputs'].items():
        assert sha256(Path(record['path']))==record['sha256']==sha256(folder/name)
    for name,digest in spec['implementation_sha256'].items():assert sha256(folder/'source_snapshot'/name)==digest
    assert sha256(folder/'controller_source.py')==spec['controller_sha256']
    assert mesh_fingerprint(folder/'input.cas.h5')==mesh_fingerprint(folder/'final.cas.h5')==spec['mesh_fingerprint']
    material=components(json.loads((folder/'reference.json').read_text(encoding='utf-8')))
    initial=read_saved(folder/'input.cas.h5',folder/'input.dat.h5')
    stages={n:diagnose_species(folder/'final.cas.h5',folder/(n+'.dat.h5')) for n in ['first','penultimate','final']}
    controls=saved_solver_controls(folder/'final.cas.h5')
    assert controls['species_scheme']==([0]*3 if spec.get('species_order','second')=='first' else [1]*3)
    assert controls['linear_absolute_tolerance_double']==spec.get('linear_absolute_tolerance_double',1e-16)
    if spec.get('strict_species_linear'):
        for k,v in [('species_amg_alpha',1e-6),('species_iter_tolerance',1e-6),('species_max_iter',100.)]:assert controls[k]==[v]*3
    assert stages['final']['cells_above_sum_limit']>0,'This rejection path is only for actual cell species closure violations'
    count=initial['summary']['cells']
    with h5py.File(folder/'first.dat.h5') as f:mu_first=_field(f['results/1/phase-1/cells'],'SV_MU_LAM',1,count)
    first_check=differences(evaluate(initial['y'],material)['ideal_viscosity'],mu_first)
    del initial
    with h5py.File(folder/'penultimate.dat.h5') as f:a=_field(f['results/1/phase-1/cells'],'SV_Y',1,count,width=4)
    with h5py.File(folder/'final.dat.h5') as f:
        b=_field(f['results/1/phase-1/cells'],'SV_Y',1,count,width=4)
        mu=_field(f['results/1/phase-1/cells'],'SV_MU_LAM',1,count)
    mask=(abs(a.sum(axis=1)-1)<=1e-8)&(abs(b.sum(axis=1)-1)<=1e-8)&(a.min(axis=1)>=0)&(a.max(axis=1)<=1)&(b.min(axis=1)>=0)&(b.max(axis=1)<=1)
    va,vb=evaluate(a[mask],material),evaluate(b[mask],material)
    subset=dict(purpose='diagnostic subset only; excluded cells remain rejected; never a full-field pass',
        cells_examined=int(mask.sum()),cells_excluded=int((~mask).sum()),
        mu_vs_previous_ideal=differences(va['ideal_viscosity'],mu[mask]),mu_vs_current_ideal=differences(vb['ideal_viscosity'],mu[mask]),
        mu_vs_previous_mass=differences(va['mass_viscosity'],mu[mask]),maximum_h2_step_change=float(abs(a[mask,0]-b[mask,0]).max()),full_field_accepted=False)
    start,end=saved_iteration(folder/'input.dat.h5'),saved_iteration(folder/'final.dat.h5')
    assert end==start+25
    assert saved_iteration(folder/'first.dat.h5')==start+1 and saved_iteration(folder/'penultimate.dat.h5')==start+24
    text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
    rows=parse_residual_history(text,start,end)
    assert all(s in text for s in ['Preparing GPU solver','NVIDIA','GPU_NUMBA_ABI_FIX','udf_ideal_mu','udf_ideal_k'])
    return dict(status='rejected_species_closure',directory=str(folder),saved_solver_controls=controls,native_run_completed_but_validation_failed=True,
        first_gpu_viscosity_vs_original_input=first_check,valid_composition_subset_diagnostic=subset,stages=stages,
        saved_iteration=end,complete_added_iterations=len(rows),final_residuals=rows[end],original_files_unchanged=True,
        mesh_unchanged=True,cleanup_verified=True,formal_physics_validated=False,numerical_criteria_met=False,
        scientific_eligible=False,grid_independence_verified=False,conductivity_independently_verified=False,CPU_fallback=False,
        files_sha256={n:sha256(folder/n) for n in ['spec.json','controller_source.py','input.cas.h5','input.dat.h5','first.dat.h5',
            'penultimate.dat.h5','final.dat.h5','final.cas.h5','journal.trn','result.json','cleanup.json','controller_outcome.json']})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True);parser.add_argument('--restart',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rejected',action='store_true',help='Only document a genuinely rejected species-closure result; never accept it')
    args=parser.parse_args();output=args.output.resolve()
    if output.exists():raise ValueError('Use a new audit output directory')
    if args.rejected:
        if args.restart:raise ValueError('A rejected candidate cannot be a verified restart chain')
        report=audit_rejected(args.candidate.resolve())
        report.update(auditor_sha256=sha256(Path(__file__).resolve()),reader_sha256=sha256(ROOT/'nozzle_platform/nozzle_gpu_probe.py'))
        output.mkdir(parents=True);save(output/'audit.json',report)
        print(report['status'],output);return
    stages={'candidate':audit(args.candidate.resolve())}
    if args.restart:
        stages['restart']=audit(args.restart.resolve())
        spec=json.loads((args.restart/'spec.json').read_text(encoding='utf-8'))
        for name,target in [('input.cas.h5','final.cas.h5'),('input.dat.h5','final.dat.h5')]:
            assert spec['inputs'][name]['sha256']==stages['candidate']['files_sha256'][target]
    output.mkdir(parents=True)
    report=dict(status='audited',stages=stages,saved_restart_verified=bool(args.restart),
        nonuniform_original_viscosity_evidence_verified=all(x['nonuniform_original_viscosity_evidence_verified'] for x in stages.values()),
        conductivity_independently_verified=False,formal_physics_validated=False,CPU_GPU_converged_nozzle_agreement=False,
        numerical_criteria_met=False,scientific_eligible=False,grid_independence_verified=False,
        auditor_sha256=sha256(Path(__file__).resolve()),reader_sha256=sha256(ROOT/'nozzle_platform/nozzle_gpu_probe.py'))
    if stages['candidate']['restart']:
        # A standalone restarted run verifies its actual saved input, unchanged
        # UDF files, absence of rehooking, and all three new checkpoints above.
        report['saved_restart_verified']=True
        report['scope']='validated saved input and restarted 25-step run; does not validate the entire source run'
    save(output/'audit.json',report)
    with (output/'saved_metrics.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['stage','checkpoint','cells','mass_imbalance','h2_convective_imbalance','species_sum_error'])
        for role,record in stages.items():
            for step,row in record['saved_fields'].items():
                writer.writerow([role,step,row['cells'],row['relative_mass_imbalance'],row['h2_relative_convective_imbalance'],row['species_sum_max_error']])
    print(report['status'],report['nonuniform_original_viscosity_evidence_verified'],output)


if __name__=='__main__':main()
