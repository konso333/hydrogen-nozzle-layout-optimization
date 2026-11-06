"""M27 bounded counter-diffusion preparation -> GPU -> CPU handoff.

Diagnostic only. One session per stage; external ledger never reopens old work.
"""
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
import argparse,json,shutil,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256,IMPLEMENTATION
OUT=ROOT/'outputs/platform_m27_hybrid_diffusion_20261105'
LEDGER=ROOT/'.local/m27_native_budget.json'

def configure(session,folder):
    from nozzle_platform.solver_device import configure_transport,GPU_TRANSPORT
    from nozzle_platform.hybrid_diffusion_benchmark import D,MW,SPECIES,LEFT,RIGHT
    session.settings.file.read_mesh(file_name='slab.msh');session.settings.mesh.check()
    setup=session.settings.setup;setup.general.solver.type='pressure-based';setup.general.solver.time='steady'
    setup.general.operating_conditions.operating_pressure=101325
    setup.models.viscous.model='laminar';setup.models.energy.enabled=True
    setup.models.species.model.option='species-transport';setup.models.species.model.material='hydrogen-air'
    setup.models.species.reactions.enable_volumetric_reactions=False
    setup.models.species.options.inlet_diffusion=True;setup.models.species.options.diffusion_energy_source=False
    setup.models.species.options.thermal_diffusion=False;setup.models.species.options.multi_component_diffusion=False
    mix=setup.materials.mixture['hydrogen-air'];mix.density.option='incompressible-ideal-gas';configure_transport(mix,GPU_TRANSPORT)
    save(folder/'diffusivity_options.json',mix.mass_diffusivity.option.allowed_values())
    mix.mass_diffusivity.option='constant-dilute-appx';mix.mass_diffusivity.constant_mass_diffusivity=D
    if list(mix.species.volumetric.get_state())!=list(SPECIES):raise ValueError('Species ordering differs')
    for name,mw in zip(SPECIES,MW):
        if abs(mix.species.volumetric_species[name].molecular_weight.value.get_state()-mw)>1e-10:raise ValueError('Native molecular weight differs')
    for name,h in [('inlet',LEFT),('outlet',RIGHT)]:
        bc=setup.boundary_conditions.velocity_inlet[name];bc.momentum.velocity_specification_method='Magnitude, Normal to Boundary';bc.momentum.velocity_magnitude=0.
        bc.thermal.temperature=300.;bc.species.specify_species_in_mole_fractions=False
        for s,y in zip(SPECIES[:-1],[h,.1,.1]):bc.species.species_mass_fraction[s]=y
    methods=session.settings.solution.methods;methods.p_v_coupling.flow_scheme='SIMPLE';methods.warped_face_gradient_correction.enable=False
    methods.spatial_discretization.gradient_scheme='least-square-cell-based'
    for n in ['mom','temperature','species-0','species-1','species-2']:methods.spatial_discretization.discretization_scheme[n]='second-order-upwind'
    methods.spatial_discretization.discretization_scheme['pressure']='second-order'
    monitor=session.settings.solution.monitor.residual;monitor.options.normalize=False
    monitor.options.residual_values.scale_residuals=True;monitor.options.residual_values.compute_local_scale=True;monitor.options.print=True;monitor.options.plot=False
    for n in monitor.equations.get_object_names():monitor.equations[n].check_convergence=False
    init=session.settings.solution.initialization;init.initialization_type='standard'
    for n,v in {'temperature':300.,'pressure':0.,'x-velocity':0.,'y-velocity':0.,'z-velocity':0.,'species-0':.1,'species-1':.1,'species-2':.1}.items():init.defaults[n]=v
    init.standard_initialize()

def worker(folder):
    from coldflow_trial.processes import OwnedProcesses
    from coldflow_trial.solver import launch_session,snapshot,parse_residual_history,mesh_fingerprint
    from nozzle_platform.solver_device import launch,GPU_TRANSPORT
    from nozzle_platform.layered_mesh import create_native_mapping
    from nozzle_platform.flow_solver import close_flow_session
    from nozzle_platform.species_flux_benchmark import iteration_count,saved_diffusion_flags
    from nozzle_platform.hybrid_diffusion_benchmark import mesh,state,render_udf,physical_signature
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'));stage=spec['stage'];session=None
    tracker=OwnedProcesses(spec['fluent']['executable'],folder)
    result=dict(status='starting',stage=stage,actual_added_iterations=None,formal_physics_validated=False,scientific_eligible=False)
    try:
        if any(sha256(ROOT/n)!=h for n,h in spec['implementation_sha256'].items()):raise ValueError('Implementation changed')
        (folder/'probe.c').write_text(render_udf(),encoding='ascii')
        if stage!='prepare':
            source=Path(spec['source'])
            for n,h in spec['source_sha256'].items():
                if sha256(source/n)!=h:raise ValueError('Source changed')
            shutil.copyfile(source/'final.cas.h5',folder/'input.cas.h5');shutil.copyfile(source/'final.dat.h5',folder/'input.dat.h5')
        fluent=dict(spec['fluent'],processes=1,start_timeout_seconds=90)
        if stage=='gpu':session=launch(dict(fluent=fluent,config=dict(compute_device='gpu',transport_model=GPU_TRANSPORT,convergence_mode='gpu-validation')),folder)
        else:session=launch_session(dict(fluent=fluent),Path(create_native_mapping(folder)))
        tracker.capture(session);session.settings.file.start_transcript(file_name='fluent.trn')
        if stage=='prepare':save(folder/'mesh.json',mesh(folder/'slab.msh'));configure(session,folder);first=0
        else:session.settings.file.read_case_data(file_name='input.cas.h5');first=iteration_count(folder/'input.dat.h5')
        actual=snapshot(session);save(folder/'loaded_configured.json',actual)
        flags={key:session.rp_vars(symbol) for key,symbol in [('thermal_diffusion','species/thermal-diffusion?'),('multi_component_diffusion','species/multicomponent-diffusion?')]}
        if any(type(v) is not bool or v for v in flags.values()):raise ValueError('Diffusion options changed')
        result['live_diffusion_flags']=flags
        if actual['models']['species']['options']['inlet_diffusion'] is not True:raise ValueError('Inlet diffusion not active')
        if stage!='prepare':
            reference=json.loads((OUT/'prepare/loaded_configured.json').read_text(encoding='utf-8'))
            if physical_signature(actual,flags)!=physical_signature(reference,dict(thermal_diffusion=False,multi_component_diffusion=False)):raise ValueError('Active model changed across handoff')
        udf=session.settings.setup.user_defined;udf.compiled_udf(library_name='libm27',source_files=['probe.c'],header_files=[],use_built_in_compiler=True);udf.load(udf_library_name='libm27')
        def mark(label):session.scheme_eval.eval('(begin (newline) (display "M27_'+label+'") (newline))')
        def probe(label):mark('BEGIN_'+label);udf.execute_on_demand(lib_name='m27_probe::libm27');mark('END_'+label)
        def report(label):mark('BEGIN_'+label);session.settings.results.report.flow.species_mass_flow(domain='mixture');mark('END_'+label)
        if stage=='prepare':
            udf.execute_on_demand(lib_name='m27_seed::libm27');result['seed_called_only_in_preparation']=True
        else:
            probe('read_probe');report('read_report')
            if stage=='cpu':
                session.settings.file.write_case(file_name='read.cas.h5');session.settings.file.write_data(file_name='read.dat.h5')
                if iteration_count(folder/'read.dat.h5')!=first:raise ValueError('Read unexpectedly added iterations')
                from nozzle_platform.hybrid_diffusion_benchmark import field_difference
                result['read_preservation']=field_difference(folder/'input.dat.h5',folder/'read.dat.h5')
                if not result['read_preservation']['all_within_declared_tolerances']:raise ValueError('Read preservation failed')
            save(folder/'stage.json',dict(stage='computing',compute_started_monotonic=time.monotonic()))
            started=time.monotonic();session.settings.solution.run_calculation.iterate(iter_count=spec['requested_iterations']);result['compute_wall_seconds']=time.monotonic()-started
            save(folder/'stage.json',dict(stage='computing_finished'));probe('fresh_probe');report('fresh_report')
        udf.unload(udf_library_name=['libm27'])
        session.settings.file.write_case(file_name='final.cas.h5');session.settings.file.write_data(file_name='final.dat.h5');session.settings.file.stop_transcript()
        last=iteration_count(folder/'final.dat.h5');result.update(initial_iteration=first,final_iteration=last,actual_added_iterations=last-first)
        if stage=='prepare':
            if last!=0:raise ValueError('Preparation iterated')
        else:
            if not 1<=last-first<=spec['requested_iterations']:raise ValueError('Actual iteration budget differs')
            raw=(folder/'fluent.trn').read_text(encoding='utf-8',errors='replace');parse_residual_history(raw,first,last)
            if last-first<spec['requested_iterations'] and f'!    {last} solution is converged' not in raw:raise ValueError('Unexplained early stop')
            if mesh_fingerprint(folder/'input.cas.h5')!=mesh_fingerprint(folder/'final.cas.h5'):raise ValueError('Mesh changed')
        result['saved_state']=state(folder/'final.cas.h5',folder/'final.dat.h5')
        if not result['saved_state']['passed']:raise ValueError('Saved diagnostic fields failed; do not continue')
        result['status']='completed'
    except BaseException as error:
        traceback.print_exc();result.update(status='failed',error=str(error) or type(error).__name__)
    finally:
        try:result['cleanup']=close_flow_session(session,tracker,folder)
        except Exception as error:result.update(status='failed',cleanup_error=str(error))
        save(folder/'result.json',result)
    return 0 if result['status']=='completed' else 1

def main():
    import psutil
    from fluent_execution.process import WindowsJob
    from nozzle_platform.layered_mesh import cleanup_native_mapping
    from coldflow_trial.processes import cleanup_records
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',choices=['prepare','gpu','cpu'],required=True);args=parser.parse_args();stage=args.stage
    ledger=json.loads(LEDGER.read_text(encoding='utf-8'))
    prior_attempts=[r for r in ledger['attempts'] if r['stage']==stage]
    retry=(stage=='gpu' and len(prior_attempts)==1 and ledger.get('signature_fix_retry_authorized') is True and prior_attempts[0].get('independently_verified_actual_iterations')==0)
    if ledger['closed'] or (prior_attempts and not retry):raise ValueError('M27 stage already attempted/closed; no automatic retry')
    if any((p.info['name'] or '').lower() in ('fluent.exe','fl2520.exe','cx2520.exe','cortex.exe') or (p.info['name'] or '').lower().startswith('fl_mpi') for p in psutil.process_iter(['name'])):raise ValueError('Another native process active')
    resources=dict(ram_gib=psutil.virtual_memory().available/2**30,disk_free_gib=shutil.disk_usage(ROOT.anchor).free/2**30)
    if resources['ram_gib']<4 or resources['disk_free_gib']<5:raise ValueError('Existing resource gates: '+str(resources))
    if stage=='gpu':
        gpu=subprocess.run(['nvidia-smi','--id=0','--query-gpu=memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        if gpu.returncode or float(gpu.stdout.strip())<4096:raise ValueError('4 GiB free GPU VRAM required')
    source=None if stage=='prepare' else Path(next(r['folder'] for r in reversed(ledger['attempts']) if r['stage']==('prepare' if stage=='gpu' else 'gpu')))
    if source:
        prior=json.loads((source/'result.json').read_text(encoding='utf-8'))
        if prior['status']!='completed' or not prior['saved_state']['passed']:raise ValueError('Prior diagnostic stage failed')
    folder=OUT/(stage+'_signature_retry' if retry else stage);folder.mkdir(parents=True,exist_ok=False)
    fluent=json.loads((ROOT/'outputs/platform_m24_native/cpu_3/spec.json').read_text(encoding='utf-8'))['fluent']
    names=tuple(dict.fromkeys(IMPLEMENTATION+('nozzle_platform/species_flux_benchmark.py','nozzle_platform/hybrid_diffusion_benchmark.py','nozzle_platform/transport_benchmark.py','scripts/benchmark_hybrid_diffusion.py')))
    requested={'prepare':0,'gpu':5,'cpu':1}[stage]
    spec=dict(stage=stage,fluent=fluent,source=str(source) if source else None,requested_iterations=requested,resources=resources,total_wall_limit_seconds=180,cpu_compute_limit_seconds=20,
        implementation_sha256={n:sha256(ROOT/n) for n in names},source_sha256={n:sha256(source/n) for n in ('final.cas.h5','final.dat.h5')} if source else {})
    for name in names:
        dest=folder/'source_snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    save(folder/'spec.json',spec);ledger['attempts'].append(dict(stage=stage,folder=str(folder),requested_iterations=requested));save(LEDGER,ledger)
    process=job=None;started=time.monotonic()
    try:
        with (folder/'worker.log').open('wb') as stream:
            process=subprocess.Popen([fluent['python'],'-X','utf8','-u',str(Path(__file__)),'--worker',str(folder)],cwd=ROOT,stdout=stream,stderr=stream,stdin=subprocess.DEVNULL,env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1'),creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            job=WindowsJob();job.assign(process)
            while process.poll() is None:
                if time.monotonic()-started>180:raise TimeoutError('180s total hard limit')
                if stage!='gpu' and (folder/'stage.json').exists():
                    current=json.loads((folder/'stage.json').read_text(encoding='utf-8'))
                    if current['stage']=='computing' and time.monotonic()-current['compute_started_monotonic']>20:raise TimeoutError('20s CPU compute hard limit')
                time.sleep(.25)
    except BaseException as error:
        save(folder/'controller_error.json',dict(error=str(error)));raise
    finally:
        if job:job.close()
        if process:process.wait(timeout=10)
        if (folder/'owned_processes.json').exists():
            cleanup=cleanup_records(json.loads((folder/'owned_processes.json').read_text(encoding='utf-8')));save(folder/'cleanup.json',cleanup)
            if not cleanup['all_tracked_processes_exited']:raise RuntimeError('Cleanup failed')
        if (folder/'native_drive.json').exists():cleanup_native_mapping(folder)
        save(folder/'controller_outcome.json',dict(exit_code=process.returncode if process else None,wall_seconds=time.monotonic()-started))
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'));print(json.dumps({k:result.get(k) for k in ('status','stage','error','actual_added_iterations','compute_wall_seconds','saved_state')},ensure_ascii=False));return process.returncode

if __name__=='__main__':raise SystemExit(worker(Path(sys.argv[2])) if len(sys.argv)>1 and sys.argv[1]=='--worker' else main())
