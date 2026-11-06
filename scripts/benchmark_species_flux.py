"""M24 tiny CPU/GPU pure-H2 comparison. No production/nozzle solves."""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import save,sha256,IMPLEMENTATION


def configure(session,folder):
    from nozzle_platform.solver_device import configure_transport,GPU_TRANSPORT
    from nozzle_platform.species_flux_benchmark import DENSITY,VELOCITY
    from coldflow_trial.solver import snapshot
    session.settings.file.read_mesh(file_name='channel.msh');session.settings.mesh.check()
    setup=session.settings.setup
    setup.general.solver.type='pressure-based';setup.general.solver.time='steady'
    setup.general.operating_conditions.operating_pressure=101325
    setup.models.viscous.model='laminar';setup.models.energy.enabled=True
    setup.models.species.model.option='species-transport';setup.models.species.model.material='hydrogen-air'
    setup.models.species.reactions.enable_volumetric_reactions=False
    mix=setup.materials.mixture['hydrogen-air'];mix.density.option='incompressible-ideal-gas'
    configure_transport(mix,GPU_TRANSPORT);mix.mass_diffusivity.option='kinetic-theory'
    names=list(mix.species.volumetric.get_state());assert names==['h2','o2','h2o','n2']
    inlet=setup.boundary_conditions.velocity_inlet['inlet']
    inlet.momentum.velocity_specification_method='Magnitude, Normal to Boundary';inlet.momentum.velocity_magnitude=VELOCITY
    inlet.thermal.temperature=300.;inlet.species.specify_species_in_mole_fractions=False
    outlet=setup.boundary_conditions.pressure_outlet['outlet'];outlet.momentum.gauge_pressure=0.
    outlet.thermal.backflow_total_temperature=300.;outlet.species.specify_species_in_mole_fractions=False
    for n in names[:-1]:
        inlet.species.species_mass_fraction[n]=1. if n=='h2' else 0.
        outlet.species.backflow_species_mass_fraction[n]=1. if n=='h2' else 0.
    assert set(setup.boundary_conditions.symmetry.get_object_names())=={'sides'}
    methods=session.settings.solution.methods
    methods.p_v_coupling.flow_scheme='SIMPLE';methods.warped_face_gradient_correction.enable=False
    methods.spatial_discretization.gradient_scheme='least-square-cell-based'
    for n in ['mom','temperature','species-0','species-1','species-2']:methods.spatial_discretization.discretization_scheme[n]='second-order-upwind'
    methods.spatial_discretization.discretization_scheme['pressure']='second-order'
    monitor=session.settings.solution.monitor.residual;monitor.options.normalize=False
    monitor.options.residual_values.scale_residuals=True;monitor.options.residual_values.compute_local_scale=True
    monitor.options.print=True;monitor.options.plot=False
    for n in monitor.equations.get_object_names():monitor.equations[n].check_convergence=False
    init=session.settings.solution.initialization;init.initialization_type='standard'
    values={'temperature':300.,'pressure':0.,'x-velocity':VELOCITY,'y-velocity':0.,'z-velocity':0.,'species-0':1.,'species-1':0.,'species-2':0.}
    defaults=init.defaults.get_state()
    for n,value in values.items():
        if n not in defaults:raise ValueError('Unknown initialization variable '+n)
        init.defaults[n]=value
    init.standard_initialize();save(folder/'configured.json',snapshot(session))
    session.settings.file.write_case(file_name='initial.cas.h5');session.settings.file.write_data(file_name='initial.dat.h5')


def worker(folder):
    from nozzle_platform.solver_device import launch,GPU_TRANSPORT
    from nozzle_platform.layered_mesh import create_native_mapping
    from nozzle_platform.flow_solver import close_flow_session
    from nozzle_platform.species_flux_benchmark import channel_mesh,saved_state,parse_report,assess_report,saved_passed,iteration_count,physical_signature,state_differences
    from coldflow_trial.processes import OwnedProcesses
    from coldflow_trial.solver import launch_session,snapshot,saved_iteration,parse_residual_history,mesh_fingerprint
    spec=json.loads((folder/'spec.json').read_text(encoding='utf-8'));backend=spec['backend']
    tracker=OwnedProcesses(spec['fluent']['executable'],folder);session=None
    result=dict(status='starting',backend=backend,cpu_fallback=False,actual_added_iterations=None,
        formal_physics_validated=False,scientific_eligible=False,grid_independence_verified=False)
    try:
        if any(sha256(ROOT/n)!=h for n,h in spec['implementation_sha256'].items()):raise ValueError('Implementation changed')
        fluent=dict(spec['fluent'],processes=1,start_timeout_seconds=90)
        if backend=='cpu':session=launch_session(dict(fluent=fluent),Path(create_native_mapping(folder)))
        else:session=launch(dict(fluent=fluent,config=dict(compute_device='gpu',transport_model=GPU_TRANSPORT,convergence_mode='gpu-validation')),folder)
        tracker.capture(session);session.settings.file.start_transcript(file_name='fluent.trn')
        if backend=='cpu' and not spec['source']:
            mesh=channel_mesh(folder/'channel.msh');save(folder/'mesh.json',mesh);configure(session,folder)
        else:
            source=Path(spec['source'])
            for name in ['initial.cas.h5','initial.dat.h5','mesh.json','configured.json']:
                if sha256(source/name)!=spec['input_sha256'][name]:raise ValueError('Common input changed')
                shutil.copyfile(source/name,folder/name)
            session.settings.file.read_case_data(file_name='initial.cas.h5')
        initial=saved_state(folder/'initial.cas.h5',folder/'initial.dat.h5')
        if not initial['uniform_composition_passed']:raise ValueError('Common initial state is not pure H2')
        reference=json.loads((folder/'configured.json').read_text(encoding='utf-8'));actual=snapshot(session)
        save(folder/'actual_configured.json',actual)
        equality={key:actual[key]==reference[key] for key in ['general','materials','boundary_conditions']}
        equality['species_model']=actual['models']['species']==reference['models']['species']
        equality['energy']=actual['models']['energy']==reference['models']['energy']
        equality['laminar']=actual['models']['viscous']['model']==reference['models']['viscous']['model']=='laminar'
        result['full_snapshot_equality']=equality
        result['snapshot_differences']=state_differences(reference,actual)
        flags={name:session.rp_vars(symbol) for name,symbol in [('thermal_diffusion','species/thermal-diffusion?'),('multi_component_diffusion','species/multicomponent-diffusion?')]}
        if any(type(value) is not bool or value for value in flags.values()):raise ValueError('Actual diffusion options differ from this pure reference')
        result['actual_diffusion_flags']=flags
        if physical_signature(actual,flags)!=physical_signature(reference):raise ValueError('Active CPU/GPU benchmark physical settings differ')
        result['active_physics_preserved']=True
        def report(label,explicit):
            session.scheme_eval.eval('(begin (newline) (display "M24_BEGIN_'+label+'") (newline))')
            if explicit:session.settings.results.report.flow.species_mass_flow(domain='mixture')
            else:session.tui.report.species_mass_flow()
            session.scheme_eval.eval('(begin (newline) (display "M24_END_'+label+'") (newline))')
        if backend=='gpu':report('read_legacy',False);report('read_explicit',True)
        first=iteration_count(folder/'initial.dat.h5');assert first==0
        save(folder/'stage.json',dict(stage='computing',compute_started_monotonic=time.monotonic(),requested_iterations=5,backend=backend))
        started=time.monotonic();session.settings.solution.run_calculation.iterate(iter_count=5)
        result['compute_wall_seconds']=time.monotonic()-started
        save(folder/'stage.json',dict(stage='computing_finished',iterations_returned=5))
        report('fresh_legacy',False);report('fresh_explicit',True)
        session.settings.file.write_case(file_name='final.cas.h5');session.settings.file.write_data(file_name='final.dat.h5')
        session.settings.file.stop_transcript()
        state=saved_state(folder/'final.cas.h5',folder/'final.dat.h5');result['saved_state']=state
        result['actual_added_iterations']=iteration_count(folder/'final.dat.h5')-first
        if not 1<=result['actual_added_iterations']<=5:raise ValueError('Actual iterations outside requested maximum')
        if mesh_fingerprint(folder/'initial.cas.h5')!=mesh_fingerprint(folder/'final.cas.h5'):raise ValueError('Mesh changed')
        text=(folder/'fluent.trn').read_text(encoding='utf-8',errors='replace')
        actual_count=result['actual_added_iterations']
        if actual_count<5 and f'!    {actual_count} solution is converged' not in text:raise ValueError('Unexpected early stop')
        parse_residual_history(text,0,actual_count);result['requested_maximum_iterations']=5
        result['saved_fields_passed']=saved_passed(state)
        if not result['saved_fields_passed']:raise ValueError('Analytic saved channel fields failed')
        labels=['fresh_legacy','fresh_explicit']+(['read_legacy','read_explicit'] if backend=='gpu' else [])
        q=json.loads((folder/'mesh.json').read_text())['expected_mass_flow_kg_s']
        reports={n:dict(raw=parse_report(text,n)) for n in labels}
        for item in reports.values():item['assessment']=assess_report(item['raw'],q)
        result.update(status='completed',reports=reports,
            fresh_report_passed=all(reports[n]['assessment']['passed'] for n in ['fresh_legacy','fresh_explicit']))
    except BaseException as error:
        import traceback;traceback.print_exc();result.update(status='failed',error=str(error) or type(error).__name__)
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
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['cpu','gpu'],required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path);parser.add_argument('--budget',type=Path,required=True)
    a=parser.parse_args();folder=a.output.resolve();budget=a.budget.resolve()
    if folder.exists():raise ValueError('Use a new directory')
    if not budget.is_relative_to(ROOT/'.local') or budget.name!='m24_native_budget.json':raise ValueError('New M24 budget required')
    if psutil.virtual_memory().available<4*2**30:raise ValueError('4 GiB available RAM required; no native started')
    if shutil.disk_usage(folder.anchor).free<5*2**30:raise ValueError('5 GiB free output disk required')
    if any((p.info['name'] or '').lower() in ['fluent.exe','fl2520.exe','cx2520.exe','cortex.exe'] or (p.info['name'] or '').lower().startswith('fl_mpi') for p in psutil.process_iter(['name'])):raise ValueError('Another Fluent process is active')
    if a.backend=='gpu':
        gpu=subprocess.run(['nvidia-smi','--id=0','--query-gpu=memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        if gpu.returncode or float(gpu.stdout.strip())<4096:raise ValueError('4 GiB free GPU VRAM required')
        if not a.source:raise ValueError('GPU comparison requires the same CPU initial files')
        prior=json.loads((a.source/'result.json').read_text())
        if prior['status']!='completed' or not prior['saved_fields_passed'] or not prior['cleanup']['all_tracked_processes_exited']:raise ValueError('CPU analytic field evidence required before GPU comparison')
    reference=Path('E:/大创赛仿真结果/M13_20261022/platform/workflows/flow_d0cfac36968c4f288870c6046cd17321/job.json')
    fluent=json.loads(reference.read_text(encoding='utf-8'))['fluent']
    ledger=json.loads(budget.read_text());assert ledger['milestone']=='M24'
    used=sum(row['requested_iterations'] for row in ledger['attempts'] if row['backend']==a.backend)
    if used+5>10:raise ValueError('M24 backend iteration request budget exhausted')
    folder.mkdir(parents=True)
    names=tuple(dict.fromkeys(IMPLEMENTATION+('nozzle_platform/species_flux_benchmark.py','nozzle_platform/transport_benchmark.py','scripts/benchmark_species_flux.py')))
    source=a.source.resolve() if a.source else None
    spec=dict(backend=a.backend,fluent=fluent,source=str(source) if source else None,requested_iterations=5,cpu_processes=1,
        total_wall_limit_seconds=180,cpu_compute_wall_limit_seconds=20,
        implementation_sha256={n:sha256(ROOT/n) for n in names},
        input_sha256={n:sha256(source/n) for n in ['initial.cas.h5','initial.dat.h5','mesh.json','configured.json']} if source else {})
    for name in names:
        dest=folder/'source_snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    save(folder/'spec.json',spec);ledger['attempts'].append(dict(backend=a.backend,folder=str(folder),requested_iterations=5));save(budget,ledger)
    process=job=None;started=time.monotonic();code=1
    try:
        with (folder/'worker.log').open('wb') as stream:
            process=subprocess.Popen([fluent['python'],'-X','utf8','-u',str(Path(__file__)),'--worker',str(folder)],cwd=ROOT,stdout=stream,stderr=stream,stdin=subprocess.DEVNULL,
                env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1'),creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            job=WindowsJob();job.assign(process)
            while process.poll() is None:
                if time.monotonic()-started>180:raise TimeoutError('Native wall limit reached')
                if a.backend=='cpu' and (folder/'stage.json').exists():
                    stage=json.loads((folder/'stage.json').read_text())
                    if stage['stage']=='computing' and time.monotonic()-stage['compute_started_monotonic']>20:raise TimeoutError('CPU 20-second compute hard limit reached')
                time.sleep(.25)
            code=process.returncode
    except BaseException as error:
        save(folder/'controller_error.json',dict(error=str(error) or type(error).__name__))
        raise
    finally:
        if job:job.close()
        if process:process.wait(timeout=10)
        if (folder/'owned_processes.json').exists():
            cleanup=cleanup_records(json.loads((folder/'owned_processes.json').read_text()));save(folder/'cleanup.json',cleanup)
            if not cleanup['all_tracked_processes_exited']:raise RuntimeError('Native cleanup not verified')
        if (folder/'native_drive.json').exists():cleanup_native_mapping(folder)
        save(folder/'controller_outcome.json',dict(exit_code=process.returncode if process else None,wall_seconds=time.monotonic()-started))
    result=json.loads((folder/'result.json').read_text())
    print(json.dumps({k:result.get(k) for k in ['backend','status','error','actual_added_iterations','compute_wall_seconds','saved_fields_passed','fresh_report_passed']},ensure_ascii=False))
    return code


if __name__=='__main__':raise SystemExit(worker(Path(sys.argv[2])) if len(sys.argv)>1 and sys.argv[1]=='--worker' else main())
