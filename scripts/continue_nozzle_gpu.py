"""At most four audited 25-step GPU batches; stop on the first rejected batch."""
import argparse,gc,json,os,shutil,subprocess,sys,time
from pathlib import Path
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256
from nozzle_platform.gpu_stability import require_batch,batch_budget,observe_batch,FLAGS
from scripts.audit_nozzle_gpu_transport import audit,audit_rejected
from coldflow_trial.solver import saved_iteration
from fluent_execution.process import WindowsJob


def failure_status(native_started,log):
    if native_started:return 'stopped_rejected_batch'
    if any(s in log for s in ['4 GiB available RAM required','5 GiB free disk required','4 GiB free GPU VRAM required']):return 'resource_blocked'
    return 'stopped_preflight_error'


def main():
    import psutil
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['cpu','gpu','source','geometry-review','output']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--batches',type=int,choices=[1,2,3,4],default=4)
    a=p.parse_args();budget=batch_budget(a.batches);out=a.output.resolve();source=a.source.resolve()
    if out.exists():raise ValueError('Preserve old results; choose a new continuation directory')
    geometry=a.geometry_review.resolve();g=json.loads((geometry/'audit.json').read_text(encoding='utf-8'))
    assert sha256(geometry/'geometry.npz')==g['geometry_sha256']
    original=audit(source);start=saved_iteration(source/'input.dat.h5')
    require_batch(original,sha256(source/'input.dat.h5'),start)
    assert json.loads((source/'spec.json').read_text(encoding='utf-8'))['mesh_fingerprint']==g['mesh_fingerprint']
    tracked=['scripts/continue_nozzle_gpu.py','nozzle_platform/gpu_stability.py','scripts/probe_nozzle_gpu_transport.py',
        'scripts/audit_nozzle_gpu_transport.py','nozzle_platform/nozzle_gpu_probe.py','nozzle_platform/convergence.py']
    frozen={n:sha256(ROOT/n) for n in tracked};out.mkdir(parents=True)
    for name in tracked:
        dest=out/'source_snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    save(out/'source_audit.json',original)
    report=dict(status='running',requested_batches=a.batches,maximum_added_iterations=budget,
        source=str(source),source_final_data_sha256=sha256(source/'final.dat.h5'),initial_iteration=original['saved_iteration'],
        geometry_review=str(geometry),geometry_audit_sha256=sha256(geometry/'audit.json'),geometry_sha256=g['geometry_sha256'],
        species_relaxation=.1,implementation_sha256=frozen,batches=[],accepted_added_iterations=0,
        attempted_iteration_budget=0,cpu_fallback=False,**FLAGS)
    history=[];save(out/'continuation.json',report)
    try:
        for i in range(a.batches):
            for n,digest in frozen.items():assert sha256(ROOT/n)==digest,'Implementation changed during study'
            assert sha256(geometry/'geometry.npz')==g['geometry_sha256']
            gc.collect();available=psutil.virtual_memory().available
            if available<4*2**30 or shutil.disk_usage(out.anchor).free<5*2**30:
                report.update(status='resource_blocked',stop_reason='4 GiB available RAM and 5 GiB free disk required',available_ram_gib=available/2**30)
                break
            folder=out/('batch_'+str(i+1))
            command=[sys.executable,'-X','utf8',str(ROOT/'scripts/probe_nozzle_gpu_transport.py'),'--cpu',str(a.cpu.resolve()),'--gpu',str(a.gpu.resolve()),
                '--restart',str(source),'--species-relaxation','0.1','--linear-absolute-tolerance','1e-20',
                '--strict-species-linear','--capture-startup','--output',str(folder)]
            row=dict(index=i+1,directory=str(folder),command=command,source=str(source),source_final_data_sha256=sha256(source/'final.dat.h5'))
            report['batches'].append(row);report['attempted_iteration_budget']+=25;save(out/'continuation.json',report)
            process=None;job=None;started=time.monotonic()
            try:
                with (out/('batch_'+str(i+1)+'_driver.log')).open('wb') as log:
                    process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                    job=WindowsJob();job.assign(process);process.wait(timeout=300)
            finally:
                if job:job.close()
                if process:process.wait(timeout=10)
                row['driver_wall_seconds']=time.monotonic()-started
            row['returncode']=process.returncode
            if process.returncode!=0:
                row['accepted']=False
                row['native_started']=(folder/'command.json').exists()
                status=failure_status(row['native_started'],(out/('batch_'+str(i+1)+'_driver.log')).read_text(encoding='utf-8',errors='replace'))
                if row['native_started']:
                    try:
                        rejected=audit_rejected(folder);save(out/('batch_'+str(i+1)+'_rejected_audit.json'),rejected)
                        row['rejected_audit_sha256']=sha256(out/('batch_'+str(i+1)+'_rejected_audit.json'))
                    except Exception as error:row['rejection_diagnostic_error']=str(error)
                report.update(status=status,stop_reason='Runner resource/preflight or saved-field validation refused; inspect driver log; no next batch')
                break
            checked=audit(folder)
            require_batch(checked,row['source_final_data_sha256'],saved_iteration(source/'final.dat.h5'))
            observation=observe_batch(folder,source,geometry/'geometry.npz',history)
            save(out/('batch_'+str(i+1)+'_audit.json'),checked)
            row.update(accepted=True,audit_sha256=sha256(out/('batch_'+str(i+1)+'_audit.json')),observation=observation)
            history.append(observation['point']);report['accepted_added_iterations']+=25
            source=folder;save(out/'continuation.json',report)
        else:report['status']='bounded_budget_completed_pending_review'
    except BaseException as error:
        report.update(status='stopped_error',stop_reason=str(error) or type(error).__name__);raise
    finally:
        report['native_processes_remaining']=[dict(pid=p.pid,name=p.info['name']) for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() in {'fluent.exe','cortex.exe','fl2520.exe','cx2520.exe'} or (p.info['name'] or '').lower().startswith('fl_mpi')]
        report['all_native_processes_exited']=not report['native_processes_remaining']
        report['history']=history
        save(out/'continuation.json',report)
        print(report['status'],report['accepted_added_iterations'],out,flush=True)


if __name__=='__main__':main()
