"""Run/restart the original-law GPU experiment from a saved 256-cell slab.

python scripts/benchmark_gpu_transport.py --source BASELINE_DIRECTORY --output NEW_DIRECTORY
python scripts/benchmark_gpu_transport.py --source PREVIOUS_DIRECTORY --restart --output NEW_DIRECTORY

Explicit diagnostic; does not enable production GPU three-grid workflows.
Baseline input requires configured.cas.h5, baseline.dat.h5, mesh.json,
reference_configured.json and spec.json containing trusted local Fluent settings.
"""
import json,os,shutil,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if '--worker' not in sys.argv else Path(sys.argv[3])
sys.path.insert(0,str(ROOT))
if '--worker' in sys.argv:
    for package in ['nozzle_platform','coldflow_trial','fluent_execution']:
        m=__import__(package);m.__path__.insert(0,str(Path(sys.argv[2])/'source_snapshot'/package))
from nozzle_platform.workflows import save,sha256,IMPLEMENTATION
from nozzle_platform.transport_audit import components,render_gpu_udf,evaluate,differences
from nozzle_platform.gpu_numba_compat import source as compatibility_source
from nozzle_platform.layered_mesh import create_native_mapping,cleanup_native_mapping
from coldflow_trial.processes import OwnedProcesses,cleanup_records


def worker(folder):
    import ansys.fluent.core as pyfluent,h5py,numpy as np
    from coldflow_trial.solver import saved_iteration,parse_residual_history,mesh_fingerprint
    from nozzle_platform.transport_benchmark import saved_fields,assess_heat
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'));src=Path(spec['source'])
    tracker=OwnedProcesses(spec['fluent']['executable'],folder);session=None;launcher=None
    result=dict(status='starting',scientific_eligible=False,formal_physics_validated=False,
        grid_independence_verified=False,cpu_fallback=False,journal_mode=True)
    try:
        for original,target in spec['input_names']:
            assert sha256(src/original)==spec['input_sha256'][original]
            shutil.copyfile(src/original,folder/target)
        shutil.copyfile(src/'mesh.json',folder/'mesh.json')
        shutil.copyfile(spec['reference_file'],folder/'reference_configured.json')
        reference=json.loads((folder/'reference_configured.json').read_text(encoding='utf-8'));material=components(reference)
        (folder/'ideal_transport.py').write_text(render_gpu_udf(material,legacy_cuda_compat=True),encoding='ascii')
        (folder/'gpu_numba_runtime_fix.py').write_text(compatibility_source(),encoding='ascii')
        if spec['restart']:
            for name in ['ideal_transport.py','gpu_numba_runtime_fix.py']:
                assert sha256(folder/name)==sha256(src/name),'restart code changed: '+name
        journal='''/file/start-transcript journal.trn
/file/read-case-data input.cas.h5
(gpuapp-add-udf "ideal_transport.py")
/solve/iterate 25
/file/write-case udf.cas.h5
/file/write-data udf.dat.h5
(with-output-to-file "journal.done" (lambda () (display "done")))
'''
        if spec['restart']:journal=journal.replace('(gpuapp-add-udf "ideal_transport.py")\n','')
        (folder/'probe.jou').write_text(journal,encoding='ascii')
        native=create_native_mapping(folder)
        inventory=subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/nvidia-smi.exe'),
            '--id=0','--query-gpu=index,name,memory.total,memory.free','--format=csv,noheader,nounits'],
            capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        values=inventory.stdout.strip().split(',')
        if inventory.returncode or len(values)!=4 or float(values[3])<1024:
            raise RuntimeError('GPU inventory/free memory check failed; no CPU fallback')
        save(folder/'device_evidence.json',dict(inventory=inventory.stdout.strip(),precision='double',cpu_setup_processes=1,cpu_fallback=False))
        argv=[spec['fluent']['executable'],'3ddp','-g','-nm','-t1','-gpu=0','-license=enterprise',
              '-driver','null','-i','probe.jou']
        save(folder/'command.json',dict(argv=argv,cwd=native,cpu_setup_processes=1,gpu=[0],cpu_fallback=False))
        save(folder/'progress.json',dict(message='native journal loading and GPU UDF',compute_started=time.monotonic()))
        with (folder/'launcher.log').open('wb') as log:
            launcher=subprocess.Popen(argv,cwd=native,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)
            started=time.monotonic()
            while not (folder/'journal.done').exists():
                tracker.capture()
                if time.monotonic()-started>120:raise TimeoutError('Native journal initialization/iteration limit 120 seconds')
                time.sleep(1)
            save(folder/'progress.json',dict(message='journal completed; reviewing saved GPU heat and fields'))
            stored=saved_fields(folder/'udf.cas.h5',folder/'udf.dat.h5')
            y=stored['fields']['SV_Y'];mu=stored['fields']['SV_MU_LAM'].reshape(-1)
            result['heat_W']=stored['heat_W']
            values=evaluate(y,material)
            result['mu_vs_ideal']=differences(values['ideal_viscosity'],mu)
            result['mu_vs_mass']=differences(values['mass_viscosity'],mu)
            result['saved_iteration']=saved_iteration(folder/'udf.dat.h5')
            assert result['saved_iteration']==saved_iteration(folder/'input.dat.h5')+25
            assert stored['cells']==256
            mesh=json.loads((folder/'mesh.json').read_text(encoding='utf-8'))
            k=float(values['ideal_conductivity'][0])
            result['heat_check']=assess_heat(stored['heat_W'],k,mesh)
            assert result['heat_check']['passed'],'saved physical heat rate mismatch'
            assert result['mu_vs_ideal']['absolute_relative_max']<1e-10,'saved viscosity mismatch'
            expected_t=300+1000*stored['cell_centres_m'][:,0]
            result['maximum_temperature_error_K']=float(abs(stored['fields']['SV_T'].reshape(-1)-expected_t).max())
            assert result['maximum_temperature_error_K']<1e-6
            assert mesh_fingerprint(folder/'input.cas.h5')==mesh_fingerprint(folder/'udf.cas.h5'),'mesh changed'
            history=parse_residual_history((folder/'journal.trn').read_text(encoding='utf-8',errors='replace'),
                saved_iteration(folder/'input.dat.h5'),result['saved_iteration'])
            result['complete_residual_iterations']=len(history)
            result['final_residuals']=history[result['saved_iteration']]
            assert max(result['final_residuals'].values())<1e-10,'small-case residual criterion failed'
            result['elapsed_native_seconds']=time.monotonic()-started
            result['status']='journal_completed_pending_independent_validation'
    except BaseException as error:
        result.update(status='failed',error=str(error) or type(error).__name__);raise
    finally:
        try:
            tracker.capture(session)
            if session is not None:
                try:session.exit(timeout=10,wait=10)
                except Exception as error:result['exit_warning']=str(error)
        finally:
            clean=cleanup_records(list(tracker.owned.values()));save(folder/'cleanup.json',clean)
            result['cleanup']=clean;save(folder/'result.json',result)
            if clean['all_tracked_processes_exited']:cleanup_native_mapping(folder)


if '--worker' in sys.argv:worker(Path(sys.argv[2]));raise SystemExit(0)
import psutil
import numpy as np
from fluent_execution.process import WindowsJob
from nozzle_platform.transport_benchmark import saved_fields
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--restart',action='store_true')
args=parser.parse_args()
assert psutil.virtual_memory().available>=2*2**30,'At least 2 GiB available RAM required for this tiny benchmark'
assert not any((p.info['name'] or '').lower().startswith('fl_mpi') or (p.info['name'] or '').lower() in
               {'fluent.exe','cx2520.exe','fl2520.exe'} for p in psutil.process_iter(['name']))
restart=args.restart
src=args.source.resolve()
assert json.loads((src/'cleanup.json').read_text(encoding='utf-8'))['all_tracked_processes_exited']
folder=args.output.resolve()
if folder.exists():raise ValueError('Use a new output directory; previous evidence is preserved')
mesh=json.loads((src/'mesh.json').read_text(encoding='utf-8'))
if mesh['cells']!=256 or mesh['divisions']!=[16,4,4] or mesh['lengths_m']!=[.01,.01,.01]:
    raise ValueError('Only the documented 256-cell transport slab is supported')
reference_file=src/'reference_configured.json'
if not reference_file.exists():reference_file=Path(json.loads((src/'spec.json').read_text(encoding='utf-8'))['source'])/'reference_configured.json'
input_names=[('udf.cas.h5','input.cas.h5'),('udf.dat.h5','input.dat.h5')] if restart else [('configured.cas.h5','input.cas.h5'),('baseline.dat.h5','input.dat.h5')]
spec=dict(source=str(src),fluent=json.loads((src/'spec.json').read_text(encoding='utf-8'))['fluent'],
    input_names=input_names,reference_file=str(reference_file),restart=restart,
    input_sha256={name:sha256(src/name) for name,_ in input_names},
    cpu_setup_processes=1,gpu=[0],cpu_fallback=False,total_wall_limit_seconds=200,iteration_count=25,
    implementation_sha256={})
if str(spec['fluent']['product_version']) not in {'25.2.0','25.2'}:
    raise ValueError('This experiment is scoped to the inspected Fluent 25.2 runtime')
for name,_ in input_names:
    if not (src/name).is_file():raise ValueError('Missing benchmark input: '+name)
if not Path(spec['fluent']['executable']).is_file() or not Path(spec['fluent']['python']).is_file():
    raise ValueError('Configured local Fluent/Python executables are unavailable')
reference=json.loads(reference_file.read_text(encoding='utf-8'))
material=components(reference)
if set(material['names'])!={'h2','o2','h2o','n2'}:raise ValueError('Unexpected reference species')
stored=saved_fields(src/input_names[0][0],src/input_names[1][0])
fraction={'h2':.1,'o2':.9*.233,'h2o':0.,'n2':.9*.767}
if stored['cells']!=256 or np.max(abs(stored['fields']['SV_Y']-np.array([fraction[n] for n in material['names']])))>1e-12:
    raise ValueError('Input is not the constant-composition slab')
if not np.allclose(stored['bounds_m'],[[0,0,0],[.01,.01,.01]],rtol=0,atol=1e-12):
    raise ValueError('Saved mesh dimensions do not match the benchmark')
if any(np.max(abs(stored['fields'][n]))>1e-12 for n in ['SV_U','SV_V','SV_W']):
    raise ValueError('Input is not a stationary conduction case')
folder.mkdir(parents=True)
for name in [*IMPLEMENTATION,'nozzle_platform/transport_audit.py','nozzle_platform/transport_benchmark.py','nozzle_platform/gpu_numba_compat.py']:
    target=folder/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/name,target);spec['implementation_sha256'][name]=sha256(target)
controller=folder/'controller_source.py';shutil.copyfile(__file__,controller);spec['controller_sha256']=sha256(controller)
save(folder/'spec.json',spec)
process=contained=None;started=time.monotonic();status='failed'
try:
    with (folder/'worker.log').open('wb') as log:
        process=subprocess.Popen([spec['fluent']['python'],'-X','utf8','-u',str(controller),'--worker',str(folder),str(ROOT)],
            cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        contained=WindowsJob();contained.assign(process)
        process.wait(timeout=200)
        status='completed' if process.returncode==0 else 'worker_failed'
except BaseException as error:
    save(folder/'controller_error.json',dict(error=str(error)));raise
finally:
    if contained:contained.close()
    if process:process.wait(timeout=10)
    if (folder/'owned_processes.json').exists():
        clean=cleanup_records(json.loads((folder/'owned_processes.json').read_text(encoding='utf-8')))
        save(folder/'cleanup.json',clean);assert clean['all_tracked_processes_exited']
    if (folder/'native_drive.json').exists():cleanup_native_mapping(folder)
    save(folder/'controller_outcome.json',dict(status=status,elapsed_seconds=time.monotonic()-started))
print('SLAB_JOURNAL_FINISHED',status,folder,flush=True)
if status!='completed':raise SystemExit(1)
