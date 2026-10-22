"""Independently audit saved control/original-law/restart slab results, no Fluent launch."""
import argparse,csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from nozzle_platform.transport_benchmark import saved_fields,assess_heat
from nozzle_platform.transport_audit import components,evaluate,differences
from nozzle_platform.workflows import sha256
from coldflow_trial.solver import saved_iteration,parse_residual_history,mesh_fingerprint


def read_json(path):return json.loads(path.read_text(encoding='utf-8'))


def audit(baseline,candidate,restart,output,plot=False):
    if output.exists():raise ValueError('Use a new audit directory; previous evidence is preserved.')
    mesh=read_json(baseline/'mesh.json')
    state=read_json(baseline/'configured.json');material=components(state)
    if mesh['cells']!=256 or mesh['divisions']!=[16,4,4]:raise ValueError('Unexpected slab mesh.')
    assert state['general']['solver']['time']=='steady'
    assert not state['general']['operating_conditions']['gravity']['enable']
    assert state['models']['energy']['enabled'] and state['models']['viscous']['model']=='laminar'
    assert not state['models']['species']['reactions']['enable_volumetric_reactions']
    assert not state['cell_zones']['fluid']['fluid']['sources']['enable']
    walls=state['boundary_conditions']['wall']
    for name,temp in [('cold',300.),('hot',310.)]:
        assert walls[name]['thermal']['thermal_condition']=='Temperature'
        assert walls[name]['thermal']['temperature']==dict(option='value',value=temp)
    assert walls['insulated']['thermal']['heat_flux']==dict(option='value',value=0)
    original=components(read_json(baseline/'reference_configured.json'))
    assert material['names']==original['names']
    assert all(np.array_equal(material[key],original[key]) for key in ['viscosity','thermal_conductivity','molecular_weight'])
    expected_y=np.array([{'h2':.1,'o2':.9*.233,'h2o':0.,'n2':.9*.767}[n] for n in material['names']])
    values=evaluate(expected_y[None,:],material)
    records={};plot_values=[];profiles=[]
    for role,folder,cas,dat,law,trn in [('control',baseline,'configured.cas.h5','baseline.dat.h5','mass','fluent.trn'),
            ('original_formula',candidate,'udf.cas.h5','udf.dat.h5','ideal','journal.trn'),
            ('restart',restart,'udf.cas.h5','udf.dat.h5','ideal','journal.trn')]:
        cleanup=read_json(folder/'cleanup.json');assert cleanup['all_tracked_processes_exited']
        spec=read_json(folder/'spec.json')
        for name,digest in spec['implementation_sha256'].items():assert sha256(folder/'source_snapshot'/name)==digest,name
        assert sha256(folder/'controller_source.py')==spec['controller_sha256']
        saved=saved_fields(folder/cas,folder/dat);assert saved['cells']==256
        assert np.allclose(saved['bounds_m'],[[0,0,0],[.01,.01,.01]],rtol=0,atol=1e-12)
        fields=saved['fields'];assert np.max(abs(fields['SV_Y']-expected_y))<1e-12
        assert np.max(abs(fields['SV_Y'].sum(axis=1)-1))<=1e-8
        assert max(float(np.max(abs(fields[key]))) for key in ['SV_U','SV_V','SV_W'])<1e-12
        mu=differences(np.full(256,values[law+'_viscosity'][0]),fields['SV_MU_LAM'].reshape(-1))
        assert mu['absolute_relative_max']<1e-10
        heat=assess_heat(saved['heat_W'],float(values[law+'_conductivity'][0]),mesh);assert heat['passed']
        t_expected=300+1000*saved['cell_centres_m'][:,0]
        t_error=float(np.max(abs(fields['SV_T'].reshape(-1)-t_expected)));assert t_error<1e-6
        count=saved_iteration(folder/dat);start=0 if role=='control' else saved_iteration(folder/'input.dat.h5')
        history=parse_residual_history((folder/trn).read_text(encoding='utf-8',errors='replace'),start,count)
        assert len(history)==25 and max(history[count].values())<1e-10
        assert mesh_fingerprint(folder/cas)==mesh_fingerprint(baseline/'configured.cas.h5')
        text=(folder/trn).read_text(encoding='utf-8',errors='replace')
        assert 'NVIDIA GeForce RTX 4060' in text
        if role!='control':
            assert 'GPU_NUMBA_ABI_FIX: legacy cuCtxGetDevice' in text
            assert 'Material property: hydrogen-air/viscosity' in text and 'Material property: hydrogen-air/thermal-conductivity' in text
            for name in ['ideal_transport.py','gpu_numba_runtime_fix.py']:
                assert sha256(folder/name)==sha256(candidate/name)
            for origin,digest in spec['input_sha256'].items():
                assert sha256(Path(spec['source'])/origin)==digest
            assert sha256(folder/'input.cas.h5')==spec['input_sha256'][spec['input_names'][0][0]]
            assert sha256(folder/'input.dat.h5')==spec['input_sha256'][spec['input_names'][1][0]]
        if role=='control':
            online=read_json(folder/'baseline_raw_heat.json')
            assert max(abs(online[k]-saved['heat_W'][k]) for k in ['cold','hot','insulated'])<1e-12
        if role=='restart':
            assert Path(spec['source']).resolve()==candidate.resolve() and spec['restart']
            assert 'gpuapp-add-udf' not in (folder/'probe.jou').read_text(encoding='ascii')
        names=[cas,dat,'spec.json','controller_source.py',trn,'cleanup.json']
        if role!='control':names+=['ideal_transport.py','gpu_numba_runtime_fix.py','probe.jou','result.json','input.cas.h5','input.dat.h5']
        records[role]=dict(directory=str(folder),saved_iteration=count,additional_iterations=len(history),
            heat_check=heat,viscosity_check=mu,temperature_max_error_K=t_error,final_residuals=history[count],
            cleanup=cleanup,implementation_files_frozen=len(spec['implementation_sha256']),
            worker_status=read_json(folder/'result.json')['status'],
            files_sha256={name:sha256(folder/name) for name in names})
        plot_values.append([heat['expected_heat_W'],abs(saved['heat_W']['hot'])])
        profiles.extend([role,*point,float(t),float(m)] for point,t,m in
            zip(saved['cell_centres_m'],fields['SV_T'].reshape(-1),fields['SV_MU_LAM'].reshape(-1)))
    report=dict(status='small_constant_composition_transport_and_restart_verified',
        scope='256-cell steady stationary conduction; YH2=.1; constant component mu/k; no reaction',
        constant_composition_gpu_transport_verified=True,saved_restart_verified=True,
        production_nozzle_flow_validated=False,CPU_GPU_converged_nozzle_agreement=False,
        formal_physics_validated=False,numerical_criteria_met=False,scientific_eligible=False,grid_independence_verified=False,
        original_results_overwritten=False,CPU_fallback=False,stages=records,
        source_code_sha256={str(p.relative_to(ROOT)):sha256(p) for p in [Path(__file__).resolve(),
            ROOT/'nozzle_platform/transport_benchmark.py',ROOT/'nozzle_platform/transport_audit.py',ROOT/'nozzle_platform/gpu_numba_compat.py']},
        caveats=['Control checkpoint is valid although its later RPC UDF stage failed; that failure remains unchanged',
            'Uniform stationary mixture does not validate composition gradients, viscous-flow performance or combustion',
            'SV_HEAT_FLUX is W per face; independently crosschecked against the control online heat report',
            'Synchronous UDF reload stalled; this successful route uses Fluent native journal command processing'])
    output.mkdir(parents=True)
    with (output/'saved_profiles.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['stage','x_m','y_m','z_m','T_K','mu_Pa_s']);writer.writerows(profiles)
    if plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(1,2,figsize=(10,4.5),layout='constrained')
        q=np.array(plot_values)*1000;x=np.arange(3)
        axes[0].bar(x-.18,q[:,0],width=.36,label='Analytic');axes[0].bar(x+.18,q[:,1],width=.36,label='Saved GPU result')
        axes[0].set(xticks=x,xticklabels=['Mass-weighted','Original formula','Restart'],ylabel='Heat rate [mW]');axes[0].legend()
        for role in records:
            rows=[row for row in profiles if row[0]==role]
            axes[1].scatter([row[1]*1000 for row in rows],[row[4] for row in rows],s=6,label=role,alpha=.6)
        axes[1].plot([0,10],[300,310],'k--',label='Analytic')
        axes[1].set(xlabel='x [mm]',ylabel='Saved cell temperature [K]');axes[1].legend(fontsize=8)
        fig.suptitle('Constant-composition GPU material and restart check')
        fig.savefig(output/'gpu_material_and_restart.png',dpi=160);plt.close(fig)
    report['artifacts_sha256']={p.name:sha256(p) for p in output.iterdir() if p.is_file()}
    (output/'audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['baseline','candidate','restart','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--plot',action='store_true');args=parser.parse_args()
    result=audit(args.baseline.resolve(),args.candidate.resolve(),args.restart.resolve(),args.output.resolve(),args.plot)
    print(result['status'])
