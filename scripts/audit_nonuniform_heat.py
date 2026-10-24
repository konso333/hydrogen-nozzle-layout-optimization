"""Read-only audit of actual wall heat in the nonuniform-composition GPU test."""
import argparse,csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256
from nozzle_platform.transport_audit import components
from nozzle_platform.nonuniform_heat import assess_saved,energy_history,saved_energy_controls,H2_LEVELS
from coldflow_trial.solver import mesh_fingerprint


def audit(folder,mode):
    import numpy as np
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'))
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    assert spec['mode']==mode and result['status']=='completed'
    assert json.loads((folder/'controller_outcome.json').read_text(encoding='utf-8'))['status']=='completed'
    assert json.loads((folder/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
    assert sha256(folder/'controller_source.py')==spec['controller_sha256']
    for n,digest in spec['implementation_sha256'].items():assert sha256(folder/'source_snapshot'/n)==digest
    for n,r in spec['inputs'].items():assert sha256(Path(r['path']))==r['sha256']==sha256(folder/n)
    for n,digest in result['files_sha256'].items():assert sha256(folder/n)==digest
    command=json.loads((folder/'command.json').read_text(encoding='utf-8'))
    assert '3ddp' in command['argv'] and '-gpu=0' in command['argv'] and '-t1' in command['argv'] and not command['cpu_fallback']
    assert mesh_fingerprint(folder/'input.cas.h5')==mesh_fingerprint(folder/'final.cas.h5')
    controls=saved_energy_controls(folder/'final.cas.h5')
    assert controls==saved_energy_controls(folder/'input.cas.h5')
    material=components(json.loads((folder/'reference.json').read_text(encoding='utf-8')))
    law='mass' if mode=='mass' else 'ideal'
    check=assess_saved(folder/'final.cas.h5',folder/'final.dat.h5',material,law)
    assert check['passed']
    wrong=assess_saved(folder/'final.cas.h5',folder/'final.dat.h5',material,'ideal' if law=='mass' else 'mass')
    assert not wrong['passed'],'The physical check cannot distinguish the models'
    start,_,_=energy_history(folder/'input.dat.h5');end,ids,values=energy_history(folder/'final.dat.h5')
    assert end==start+25 and np.array_equal(ids[ids>start],np.arange(start+1,end+1))
    assert values[-1,0]<1e-10
    text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
    assert 'Preparing GPU solver' in text and 'NVIDIA' in text
    journal=(folder/'probe.jou').read_text(encoding='ascii')
    if mode=='mass':assert 'gpuapp-add-udf' not in journal
    else:assert 'GPU_NUMBA_ABI_FIX' in text and 'udf_ideal_k' in text
    if mode=='restart':
        assert 'gpuapp-add-udf' not in journal
        for n,digest in spec['restart_code_sha256'].items():assert sha256(folder/n)==digest
    return dict(directory=str(folder),mode=mode,heat_check=check,saved_energy_controls=controls,wrong_mixing_law_rejected=True,
        initial_energy_iteration=start,final_energy_iteration=end,complete_added_energy_iterations=25,
        final_energy_residual=float(values[-1,0]),mesh_unchanged=True,original_inputs_unchanged=True,
        cleanup_verified=True,cpu_fallback=False,
        files_sha256={n:sha256(folder/n) for n in ['input.cas.h5','input.dat.h5','reference.json','spec.json','controller_source.py','final.cas.h5',
            'final.dat.h5','ideal_transport.py','gpu_numba_runtime_fix.py','journal.trn','probe.jou','cleanup.json','controller_outcome.json']})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ['mass','ideal','restart','output']:parser.add_argument('--'+n,type=Path,required=True)
    parser.add_argument('--plot',action='store_true');args=parser.parse_args();out=args.output.resolve()
    if out.exists():raise ValueError('Use a new audit output directory')
    stages={n:audit(getattr(args,n).resolve(),n) for n in ['mass','ideal','restart']}
    for n in ['input.cas.h5','input.dat.h5','reference.json']:
        assert stages['mass']['files_sha256'][n]==stages['ideal']['files_sha256'][n]
    for a,b in [('input.cas.h5','final.cas.h5'),('input.dat.h5','final.dat.h5')]:
        assert stages['restart']['files_sha256'][a]==stages['ideal']['files_sha256'][b]
    for n in ['ideal_transport.py','gpu_numba_runtime_fix.py']:
        assert stages['ideal']['files_sha256'][n]==stages['restart']['files_sha256'][n]
    report=dict(status='nonuniform_frozen_composition_conductivity_response_and_restart_verified',stages=stages,
        conductivity_response_verified=True,saved_restart_verified=True,
        real_nozzle_conductivity_field_validated=False,formal_physics_validated=False,numerical_criteria_met=False,
        scientific_eligible=False,grid_independence_verified=False,cpu_fallback=False,
        scope='256-cell static conduction; four prescribed frozen composition strips; not a physical nozzle-flow solution',
        reader_sha256=sha256(ROOT/'nozzle_platform/nonuniform_heat.py'),auditor_sha256=sha256(Path(__file__).resolve()))
    out.mkdir(parents=True)
    with (out/'strip_heat_comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['stage','band','H2_mass_fraction','expected_k_W_m_K','cold_inferred_k_W_m_K','hot_inferred_k_W_m_K','cold_heat_W','hot_heat_W'])
        for role,data in stages.items():
            for row in data['heat_check']['strips']:
                writer.writerow([role,row['band'],H2_LEVELS[row['band']],*[row[k] for k in ['expected_k_W_m_K','cold_inferred_k_W_m_K','hot_inferred_k_W_m_K','cold_heat_W','hot_heat_W']]])
    if args.plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from nozzle_platform.transport_benchmark import saved_fields
        fig,axes=plt.subplots(1,2,figsize=(10,4.5),layout='constrained')
        for role,label,style in [('mass','Mass-weighted','--'),('ideal','Original formula','-'),('restart','Restart',':')]:
            rows=stages[role]['heat_check']['strips']
            axes[0].plot(H2_LEVELS,[r['expected_k_W_m_K'] for r in rows],style,label=label+' analytic')
            axes[0].scatter(H2_LEVELS,[r['hot_inferred_k_W_m_K'] for r in rows],s=30,marker='x',label=label+' heat-inferred')
        axes[0].set(xlabel='H2 mass fraction in strip',ylabel='Conductivity [W / (m K)]');axes[0].legend(fontsize=7)
        p=args.ideal.resolve();data=saved_fields(p/'final.cas.h5',p/'final.dat.h5');xyz=data['cell_centres_m']
        axes[1].scatter(xyz[:,0]*1000,data['fields']['SV_T'].reshape(-1),c=xyz[:,1]*1000,s=12,cmap='viridis',label='Saved GPU cells')
        axes[1].plot([0,10],[300,310],'k--',label='Analytic');axes[1].set(xlabel='x [mm]',ylabel='Temperature [K]');axes[1].legend(fontsize=8)
        fig.suptitle('Prescribed nonuniform composition: GPU conduction check');fig.savefig(out/'nonuniform_conductivity_validation.png',dpi=160);plt.close(fig)
    report['artifacts_sha256']={p.name:sha256(p) for p in out.iterdir() if p.is_file()};save(out/'audit.json',report)
    print(report['status'],out)


if __name__=='__main__':main()
