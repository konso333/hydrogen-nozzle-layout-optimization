"""Explicit bounded original-law GPU probe of the existing single-nozzle mesh.

Independent of platform selections. Uses an audited GPU case and original CPU
composition checkpoint, or restarts its own output without manually rehooking.
Every run adds 25 iterations, saves 1/24/25 (optionally also 2/3), and keeps
all formal flags false.
"""
import argparse,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(sys.argv[3]) if '--worker' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if '--worker' in sys.argv:
    for package in ['nozzle_platform','coldflow_trial','fluent_execution']:
        m=__import__(package);m.__path__.insert(0,str(Path(sys.argv[2])/'source_snapshot'/package))
from nozzle_platform.workflows import save,sha256,IMPLEMENTATION
from nozzle_platform.transport_audit import components,render_gpu_udf
from nozzle_platform.gpu_numba_compat import source as compatibility_source
from nozzle_platform.nozzle_gpu_probe import read_saved,compare_compositions,saved_solver_controls,iteration_plan
from nozzle_platform.layered_mesh import create_native_mapping,cleanup_native_mapping
from coldflow_trial.solver import mesh_fingerprint,saved_iteration,parse_residual_history
from coldflow_trial.processes import OwnedProcesses,cleanup_records
from fluent_execution.process import WindowsJob


def worker(folder):
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'))
    tracker=OwnedProcesses(spec['fluent']['executable'],folder)
    result=dict(status='starting',formal_physics_validated=False,numerical_criteria_met=False,
        scientific_eligible=False,grid_independence_verified=False,cpu_fallback=False)
    try:
        for name,record in spec['inputs'].items():
            assert sha256(Path(record['path']))==record['sha256'],name
            shutil.copyfile(record['path'],folder/name)
        reference=json.loads((folder/'reference.json').read_text(encoding='utf-8'));material=components(reference)
        (folder/'ideal_transport.py').write_text(render_gpu_udf(material,legacy_cuda_compat=True),encoding='ascii')
        (folder/'gpu_numba_runtime_fix.py').write_text(compatibility_source(),encoding='ascii')
        if spec['restart']:
            for n in ['ideal_transport.py','gpu_numba_runtime_fix.py']:
                assert sha256(folder/n)==spec['restart_code_sha256'][n],n
        first=saved_iteration(folder/'input.dat.h5')
        hook='' if spec['restart'] else '(gpuapp-add-udf "ideal_transport.py")\n'
        journal='/file/start-transcript journal.trn\n/file/read-case input.cas.h5\n/file/read-data input.dat.h5\n'+hook
        if spec['species_order']=='first' and not spec['restart']:
            journal+=''.join('/solve/set/discretization-scheme species-'+str(i)+' 0\n' for i in range(3))
        tolerance=spec['linear_absolute_tolerance_double']
        if not spec['restart'] and tolerance!=1e-16:
            journal+="(if (= (rpgetvar 'gpuapp/solver/absolute-tolerance-double) 1e-16) (rpsetvar 'gpuapp/solver/absolute-tolerance-double "+repr(tolerance)+") (error \"Unexpected original GPU absolute tolerance\"))\n"
        journal+="(if (not (= (rpgetvar 'gpuapp/solver/absolute-tolerance-double) "+repr(tolerance)+")) (error \"GPU absolute tolerance mismatch\"))\n"
        if spec['strict_species_linear'] and not spec['restart']:
            for key,values in [('amg-alpha','1e-6 1e-6 1e-6'),('iter-tolerance','1e-6 1e-6 1e-6'),('max-iter','100 100 100')]:
                journal+="(rpsetvar 'species/"+key+" '("+values+"))\n"
        relaxation=spec['species_relaxation']
        journal+="(rpsetvar 'species/relax '("+' '.join([repr(relaxation)]*3)+"))\n"
        for count,name in iteration_plan(spec['capture_startup']):
            journal+='/solve/iterate '+str(count)+'\n/file/write-data '+name+'.dat.h5\n'
        journal+='/file/write-case final.cas.h5\n'
        journal+='(with-output-to-file "journal.done" (lambda () (display "done")))\n'
        (folder/'probe.jou').write_text(journal,encoding='ascii')
        native=create_native_mapping(folder)
        command=[spec['fluent']['executable'],'3ddp','-g','-nm','-t1','-gpu=0','-license=enterprise','-driver','null','-i','probe.jou']
        save(folder/'command.json',dict(argv=command,cwd=native,gpu=[0],cpu_setup_processes=1,cpu_fallback=False))
        started=time.monotonic()
        with (folder/'launcher.log').open('wb') as log:
            process=subprocess.Popen(command,cwd=native,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            while not (folder/'journal.done').exists():
                tracker.capture()
                if time.monotonic()-started>180:raise TimeoutError('Native GPU journal exceeded 180 seconds')
                text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace') if (folder/'journal.trn').exists() else ''
                if 'Unexpected license problem' in text:raise RuntimeError('Fluent license rejected')
                if process.poll() is not None:raise RuntimeError('Fluent launcher exited before completion')
                time.sleep(1)
        result['native_wall_seconds']=time.monotonic()-started
        from ansys.fluent.core.filereader.case_file import CaseFile
        saved_case=CaseFile(case_file_name=str(folder/'final.cas.h5'))
        rp=saved_case.rp_vars()
        metadata={k:rp[k] for k in ['species/scheme','species/solve?','species/relax','species/inlet-diffusion?','energy/species-diffusion?']}
        save(folder/'case_metadata.json',metadata)
        assert metadata['species/scheme']==([0]*3 if spec['species_order']=='first' else [1]*3),'saved species scheme changed'
        result['saved_solver_controls']=saved_solver_controls(folder/'final.cas.h5')
        assert result['saved_solver_controls']['linear_absolute_tolerance_double']==tolerance
        assert result['saved_solver_controls']['species_relax']==[relaxation]*3
        if spec['strict_species_linear']:
            for key,value in [('species_amg_alpha',1e-6),('species_iter_tolerance',1e-6),('species_max_iter',100.)]:
                assert result['saved_solver_controls'][key]==[value]*3,'saved linear controls changed'
        assert mesh_fingerprint(folder/'final.cas.h5')==spec['mesh_fingerprint'],'mesh changed'
        previous=read_saved(folder/'final.cas.h5',folder/'penultimate.dat.h5')
        final=read_saved(folder/'final.cas.h5',folder/'final.dat.h5')
        result['saved_fields']=final['summary']
        if spec['capture_startup']:
            result['startup_checkpoint_fields']={}
            for name,offset in [('first',1),('second',2),('third',3)]:
                stored=read_saved(folder/'final.cas.h5',folder/(name+'.dat.h5'))
                assert saved_iteration(folder/(name+'.dat.h5'))==first+offset
                result['startup_checkpoint_fields'][name]=stored['summary']
        result['viscosity_checks']=compare_compositions(final,previous,material)
        result['saved_iteration']=saved_iteration(folder/'final.dat.h5')
        assert final['summary']['cells']==851803
        assert result['saved_iteration']==first+25
        assert saved_iteration(folder/'penultimate.dat.h5')==first+24
        history=parse_residual_history((folder/'journal.trn').read_text(encoding='utf-8',errors='replace'),first,first+25)
        result['complete_residual_iterations']=len(history);result['final_residuals']=history[first+25]
        text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
        assert 'Preparing GPU solver' in text and 'NVIDIA' in text and 'GPU_NUMBA_ABI_FIX' in text
        assert 'udf_ideal_mu' in text and 'udf_ideal_k' in text
        result['native_gpu_hooks_seen']=True
        result['files_sha256']={n:sha256(folder/n) for n in ['final.cas.h5','final.dat.h5','penultimate.dat.h5','first.dat.h5','ideal_transport.py','gpu_numba_runtime_fix.py','journal.trn']+(['second.dat.h5','third.dat.h5'] if spec['capture_startup'] else [])}
        result['status']='completed_pending_independent_property_audit'
    except BaseException as error:
        result.update(status='failed',error=str(error) or type(error).__name__);raise
    finally:
        tracker.capture();result['cleanup']=cleanup_records(list(tracker.owned.values()))
        save(folder/'cleanup.json',result['cleanup']);save(folder/'result.json',result)
        if result['cleanup']['all_tracked_processes_exited']:cleanup_native_mapping(folder)


def main():
    import psutil,numpy as np
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu',type=Path,required=True);parser.add_argument('--gpu',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--restart',type=Path)
    parser.add_argument('--start-from',choices=['cpu','gpu'],default='cpu',help='Explicit initial solution; GPU option is the saved mass-weighted 100-step diagnostic')
    parser.add_argument('--species-order',choices=['first','second'],default='second',help='Explicit first-order diagnostic, or unchanged second-order original discretization')
    parser.add_argument('--linear-absolute-tolerance',type=float,choices=[1e-16,1e-20],default=1e-16,help='Explicit stricter GPU linear-solver experiment; never changes acceptance limits')
    parser.add_argument('--strict-species-linear',action='store_true',help='Explicit diagnostic: species relative linear tolerances 1e-6 and maximum inner iterations 100')
    parser.add_argument('--species-relaxation',type=float,choices=[.01,.1,1.],default=1.,help='Explicit conservative startup diagnostic; preserved original physics and acceptance limits')
    parser.add_argument('--capture-startup',action='store_true',help='Also save and validate steps 2/3; total iterations remain 25')
    args=parser.parse_args();folder=args.output.resolve();cpu=args.cpu.resolve();gpu=args.gpu.resolve()
    if folder.exists():raise ValueError('Use a new output directory')
    assert psutil.virtual_memory().available>=4*2**30,'4 GiB available RAM required'
    assert shutil.disk_usage(folder.anchor).free>=5*2**30,'5 GiB free disk required'
    assert not any((p.info['name'] or '').lower() in {'fluent.exe','cx2520.exe','fl2520.exe'} or (p.info['name'] or '').lower().startswith('fl_mpi') for p in psutil.process_iter(['name']))
    reference=json.loads((cpu/'configured.json').read_text(encoding='utf-8'))
    state=json.loads((gpu/'configured.json').read_text(encoding='utf-8'));job=json.loads((cpu/'job.json').read_text(encoding='utf-8'))
    assert job['domain']['array_specification']['N']==1 and job['mesh']['cells']==851803
    expected=dict(fuel_velocity_m_s=1.,oxidizer_velocity_m_s=1.,fuel_temperature_K=300.,oxidizer_temperature_K=300.,oxidizer_o2_mass_fraction=.233,outlet_gauge_pressure_Pa=0.,backflow_temperature_K=300.,operating_pressure_Pa=101325.)
    from coldflow_trial.solver import verify_physics
    for s in [reference,state]:verify_physics(s,dict(expected_conditions=expected,boundaries={k:k for k in ['fuel_inlet','oxidizer_inlet','outlet','wall']}),laminar=True)
    assert reference['general']==state['general'] and reference['cell_zones']==state['cell_zones']
    bc=dict(reference['boundary_conditions']);hidden=bc.pop('perforated_wall',None)
    assert hidden is None or hidden['setup_method']=='None'
    assert bc==state['boundary_conditions']
    assert reference['methods']['spatial_discretization']==state['methods']['spatial_discretization']
    assert reference['controls']['under_relaxation']==state['controls']['under_relaxation']
    assert not state['methods']['warped_face_gradient_correction']['enable']
    a,b=components(reference),components(state)
    assert a['names']==['h2','o2','h2o','n2']
    assert a['names']==b['names'] and all(np.array_equal(a[k],b[k]) for k in ['molecular_weight','viscosity','thermal_conductivity'])
    for prop in ['viscosity','thermal_conductivity']:
        assert reference['materials']['mixture']['hydrogen-air'][prop]['option']=='ideal-gas-mixing-law'
        assert state['materials']['mixture']['hydrogen-air'][prop]['option']=='mass-weighted-mixing-law'
    original_mix={k:v for k,v in reference['materials']['mixture']['hydrogen-air'].items() if k not in {'viscosity','thermal_conductivity'}}
    gpu_mix={k:v for k,v in state['materials']['mixture']['hydrogen-air'].items() if k not in {'viscosity','thermal_conductivity'}}
    assert original_mix==gpu_mix,'Other mixture properties changed'
    assert reference['methods']['p_v_coupling']==state['methods']['p_v_coupling']
    fingerprint=mesh_fingerprint(cpu/'configured.cas.h5')
    assert fingerprint==mesh_fingerprint(gpu/'configured.cas.h5')
    for src in [cpu,gpu]:
        report=json.loads((src/'report.json').read_text(encoding='utf-8'))
        for name in ['configured.json','final.dat.h5']:
            assert report['artifact_sha256'][name]==sha256(src/name),'source audit hash changed'
        assert report['artifact_sha256']['final.cas.h5']==sha256(src/'final.cas.h5')
        if src==gpu:assert sha256(src/'configured.cas.h5')==sha256(src/'final.cas.h5')
        else:assert mesh_fingerprint(src/'final.cas.h5')==fingerprint
        assert json.loads((src/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
    inputs={'input.cas.h5':gpu/'configured.cas.h5','input.dat.h5':cpu/'final.dat.h5','reference.json':cpu/'configured.json'}
    if args.start_from=='gpu':inputs['input.dat.h5']=gpu/'final.dat.h5'
    if args.restart:
        previous=args.restart.resolve()
        assert json.loads((previous/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
        assert json.loads((previous/'result.json').read_text(encoding='utf-8'))['status']=='completed_pending_independent_property_audit'
        assert json.loads((previous/'spec.json').read_text(encoding='utf-8'))['species_order']==args.species_order
        assert json.loads((previous/'spec.json').read_text(encoding='utf-8'))['linear_absolute_tolerance_double']==args.linear_absolute_tolerance
        assert json.loads((previous/'spec.json').read_text(encoding='utf-8'))['strict_species_linear']==args.strict_species_linear
        inputs.update({'input.cas.h5':previous/'final.cas.h5','input.dat.h5':previous/'final.dat.h5'})
        assert mesh_fingerprint(inputs['input.cas.h5'])==fingerprint
    fields=read_saved(inputs['input.cas.h5'],inputs['input.dat.h5'])
    assert fields['summary']['cells']==851803 and np.ptp(fields['y'][:,0])>.5
    if not args.restart:
        from nozzle_platform.transport_audit import evaluate,differences
        key='ideal_viscosity' if args.start_from=='cpu' else 'mass_viscosity'
        initial_mu_comparison=differences(evaluate(fields['y'],a)[key],fields['mu'])
        if args.start_from=='cpu':assert initial_mu_comparison['absolute_relative_max']<1e-10
    del fields
    evidence=subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/nvidia-smi.exe'),'--id=0','--query-gpu=index,name,memory.total,memory.free,driver_version','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    values=evidence.stdout.strip().split(',');assert evidence.returncode==0 and len(values)==5 and float(values[3])>=4096,'4 GiB free GPU VRAM required'
    fluent=job['fluent'];assert str(fluent['product_version']) in {'25.2.0','25.2'}
    assert Path(fluent['executable']).is_file() and Path(fluent['python']).is_file()
    spec=dict(inputs={n:dict(path=str(p),sha256=sha256(p)) for n,p in inputs.items()},fluent={**fluent,'processes':1},restart=bool(args.restart),
        mesh_fingerprint=fingerprint,device_evidence=evidence.stdout.strip(),GPU=[0],precision='double',cpu_setup_processes=1,cpu_fallback=False,
        total_wall_limit_seconds=240,native_wall_limit_seconds=180,added_iterations=25,formal_physics_validated=False,
        scientific_eligible=False,grid_independence_verified=False,implementation_sha256={},initial_solution=args.start_from,
        species_order=args.species_order,momentum_and_energy_order='second-order-upwind',
        linear_absolute_tolerance_double=args.linear_absolute_tolerance,
        strict_species_linear=args.strict_species_linear,
        species_relaxation=args.species_relaxation,capture_startup=args.capture_startup,
        initial_solution_transport_model='ideal-gas-mixing-law' if args.start_from=='cpu' else 'mass-weighted-mixing-law',
        initial_viscosity_comparison=None if args.restart else initial_mu_comparison)
    if args.restart:spec['restart_code_sha256']={n:sha256(previous/n) for n in ['ideal_transport.py','gpu_numba_runtime_fix.py']}
    folder.mkdir(parents=True)
    for n in list(IMPLEMENTATION)+['nozzle_platform/transport_audit.py','nozzle_platform/gpu_numba_compat.py','nozzle_platform/nozzle_gpu_probe.py']:
        target=folder/'source_snapshot'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,target);spec['implementation_sha256'][n]=sha256(target)
    controller=folder/'controller_source.py';shutil.copyfile(__file__,controller);spec['controller_sha256']=sha256(controller);save(folder/'spec.json',spec)
    process=contained=None;started=time.monotonic();status='failed'
    try:
        with (folder/'worker.log').open('wb') as log:
            process=subprocess.Popen([fluent['python'],'-X','utf8','-u',str(controller),'--worker',str(folder),str(ROOT)],cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            contained=WindowsJob();contained.assign(process);process.wait(timeout=240)
            status='completed' if process.returncode==0 else 'worker_failed'
    except BaseException as error:
        save(folder/'controller_error.json',dict(error=str(error)));raise
    finally:
        if contained:contained.close()
        if process:process.wait(timeout=10)
        if (folder/'owned_processes.json').exists():
            clean=cleanup_records(json.loads((folder/'owned_processes.json').read_text(encoding='utf-8')));save(folder/'cleanup.json',clean)
            assert clean['all_tracked_processes_exited']
        if (folder/'native_drive.json').exists():cleanup_native_mapping(folder)
        save(folder/'controller_outcome.json',dict(status=status,elapsed_seconds=time.monotonic()-started))
    print(status,folder,flush=True)
    if status!='completed':raise SystemExit(1)


if __name__=='__main__':
    if '--worker' in sys.argv:worker(Path(sys.argv[2]))
    else:main()
