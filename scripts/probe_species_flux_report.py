"""Native reader experiments, with explicit CPU-reader/GPU choice.

The CPU option only reads the original CPU case and saved solution; it NEVER
initializes or iterates, and is not a CPU fallback for GPU solving.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
from nozzle_platform.workflows import save, sha256
from nozzle_platform.layered_mesh import create_native_mapping, cleanup_native_mapping
from nozzle_platform.species_reports import parse_species_report, assess_species_report, report_block,field_preservation
from nozzle_platform.nozzle_gpu_probe import read_saved,saved_solver_controls,compare_compositions
from coldflow_trial.solver import mesh_fingerprint, saved_iteration
from coldflow_trial.processes import OwnedProcesses, cleanup_records
from fluent_execution.process import WindowsJob


def reader_journal(fresh_gpu_step=False):
    if type(fresh_gpu_step) is not bool:raise ValueError('Explicit fresh-step boolean required')
    def report(stage):
        return ('(display "BEGIN_SPECIES_'+stage+'\\n")\n/report/species-mass-flow\n'
                '(display "END_SPECIES_'+stage+'\\n")\n')
    text='/file/start-transcript journal.trn\n/file/read-case input.cas.h5\n/file/read-data input.dat.h5\n'+report('input')
    if fresh_gpu_step:
        text+='/solve/iterate 1\n'+report('fresh')
        text+='/file/write-data fresh.dat.h5\n/file/read-data fresh.dat.h5\n'+report('reloaded')
        text+='/file/write-case after_report.cas.h5\n'
    return text+'/file/write-data after_report.dat.h5\n(with-output-to-file "journal.done" (lambda () (display "done")))\n'


def main():
    import psutil
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('source', 'cpu', 'output'):
        p.add_argument('--' + n, type=Path, required=True)
    p.add_argument('--backend', choices=('cpu-reader', 'gpu'), required=True)
    p.add_argument('--data-origin',choices=('saved-gpu','original-cpu'),default='saved-gpu',
                   help='Zero-step control: read original CPU data with its original CPU case')
    p.add_argument('--fresh-gpu-step',action='store_true',help='Explicit one-step GPU diagnostic: report before and after re-reading saved data')
    a = p.parse_args(); source = a.source.resolve(); cpu = a.cpu.resolve(); out = a.output.resolve()
    if a.data_origin=='original-cpu' and a.backend!='cpu-reader':raise ValueError('Original CPU control requires explicit CPU reader')
    if a.fresh_gpu_step and (a.backend!='gpu' or a.data_origin!='saved-gpu'):
        raise ValueError('A fresh step requires explicit GPU and saved-GPU source')
    if out.exists(): raise ValueError('Preserve results: choose a new output directory')
    if psutil.virtual_memory().available < 4 * 2**30: raise ValueError('4 GiB available RAM required')
    if shutil.disk_usage(out.anchor).free < 5 * 2**30: raise ValueError('5 GiB free disk required')
    if any((p.info['name'] or '').lower() in {'fluent.exe', 'fl2520.exe', 'cx2520.exe', 'cortex.exe'}
           or (p.info['name'] or '').lower().startswith('fl_mpi') for p in psutil.process_iter(['name'])):
        raise ValueError('Another native solver is active')
    result = json.loads((source/'result.json').read_text(encoding='utf-8'))
    if result['status'] != 'completed_pending_independent_property_audit': raise ValueError('Unaccepted source')
    for n, d in result['files_sha256'].items():
        if sha256(source/n) != d: raise ValueError('Source changed: ' + n)
    job_spec = json.loads((cpu/'job.json').read_text(encoding='utf-8'))
    case = source/'final.cas.h5' if a.backend == 'gpu' else cpu/'final.cas.h5'
    data = (source if a.data_origin=='saved-gpu' else cpu)/'final.dat.h5'
    if mesh_fingerprint(case) != mesh_fingerprint(source/'final.cas.h5'): raise ValueError('Different reader mesh')
    if a.backend == 'cpu-reader':
        original = json.loads((cpu/'report.json').read_text(encoding='utf-8'))
        if original['artifact_sha256']['configured.json'] != sha256(cpu/'configured.json'):
            raise ValueError('CPU reference configuration changed')
        if sha256(case) != original['artifact_sha256']['final.cas.h5']:
            raise ValueError('CPU reference case changed')
        if a.data_origin=='original-cpu' and sha256(data)!=original['artifact_sha256']['final.dat.h5']:
            raise ValueError('Original CPU reference data changed')
    reference = json.loads((cpu/'configured.json').read_text(encoding='utf-8'))
    if reference['models']['species']['options']['inlet_diffusion'] is not False:
        raise ValueError('Inlet diffusion assumption changed')
    import h5py
    with h5py.File(case) as f: rp=f['settings/Rampant Variables'][0].decode()
    if re.findall(r'\(species/inlet-diffusion\?\s+(#t|#f)\)',rp)!=['#f']:
        raise ValueError('Actual reader case has different inlet diffusion')
    if a.backend == 'gpu':
        inventory = subprocess.run(['nvidia-smi','--id=0','--query-gpu=memory.free','--format=csv,noheader,nounits'],
                                   capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        if inventory.returncode or float(inventory.stdout.strip()) < 4096:
            raise ValueError('4 GiB free GPU VRAM required')
    before = read_saved(case, data)['summary']; first = saved_iteration(data)
    if a.fresh_gpu_step:
        controls=saved_solver_controls(case)
        expected=dict(species_scheme=[1]*3,species_relax=[.1]*3,linear_absolute_tolerance_double=1e-20,
                      species_amg_alpha=[1e-6]*3,species_iter_tolerance=[1e-6]*3,species_max_iter=[100.]*3)
        if controls!=expected:raise ValueError('Fresh-step source controls changed')
    out.mkdir(parents=True)
    fluent = job_spec['fluent']; tracker = OwnedProcesses(fluent['executable'], out)
    spec = dict(backend=a.backend,data_origin=a.data_origin,added_iterations=1 if a.fresh_gpu_step else 0,
                fresh_gpu_step=a.fresh_gpu_step,source=str(source),
                inputs={n:dict(path=str(v), sha256=sha256(v)) for n,v in [('input.cas.h5',case),('input.dat.h5',data)]},
                source_result_sha256=sha256(source/'result.json'), initial_iteration=first,
                native_wall_limit_seconds=180 if a.fresh_gpu_step else 150, cpu_setup_processes=1, cpu_solver_iterations=0,
                cpu_fallback=False, controller_sha256=sha256(Path(__file__)))
    save(out/'spec.json',spec); shutil.copyfile(__file__,out/'controller_source.py')
    for n, v in [('input.cas.h5',case),('input.dat.h5',data)]: shutil.copyfile(v,out/n)
    if a.backend == 'gpu':
        for n in ('ideal_transport.py','gpu_numba_runtime_fix.py'): shutil.copyfile(source/n,out/n)
    (out/'probe.jou').write_text(reader_journal(a.fresh_gpu_step),encoding='ascii')
    native = create_native_mapping(out)
    argv = [fluent['executable'],'3ddp','-g','-nm','-t1']
    argv += ['-gpu=0','-license=enterprise'] if a.backend == 'gpu' else ['-license=premium']
    argv += ['-driver','null','-i','probe.jou']
    save(out/'command.json',dict(argv=argv,cwd=native))
    result = dict(status='starting',added_iterations=None if a.fresh_gpu_step else 0,
                  maximum_added_iterations=spec['added_iterations'],cpu_solver_iterations=0,cpu_fallback=False,
                  formal_physics_validated=False,scientific_eligible=False,grid_independence_verified=False)
    process = contained = None; started = time.monotonic()
    try:
        with (out/'launcher.log').open('wb') as log:
            process = subprocess.Popen(argv,cwd=native,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            contained = WindowsJob(); contained.assign(process)
            while not (out/'journal.done').exists():
                tracker.capture()
                if time.monotonic()-started > spec['native_wall_limit_seconds']: raise TimeoutError('Bounded reader timeout')
                tr = (out/'journal.trn').read_text(encoding='utf-8',errors='replace') if (out/'journal.trn').exists() else ''
                tr += (out/'launcher.log').read_text(encoding='utf-8',errors='replace')
                if 'Unexpected license problem' in tr: raise RuntimeError('Fluent license rejected')
                if process.poll() is not None: raise RuntimeError('Reader exited before journal.done')
                time.sleep(1)
        text = (out/'journal.trn').read_text(encoding='utf-8',errors='replace')
        parsed = parse_species_report(report_block(text,'input'))
        result['report_assessment'] = assess_species_report(parsed,before,inlet_diffusion_enabled=False)
        result['native_species_report'] = parsed
        end=first+spec['added_iterations']
        if saved_iteration(out/'after_report.dat.h5') != end: raise ValueError('Unexpected iteration count')
        actual_case=out/'after_report.cas.h5' if a.fresh_gpu_step else case
        after = read_saved(actual_case,out/'after_report.dat.h5')
        old = read_saved(actual_case,out/'fresh.dat.h5') if a.fresh_gpu_step else read_saved(case,data)
        if a.fresh_gpu_step:
            from nozzle_platform.transport_audit import components
            for label in ['fresh','reloaded']:
                values=parse_species_report(report_block(text,label))
                result[label+'_report']=values
                result[label+'_report_assessment']=assess_species_report(values,old['summary'],inlet_diffusion_enabled=False)
            if saved_iteration(out/'fresh.dat.h5')!=end:raise ValueError('Fresh checkpoint count changed')
            if mesh_fingerprint(actual_case)!=mesh_fingerprint(case):raise ValueError('Fresh step mesh changed')
            if saved_solver_controls(actual_case)!=controls:raise ValueError('Fresh step numerical controls changed')
            if any(s not in text for s in ['Preparing GPU solver','NVIDIA','GPU_NUMBA_ABI_FIX','udf_ideal_mu','udf_ideal_k']):
                raise ValueError('Missing fresh native GPU execution evidence')
            viscosity=compare_compositions(old,read_saved(case,data),components(json.loads((source/'reference.json').read_text(encoding='utf-8'))))
            if not(viscosity['current_composition_matches_ideal'] or viscosity['previous_composition_matches_ideal']):
                raise ValueError('Fresh step actual viscosity mismatch')
            result['viscosity_check']=viscosity;result['saved_solver_controls']=controls
        preserved=field_preservation(old,after)
        result.update(status='completed' if preserved['verified'] else 'completed_with_field_changes',
                      added_iterations=spec['added_iterations'],saved_iteration=end,saved_field_preservation=preserved,
                      saved_before=before,saved_after=after['summary'])
    except BaseException as error:
        result.update(status='failed',error=str(error) or type(error).__name__);raise
    finally:
        tracker.capture();result['native_wall_seconds']=time.monotonic()-started
        if contained: contained.close()
        if process: process.wait(timeout=10)
        result['cleanup']=cleanup_records(list(tracker.owned.values()));save(out/'cleanup.json',result['cleanup'])
        result['files_sha256']={p.name:sha256(p) for p in out.iterdir() if p.is_file() and p.name not in {'result.json','owned_processes.json'}}
        save(out/'result.json',result)
        if result['cleanup']['all_tracked_processes_exited']: cleanup_native_mapping(out)
    print(result['status'], result.get('report_assessment'),out,flush=True)


if __name__ == '__main__': main()
