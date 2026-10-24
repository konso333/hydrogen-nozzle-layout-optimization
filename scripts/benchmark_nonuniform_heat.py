"""Prepare and run an independent four-composition GPU conduction benchmark.

--mode prepare reads the documented M13 slab, patches four cell registers using
Fluent, freezes flow/species equations and disables species diffusion energy.
--mode mass/ideal runs 25 energy iterations from that same prepared state.
--mode restart loads a completed ideal result without manually rehooking UDFs.
No production platform selection or original result is modified.
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
from nozzle_platform.nonuniform_heat import prescribed_y,assess_saved,patch_schedule,energy_history,saved_energy_controls
from nozzle_platform.transport_benchmark import saved_fields
from nozzle_platform.layered_mesh import create_native_mapping,cleanup_native_mapping
from coldflow_trial.processes import OwnedProcesses,cleanup_records
from coldflow_trial.solver import mesh_fingerprint,snapshot
from fluent_execution.process import WindowsJob


def prepare(session,folder):
    import numpy as np
    session.settings.file.start_transcript(file_name='prepare.trn')
    session.settings.file.read_case(file_name='input.cas.h5');session.settings.file.read_data(file_name='input.dat.h5')
    before=snapshot(session)
    eq=session.settings.solution.controls.equations
    for n in ['flow','species-0','species-1','species-2']:eq[n]=False
    eq['temperature']=True
    session.settings.setup.models.species.options.diffusion_energy_source=False
    patch=session.settings.solution.initialization.patch.calculate_patch
    available=patch.variable.allowed_values();save(folder/'patch_variables.json',available)
    patch(variable='temperature',value=305.,cell_zones=['fluid'],registers=[])
    registers=session.settings.solution.cell_registers
    for i,h2 in enumerate([0.,.1,.5,1.]):
        name='composition_strip_'+str(i)
        registers.create(name=name)
        register=registers[name]
        allowed=register.type.option.allowed_values();save(folder/('register_options_'+str(i)+'.json'),allowed)
        assert 'hexahedron' in allowed
        register.type.option='hexahedron'
        register.type.hexahedron.set_state(dict(min_point=[-1e-8,.0025*i,-1e-8],max_point=[.01000001,.0025*(i+1),.01000001],inside=True))
        save(folder/('register_'+str(i)+'.json'),register.get_state())
        # Remove oxidizer/water before raising H2. Otherwise Fluent normalizes
        # an intermediate H2+O2>1 state and the final pure-H2 target is wrong.
        for j,value in patch_schedule(h2):
            # This 25.2 command uses explicit solver species indices, matching
            # the verified reference order; argument introspection is empty.
            patch(variable='species-'+str(j),value=value,cell_zones=[],registers=[name])
    monitor=session.settings.solution.monitor.residual
    monitor.options.normalize=False;monitor.options.print=True;monitor.options.plot=False
    monitor.equations['energy'].check_convergence=False
    after=snapshot(session);save(folder/'configured.json',after)
    assert before['materials']==after['materials'] and before['boundary_conditions']==after['boundary_conditions']
    assert after['controls']['equations']=={'flow':False,'species-0':False,'species-1':False,'species-2':False,'temperature':True}
    assert after['models']['species']['options']['diffusion_energy_source'] is False
    session.settings.file.write_case(file_name='prepared.cas.h5');session.settings.file.write_data(file_name='prepared.dat.h5')
    stored=saved_fields(folder/'prepared.cas.h5',folder/'prepared.dat.h5')
    y=stored['fields']['SV_Y'];expected=prescribed_y(stored['cell_centres_m'])
    assert np.max(abs(y-expected))<1e-12,'Native species patch did not preserve the prescribed profile'
    assert np.max(abs(stored['fields']['SV_T']-305.))<1e-10
    assert all(np.max(abs(stored['fields'][n]))<1e-12 for n in ['SV_U','SV_V','SV_W'])
    return dict(prepared=True,cells=stored['cells'],maximum_species_patch_error=float(abs(y-expected).max()),
        composition_frozen=True,flow_frozen=True,species_diffusion_energy=False)


def worker(folder):
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'));mode=spec['mode']
    tracker=OwnedProcesses(spec['fluent']['executable'],folder);session=None
    result=dict(status='starting',mode=mode,formal_physics_validated=False,scientific_eligible=False,
        grid_independence_verified=False,cpu_fallback=False)
    try:
        for name,record in spec['inputs'].items():
            assert sha256(Path(record['path']))==record['sha256']
            shutil.copyfile(record['path'],folder/name)
        if mode=='prepare':
            from nozzle_platform.solver_device import launch,GPU_TRANSPORT
            session=launch(dict(fluent=spec['fluent'],config=dict(compute_device='gpu',transport_model=GPU_TRANSPORT,convergence_mode='gpu-validation')),folder)
            tracker.capture(session);result.update(prepare(session,folder))
            result['status']='prepared'
        else:
            material=components(json.loads((folder/'reference.json').read_text(encoding='utf-8')))
            (folder/'ideal_transport.py').write_text(render_gpu_udf(material,legacy_cuda_compat=True),encoding='ascii')
            (folder/'gpu_numba_runtime_fix.py').write_text(compatibility_source(),encoding='ascii')
            if mode=='restart':
                for n,digest in spec['restart_code_sha256'].items():assert sha256(folder/n)==digest
            native=create_native_mapping(folder)
            hook='(gpuapp-add-udf "ideal_transport.py")\n' if mode=='ideal' else ''
            journal='/file/start-transcript journal.trn\n/file/read-case input.cas.h5\n/file/read-data input.dat.h5\n'+hook
            journal+='/solve/iterate 1\n/file/write-data first.dat.h5\n/solve/iterate 24\n/file/write-data final.dat.h5\n/file/write-case final.cas.h5\n'
            journal+='(with-output-to-file "journal.done" (lambda () (display "done")))\n'
            (folder/'probe.jou').write_text(journal,encoding='ascii')
            argv=[spec['fluent']['executable'],'3ddp','-g','-nm','-t1','-gpu=0','-license=enterprise','-driver','null','-i','probe.jou']
            save(folder/'command.json',dict(argv=argv,cwd=native,cpu_fallback=False,cpu_setup_processes=1,gpu=[0]))
            started=time.monotonic()
            with (folder/'launcher.log').open('wb') as log:
                process=subprocess.Popen(argv,cwd=native,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
                while not (folder/'journal.done').exists():
                    tracker.capture()
                    if time.monotonic()-started>120:raise TimeoutError('Tiny native benchmark exceeded 120 seconds')
                    if process.poll() is not None:raise RuntimeError('Fluent exited before journal completion')
                    time.sleep(1)
            result['native_wall_seconds']=time.monotonic()-started
            result['heat_check']=assess_saved(folder/'final.cas.h5',folder/'final.dat.h5',material,'mass' if mode=='mass' else 'ideal')
            assert result['heat_check']['passed'],'Saved physical heat/temperature mismatch'
            result['saved_energy_controls']=saved_energy_controls(folder/'final.cas.h5')
            assert mesh_fingerprint(folder/'input.cas.h5')==mesh_fingerprint(folder/'final.cas.h5')
            start,_,_=energy_history(folder/'input.dat.h5');end,ids,values=energy_history(folder/'final.dat.h5')
            assert end==start+25 and (ids>start).sum()==25
            result['saved_energy_iteration']=end;result['final_energy_residual']=float(values[-1,0])
            assert result['final_energy_residual']<1e-10
            text=(folder/'journal.trn').read_text(encoding='utf-8',errors='replace')
            assert 'Preparing GPU solver' in text and 'NVIDIA' in text
            if mode!='mass':assert 'GPU_NUMBA_ABI_FIX' in text and 'udf_ideal_k' in text
            result['status']='completed'
        output_names=['prepared.cas.h5','prepared.dat.h5','configured.json'] if mode=='prepare' else ['final.cas.h5','final.dat.h5','first.dat.h5','ideal_transport.py','gpu_numba_runtime_fix.py','journal.trn']
        result['files_sha256']={n:sha256(folder/n) for n in output_names}
    except BaseException as error:
        result.update(status='failed',error=str(error) or type(error).__name__);raise
    finally:
        tracker.capture(session)
        if session is not None:
            try:session.exit(timeout=10,wait=10)
            except Exception as e:result['exit_warning']=str(e)
        clean=cleanup_records(list(tracker.owned.values()));result['cleanup']=clean
        save(folder/'cleanup.json',clean);save(folder/'result.json',result)
        if clean['all_tracked_processes_exited']:cleanup_native_mapping(folder)


def main():
    import psutil
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['prepare','mass','ideal','restart'],required=True)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();src=args.source.resolve();folder=args.output.resolve()
    if folder.exists():raise ValueError('Use a new output directory; old evidence is preserved')
    assert psutil.virtual_memory().available>=2*2**30,'At least 2 GiB RAM required for the tiny benchmark'
    assert shutil.disk_usage(folder.anchor).free>=2*2**30
    assert not any((p.info['name'] or '').lower() in ['fluent.exe','cx2520.exe','fl2520.exe'] or (p.info['name'] or '').lower().startswith('fl_mpi') for p in psutil.process_iter(['name']))
    assert json.loads((src/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
    fluent=json.loads((src/'spec.json').read_text(encoding='utf-8'))['fluent']
    assert str(fluent['product_version']) in ['25.2.0','25.2']
    if args.mode=='prepare':
        inputs={'input.cas.h5':src/'input.cas.h5','input.dat.h5':src/'input.dat.h5','reference.json':src/'reference_configured.json'}
        assert json.loads((src/'mesh.json').read_text(encoding='utf-8'))['cells']==256
    else:
        state=json.loads((src/'result.json').read_text(encoding='utf-8'))
        assert state['status']==('completed' if args.mode=='restart' else 'prepared')
        prefix='final' if args.mode=='restart' else 'prepared'
        inputs={'input.cas.h5':src/(prefix+'.cas.h5'),'input.dat.h5':src/(prefix+'.dat.h5'),'reference.json':src/'reference.json'}
        for n in [prefix+'.cas.h5',prefix+'.dat.h5']:assert state['files_sha256'][n]==sha256(src/n)
    evidence=subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/nvidia-smi.exe'),'--id=0','--query-gpu=index,name,memory.total,memory.free,driver_version','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    fields=evidence.stdout.strip().split(',');assert evidence.returncode==0 and len(fields)==5 and float(fields[3])>=1024
    spec=dict(mode=args.mode,fluent={**fluent,'processes':1,'license_product':'enterprise'},inputs={n:dict(path=str(p),sha256=sha256(p)) for n,p in inputs.items()},
        gpu=[0],precision='double',cpu_setup_processes=1,cpu_fallback=False,total_wall_limit_seconds=240,
        native_wall_limit_seconds=120,device_evidence=evidence.stdout.strip(),implementation_sha256={})
    if args.mode=='restart':spec['restart_code_sha256']={n:sha256(src/n) for n in ['ideal_transport.py','gpu_numba_runtime_fix.py']}
    folder.mkdir(parents=True)
    for n in list(IMPLEMENTATION)+['nozzle_platform/transport_audit.py','nozzle_platform/gpu_numba_compat.py','nozzle_platform/transport_benchmark.py','nozzle_platform/nonuniform_heat.py']:
        target=folder/'source_snapshot'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,target);spec['implementation_sha256'][n]=sha256(target)
    controller=folder/'controller_source.py';shutil.copyfile(__file__,controller);spec['controller_sha256']=sha256(controller);save(folder/'spec.json',spec)
    process=contained=None;status='failed';started=time.monotonic()
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
            clean=cleanup_records(json.loads((folder/'owned_processes.json').read_text(encoding='utf-8')));save(folder/'cleanup.json',clean);assert clean['all_tracked_processes_exited']
        if (folder/'native_drive.json').exists():cleanup_native_mapping(folder)
        save(folder/'controller_outcome.json',dict(status=status,elapsed_seconds=time.monotonic()-started))
    print(status,folder,flush=True)
    if status!='completed':raise SystemExit(1)


if __name__=='__main__':
    if '--worker' in sys.argv:worker(Path(sys.argv[2]))
    else:main()
