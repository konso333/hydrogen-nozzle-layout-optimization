"""Fresh-mesh, bounded nonreacting H2/air engineering trial using Fluent 25.2."""

import csv
import math
from pathlib import Path
import re
import time

from coldflow_trial.config import EQUATIONS
from coldflow_trial.processes import OwnedProcesses, cleanup_records
from coldflow_trial.solver import (launch_session, snapshot, verify_physics, parse_residual_history,
                                  block_reports, saved_iteration, mesh_fingerprint, require)
from nozzle_platform.workflows import save
from nozzle_platform.flow_validation import case_openings, case_volume, verify_saved_fluxes
from nozzle_platform.mesh_quality import condition_mesh
from nozzle_platform.convergence import CRITERIA, VERIFICATION_CRITERIA, BLOCK_SIZE, checks, assessment
from nozzle_platform.solver_device import launch, validate_selection, configure_transport, verify_transport, save_validation_data, GPU_MODE
from nozzle_platform import species_controls,field_health


def quality_from_transcript(path):
    text = path.read_text(encoding='utf-8',errors='replace')
    quality = re.findall(r'Minimum Orthogonal Quality\s*=\s*([\d.eE+-]+)',text)
    require(bool(quality),'Fluent 没有返回可核实的正交质量。')
    value = float(quality[-1])
    require(math.isfinite(value),'Fluent 正交质量无效。')
    return value


def run(payload, directory):
    import h5py
    import numpy as np
    config = payload['config']
    device,transport=validate_selection(config)
    diagnostic=config.get('convergence_mode')==GPU_MODE
    from nozzle_platform.outlet_sampling import prepare as prepare_outlet, sample as sample_outlet
    from nozzle_platform.field_profiles import prepare as prepare_interior, sample as sample_interior
    expected = {'fuel_velocity_m_s':config['fuel_velocity_m_s'],'oxidizer_velocity_m_s':config['oxidizer_velocity_m_s'],
        'fuel_temperature_K':config['temperature_K'],'oxidizer_temperature_K':config['temperature_K'],
        'oxidizer_o2_mass_fraction':config['oxidizer_o2_mass_fraction'],'outlet_gauge_pressure_Pa':0.,
        'backflow_temperature_K':config['temperature_K'],'operating_pressure_Pa':101325.}
    settings = {'fluent':payload['fluent'],'config':config,'expected_conditions':expected,
        'boundaries':{k:k for k in ('fuel_inlet','oxidizer_inlet','outlet','wall')}}
    verification = config.get('convergence_mode')=='grid-verification'
    spatial_scheme='second-order-upwind' if verification or diagnostic else 'first-order-upwind'
    criteria = VERIFICATION_CRITERIA if verification else CRITERIA
    tracker = OwnedProcesses(payload['fluent']['executable'],directory)
    session = None
    state = {'fluent_executed':False,'scientific_eligible':False,'chemical_reactions_enabled':False,
        'initialization':'fresh hybrid initialization','model':'steady laminar nonreacting H2/O2/H2O/N2',
        'spatial_scheme':spatial_scheme,'boundary_layers':payload['mesh'].get('boundary_layers','none'),'convergence_criteria':criteria,
        'history':[],'numerical_criteria_met':False,'status':'preparing'}
    state.update(compute_device=device,transport_model=transport,residual_scaling='local' if device=='gpu' else 'global',
        gpu_validation_only=diagnostic,formal_physics_validated=False,grid_independence_verified=False,cpu_fallback=False)
    cleanup = None
    def progress(message):
        save(directory/'progress.json',{'message':message})
    try:
        session = launch(settings,directory)
        tracker.capture(session)
        # Relative solver file names avoid Unicode filename issues in Fluent/HDF5.
        session.settings.file.start_transcript(file_name='fluent.trn')
        session.settings.file.read_mesh(file_name='flow.msh')
        session.settings.mesh.check()
        if device=='gpu':
            # GPU validation forbids mesh conversion/repair. Verify actual bounds
            # and openings from configured.cas below, before any initialization.
            imported_bounds=np.asarray(payload['mesh']['bounds_m'])
        else:
            session.settings.file.write_case(file_name='imported.cas.h5')
            with h5py.File(directory/'imported.cas.h5','r') as case:
                points = case['meshes/1/nodes/coords/1'][()]
            imported_bounds = [points.min(axis=0),points.max(axis=0)]
        require(np.allclose(imported_bounds,payload['mesh']['bounds_m'],rtol=1e-7,atol=1e-9),'Fluent 导入后的尺寸或单位发生变化。')
        state['imported_bounds_m'] = np.asarray(imported_bounds).tolist()
        expected_patches = {k:payload['domain']['boundary_patch_counts'][k] for k in ('fuel_inlet','oxidizer_inlet','outlet')}
        if device!='gpu':
            state['imported_opening_counts'] = case_openings(directory/'imported.cas.h5')
            require(state['imported_opening_counts']==expected_patches,'Fluent 导入后实际连通开口数量改变。')
        layered=payload['mesh'].get('mesh_method')=='layered-polyhex'
        state.update(condition_mesh(session,directory,progress,preserve_layers=layered,allow_conditioning=device!='gpu'))
        state['solver_cell_type']=('mixed-polyhex' if layered else
            'polyhedral' if state['polyhedral_conversion_performed'] else 'tetrahedral')
        setup = session.settings.setup
        setup.general.solver.type = 'pressure-based'
        setup.general.solver.time = 'steady'
        setup.general.operating_conditions.operating_pressure = 101325
        setup.models.viscous.model = 'laminar'
        setup.models.energy.enabled = True
        setup.models.species.model.option = 'species-transport'
        setup.models.species.model.material = 'hydrogen-air'
        setup.models.species.reactions.enable_volumetric_reactions = False
        mix = setup.materials.mixture['hydrogen-air']
        mix.density.option = 'incompressible-ideal-gas'
        configure_transport(mix,transport)
        mix.mass_diffusivity.option = 'kinetic-theory'
        bc = setup.boundary_conditions
        for role,prefix,h2,o2 in [('fuel_inlet','fuel',1.,0.),('oxidizer_inlet','oxidizer',0.,config['oxidizer_o2_mass_fraction'])]:
            inlet = bc.velocity_inlet[role]
            inlet.momentum.velocity_specification_method = 'Magnitude, Normal to Boundary'
            inlet.momentum.velocity_magnitude = expected[prefix+'_velocity_m_s']
            inlet.thermal.temperature = config['temperature_K']
            inlet.species.specify_species_in_mole_fractions = False
            for name,value in {'h2':h2,'o2':o2,'h2o':0.}.items():
                inlet.species.species_mass_fraction[name] = value
        outlet = bc.pressure_outlet['outlet']
        outlet.momentum.gauge_pressure = 0
        outlet.thermal.backflow_total_temperature = config['temperature_K']
        outlet.species.specify_species_in_mole_fractions = False
        for name,value in {'h2':0.,'o2':config['oxidizer_o2_mass_fraction'],'h2o':0.}.items():
            outlet.species.backflow_species_mass_fraction[name] = value
        bc.wall['wall'].thermal.thermal_condition = 'Heat Flux'
        bc.wall['wall'].thermal.heat_flux = 0
        methods = session.settings.solution.methods
        methods.p_v_coupling.flow_scheme = 'SIMPLE'
        if diagnostic:methods.warped_face_gradient_correction.enable=False
        methods.spatial_discretization.gradient_scheme = 'least-square-cell-based'
        for name in ('mom','temperature','species-0','species-1','species-2'):
            methods.spatial_discretization.discretization_scheme[name] = spatial_scheme
        methods.spatial_discretization.discretization_scheme['pressure']='second-order' if verification or diagnostic else 'standard'
        def verify_methods(state):
            schemes=state['methods']['spatial_discretization']['discretization_scheme']
            require(all(schemes[name]==spatial_scheme for name in ('mom','temperature','species-0','species-1','species-2')),
                    'Fluent 实际离散格式与当前验证模式不一致。')
            require(schemes['pressure']==('second-order' if verification or diagnostic else 'standard'),'Fluent 压力离散格式不一致。')
        species_controls.apply(session,config)
        configured = snapshot(session)
        verify_physics(configured,settings,laminar=True)
        verify_transport(configured,config)
        verify_methods(configured)
        save(directory/'configured.json',configured)
        monitor = session.settings.solution.monitor.residual
        require(set(monitor.equations.get_object_names())==set(EQUATIONS),'Fluent 活跃方程与冷流模板不符。')
        monitor.options.normalize = False
        monitor.options.residual_values.scale_residuals = True
        monitor.options.residual_values.compute_local_scale = device=='gpu' or diagnostic
        if diagnostic:state['residual_scaling']='local'
        monitor.options.print = True
        monitor.options.plot = False
        for name in EQUATIONS:
            monitor.equations[name].monitor = True
            monitor.equations[name].check_convergence = False
        session.settings.file.write_case(file_name='configured.cas.h5')
        solver_controls=species_controls.saved(directory/'configured.cas.h5',config)
        save(directory/'species_controls.json',solver_controls)
        state['species_solver_controls']=solver_controls
        with h5py.File(directory/'configured.cas.h5','r') as case:
            points = case['meshes/1/nodes/coords/1'][()]
            topology=case['meshes/1/cells/zoneTopology']
            state['solver_cell_count']=int(np.sum(topology['maxId'][()]-topology['minId'][()]+1))
            require(state['solver_cell_count']>0,'Fluent 实际求解单元数无效。')
            if layered:require(state['solver_cell_count']==payload['mesh']['cells'],'壁面层网格的实际求解单元数与原生体网格不一致。')
        final_bounds = np.array([points.min(axis=0),points.max(axis=0)])
        if device=='gpu':
            require(np.allclose(final_bounds,payload['mesh']['bounds_m'],rtol=1e-7,atol=1e-9),'GPU 导入后的尺寸或单位发生变化。')
            imported_bounds=final_bounds
            state['imported_bounds_m']=final_bounds.tolist()
        state['solver_bounds_m'] = final_bounds.tolist()
        # Conversion constructs dual boundary faces and can slightly change the
        # polygonal approximation. Verify import units exactly before conversion;
        # then independently bound the remeshed extents, areas and volume.
        length = np.ptp(np.asarray(imported_bounds),axis=0)
        state['relative_bounds_change_after_conversion'] = float(np.max(abs(final_bounds-imported_bounds)/length))
        require(state['relative_bounds_change_after_conversion']<.01,'多面体转换后的外形边界变化超过 1%。')
        integrals = session.settings.results.report.surface_integrals
        state['boundary_area_m2'] = {role:float(integrals.get_area(surface_names=[role])[role]) for role in settings['boundaries']}
        state['relative_boundary_area_errors'] = {}
        for role,area in state['boundary_area_m2'].items():
            relative_error = abs(area/(payload['domain']['boundary_area_mm2'][role]*1e-6)-1)
            state['relative_boundary_area_errors'][role] = relative_error
        state['boundary_integral_volume_m3'] = case_volume(directory/'configured.cas.h5')
        volumes = re.findall(r'total volume \(m3\):\s*([\d.eE+-]+)',(directory/'fluent.trn').read_text(encoding='utf-8',errors='replace'))
        require(bool(volumes),'Fluent 未返回可核实的流体总体积。')
        state['solver_volume_m3'] = float(volumes[-1])
        require(math.isclose(state['boundary_integral_volume_m3'],state['solver_volume_m3'],rel_tol=.001),
                '保存网格的边界体积积分与 Fluent 体积差超过 0.1%。')
        state['relative_CAD_volume_error'] = abs(state['solver_volume_m3']/(payload['domain']['volume_mm3']*1e-9)-1)
        save(directory/'mesh_check.json',state)
        require(max(state['relative_boundary_area_errors'].values())<.02,'Fluent 边界面积与 CAD 面积差超过 2%，请减小入口网格尺寸后重新生成。')
        require(state['relative_CAD_volume_error']<.01,'Fluent 流体体积与 CAD 体积差超过 1%。')
        state['solver_opening_counts'] = case_openings(directory/'configured.cas.h5')
        if device=='gpu':state['imported_opening_counts']=state['solver_opening_counts']
        require(state['solver_opening_counts']==expected_patches,'多面体转换后实际连通开口数量改变，已停止求解。')
        interior_sampler=outlet_sampler=None
        if verification:
            progress('正在建立共同物理位置的内部场采样与保存网格体积矩复核')
            interior_sampler=prepare_interior(directory/'configured.cas.h5',payload['domain'])
            require(math.isclose(interior_sampler['volume_m3'],state['solver_volume_m3'],rel_tol=.001),'保存单元体积矩与 Fluent 总体积不一致。')
            state['interior_sampling_volume_m3']=interior_sampler['volume_m3']
            outlet_sampler=prepare_outlet(directory/'configured.cas.h5',payload['domain'])
        session.settings.solution.initialization.hybrid_initialize()
        if diagnostic:
            verify_transport(snapshot(session),config)
            profile=dict(compute_device=device,transport_model=transport,precision='double',
                validation_only=True,formal_physics_validated=False,cpu_fallback=False,
                warped_face_gradient_correction=False,residual_scaling='local',
                original_transport_model='ideal-gas-mixing-law',iterations=config['iterations'],
                case_write_strategy='reuse_static_configured_case_after_settings_check',
                species_solver_controls=solver_controls,
                initialization='fresh hybrid initialization',iteration_wall_seconds=[])
            if device=='gpu':
                transcript=(directory/'fluent.trn').read_text(encoding='utf-8',errors='replace')
                require('Preparing GPU solver' in transcript and 'NVIDIA' in transcript,'尚未取得 Fluent 原生 GPU 初始化证据，停止接入验证。')
                profile['native_gpu_initialized']=True
            save(directory/'execution_profile.json',profile)
        state['fluent_executed'] = True
        state['fluent_version'] = str(session.get_fluent_version())
        previous,end = None,0
        residual_history = []
        while end<config['iterations']:
            requested = config['iterations']-end if diagnostic else min(BLOCK_SIZE,config['iterations']-end)
            progress(f'正在计算冷流：{end} / {config["iterations"]} 步')
            calculation_started=time.monotonic()
            if diagnostic:save(directory/'progress.json',dict(message=f'正在进行 {device.upper()} 接入短测：{end} / {config["iterations"]} 步',
                compute_started_monotonic=calculation_started,cpu_compute_limit_seconds=60 if device=='cpu' else None))
            session.settings.solution.run_calculation.iterate(iter_count=requested)
            if diagnostic:
                profile['iteration_wall_seconds'].append(dict(iterations=requested,seconds=time.monotonic()-calculation_started))
                save(directory/'execution_profile.json',profile)
                progress('接入短测计算结束，正在保存并核对结果')
            if diagnostic:save_validation_data(session,directory,configured)
            else:session.settings.file.write_case_data(file_name='final.cas.h5')
            actual = saved_iteration(directory/'final.dat.h5')
            require(actual==end+requested,'Fluent 实际步数与请求不符，已停止。')
            rows = parse_residual_history((directory/'fluent.trn').read_text(encoding='utf-8',errors='replace'),end,actual)
            residual_history.extend(dict(iteration=i,**rows[i]) for i in sorted(rows))
            point = block_reports(session,settings,previous)
            point.update(iteration=actual,residuals=rows[actual],block_steps=requested,
                block_maximum_residuals={name:max(row[name] for row in rows.values()) for name in EQUATIONS})
            if verification:
                profile=sample_outlet(directory/'final.dat.h5',outlet_sampler)
                point['outlet_profile']=profile
                old=state['history'][-1].get('outlet_profile') if state['history'] else None
                drift=None
                if old:
                    a,b=np.array(profile['values']),np.array(old['values'])
                    drift=max(float(np.linalg.norm(a[:,part]-b[:,part])/max(np.linalg.norm(a[:,part]),1e-12))
                              for part in (slice(0,3),slice(3,4)))
                point['outlet_profile_relative_drift']=drift
                state['outlet_profile']=profile
                interior=sample_interior(directory/'final.dat.h5',interior_sampler)
                point['interior_profile']=interior
                old=state['history'][-1].get('interior_profile') if state['history'] else None
                drift=None
                if old:
                    a,b=np.array(interior['values']),np.array(old['values'])
                    drift=max(float(np.linalg.norm(a[:,part]-b[:,part])/max(np.linalg.norm(a[:,part]),1e-12))
                              for part in (slice(0,3),slice(3,4)))
                point['interior_profile_relative_drift']=drift
                state['interior_profile']=interior
            point['criteria_met'] = all(item['passed'] for item in checks(point,criteria))
            state['history'].append(point)
            state['convergence_assessment'] = assessment(state['history'],criteria,
                minimum_iterations=200 if verification else 100,consecutive_blocks=3 if verification else 2)
            state['numerical_criteria_met'] = state['convergence_assessment']['numerical_criteria_met']
            state['iterations_completed'] = actual
            save(directory/'partial.json',state)
            previous,end = point,actual
            tracker.capture(session)
            if state['numerical_criteria_met']:
                break
        state['status'] = 'numerical_criteria_met' if state['numerical_criteria_met'] else 'iteration_budget_reached'
        if diagnostic:
            state.update(status='gpu_validation_completed' if device=='gpu' else 'cpu_validation_completed',
                numerical_criteria_met=False,execution_profile=profile)
        state['iterations_completed'] = end
        final_settings=snapshot(session)
        verify_physics(final_settings,settings,laminar=True)
        verify_transport(final_settings,config)
        verify_methods(final_settings)
        species_controls.verify_live(session,solver_controls)
        health=field_health.inspect(directory)
        save(directory/'field_health.json',health)
        state['field_health']=health
        field_health.require_healthy(health)
        state['saved_data_crosscheck'] = verify_saved_fluxes(directory,state['history'][-1])
        if verification:
            save(directory/'outlet_profile.json',state['outlet_profile'])
            save(directory/'interior_profile.json',state['interior_profile'])
        with (directory/'residuals.csv').open('w',encoding='utf-8',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=['iteration',*EQUATIONS])
            writer.writeheader()
            writer.writerows(residual_history)
        with (directory/'history.csv').open('w',encoding='utf-8',newline='') as stream:
            names = ['iteration',*EQUATIONS,'relative_mass_imbalance','h2_relative_convective_imbalance','relative_pressure_drop_change']
            writer = csv.DictWriter(stream,fieldnames=names)
            writer.writeheader()
            for point in state['history']:
                writer.writerow({key:point[key] for key in names if key not in EQUATIONS}|point['residuals'])
    finally:
        try:
            tracker.capture(session)
            if session is not None:
                session.settings.file.stop_transcript()
                session.exit(timeout=10,wait=10)
        finally:
            cleanup = cleanup_records(list(tracker.owned.values()))
            save(directory/'cleanup.json',cleanup)
    require(cleanup['all_tracked_processes_exited'],'本次 Fluent 进程退出尚未核实。')
    if verification:
        require(mesh_fingerprint(directory/'configured.cas.h5')==mesh_fingerprint(directory/'final.cas.h5'),
                '求解期间网格发生变化，缓存的共同采样几何已失效。')
    state['cleanup'] = cleanup
    return state
