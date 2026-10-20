"""Fresh-mesh, bounded nonreacting H2/air engineering trial using Fluent 25.2."""

import csv
import math
from pathlib import Path
import re

from coldflow_trial.config import EQUATIONS
from coldflow_trial.processes import OwnedProcesses, cleanup_records
from coldflow_trial.solver import (launch_session, snapshot, verify_physics, parse_residuals,
                                  block_reports, saved_iteration, passes, require)
from nozzle_platform.workflows import save
from nozzle_platform.flow_validation import case_openings, case_volume, verify_saved_fluxes


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
    expected = {'fuel_velocity_m_s':config['fuel_velocity_m_s'],'oxidizer_velocity_m_s':config['oxidizer_velocity_m_s'],
        'fuel_temperature_K':config['temperature_K'],'oxidizer_temperature_K':config['temperature_K'],
        'oxidizer_o2_mass_fraction':config['oxidizer_o2_mass_fraction'],'outlet_gauge_pressure_Pa':0.,
        'backflow_temperature_K':config['temperature_K'],'operating_pressure_Pa':101325.}
    settings = {'fluent':payload['fluent'],'expected_conditions':expected,
        'boundaries':{k:k for k in ('fuel_inlet','oxidizer_inlet','outlet','wall')}}
    criteria = {'scaled_residuals':{name:1e-6 if name=='energy' else 1e-4 for name in EQUATIONS},
        'relative_mass_imbalance':.005,'h2_relative_convective_imbalance':.005,'relative_pressure_drop_change':.01}
    tracker = OwnedProcesses(payload['fluent']['executable'],directory)
    session = None
    state = {'fluent_executed':False,'scientific_eligible':False,'chemical_reactions_enabled':False,
        'initialization':'fresh hybrid initialization','model':'steady laminar nonreacting H2/O2/H2O/N2',
        'spatial_scheme':'first-order-upwind','boundary_layers':'none','convergence_criteria':criteria,
        'history':[],'numerical_criteria_met':False,'status':'preparing'}
    cleanup = None
    def progress(message):
        save(directory/'progress.json',{'message':message})
    try:
        session = launch_session(settings,directory)
        tracker.capture(session)
        # Relative solver file names avoid Unicode filename issues in Fluent/HDF5.
        session.settings.file.start_transcript(file_name='fluent.trn')
        session.settings.file.read_mesh(file_name='flow.msh')
        session.settings.mesh.check()
        session.settings.file.write_case(file_name='imported.cas.h5')
        with h5py.File(directory/'imported.cas.h5','r') as case:
            points = case['meshes/1/nodes/coords/1'][()]
        imported_bounds = [points.min(axis=0),points.max(axis=0)]
        require(np.allclose(imported_bounds,payload['mesh']['bounds_m'],rtol=1e-7,atol=1e-9),'Fluent 导入后的尺寸或单位发生变化。')
        state['imported_bounds_m'] = np.asarray(imported_bounds).tolist()
        expected_patches = {k:payload['domain']['boundary_patch_counts'][k] for k in ('fuel_inlet','oxidizer_inlet','outlet')}
        state['imported_opening_counts'] = case_openings(directory/'imported.cas.h5')
        require(state['imported_opening_counts']==expected_patches,'Fluent 导入后实际连通开口数量改变。')
        session.settings.mesh.quality()
        state['imported_tetra_minimum_orthogonal_quality'] = quality_from_transcript(directory/'fluent.trn')
        state['polyhedral_conversion_performed'] = False
        state['minimum_fluent_orthogonal_quality'] = state['imported_tetra_minimum_orthogonal_quality']
        state['interior_improvement_passes'] = 0
        def improve_interior():
            session.tui.mesh.repair_improve.allow_repair_at_boundaries('no')
            session.tui.mesh.repair_improve.include_local_polyhedra_conversion_in_repair('yes' if state['polyhedral_conversion_performed'] else 'no')
            state['interior_improvement_passes'] += 1
            progress(f'正在进行第 {state["interior_improvement_passes"]} 次内部网格改善，不移动边界')
            session.tui.mesh.repair_improve.improve_quality()
            session.settings.mesh.check()
            session.settings.mesh.quality()
            state['minimum_fluent_orthogonal_quality'] = quality_from_transcript(directory/'fluent.trn')
        while state['minimum_fluent_orthogonal_quality']<.05 and state['interior_improvement_passes']<2:
            improve_interior()
        if state['minimum_fluent_orthogonal_quality'] < .05:
            progress('四面体正交质量不足，正在转换多面体并重新检查边界')
            session.settings.mesh.polyhedra.convert_domain()
            state['polyhedral_conversion_performed'] = True
            state['polyhedral_conversion_type'] = 'whole_domain'
            session.settings.mesh.check()
            session.settings.mesh.quality()
        state['minimum_fluent_orthogonal_quality'] = quality_from_transcript(directory/'fluent.trn')
        while state['minimum_fluent_orthogonal_quality']<.05 and state['interior_improvement_passes']<6:
            improve_interior()
        require(state['minimum_fluent_orthogonal_quality']>=.05,'Fluent 最小正交质量低于 0.05；已停止求解，请调整网格。')
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
        mix.viscosity.option = 'ideal-gas-mixing-law'
        mix.thermal_conductivity.option = 'ideal-gas-mixing-law'
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
        methods.spatial_discretization.gradient_scheme = 'least-square-cell-based'
        for name in ('mom','temperature','species-0','species-1','species-2'):
            methods.spatial_discretization.discretization_scheme[name] = 'first-order-upwind'
        configured = snapshot(session)
        verify_physics(configured,settings,laminar=True)
        save(directory/'configured.json',configured)
        monitor = session.settings.solution.monitor.residual
        require(set(monitor.equations.get_object_names())==set(EQUATIONS),'Fluent 活跃方程与冷流模板不符。')
        monitor.options.normalize = False
        monitor.options.residual_values.scale_residuals = True
        monitor.options.residual_values.compute_local_scale = False
        monitor.options.print = True
        monitor.options.plot = False
        for name in EQUATIONS:
            monitor.equations[name].monitor = True
            monitor.equations[name].check_convergence = False
        session.settings.file.write_case(file_name='configured.cas.h5')
        with h5py.File(directory/'configured.cas.h5','r') as case:
            points = case['meshes/1/nodes/coords/1'][()]
        final_bounds = np.array([points.min(axis=0),points.max(axis=0)])
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
        require(state['solver_opening_counts']==expected_patches,'多面体转换后实际连通开口数量改变，已停止求解。')
        session.settings.solution.initialization.hybrid_initialize()
        state['fluent_executed'] = True
        state['fluent_version'] = str(session.get_fluent_version())
        previous,consecutive,end = None,0,0
        while end<config['iterations']:
            requested = min(25,config['iterations']-end)
            progress(f'正在计算冷流：{end} / {config["iterations"]} 步')
            session.settings.solution.run_calculation.iterate(iter_count=requested)
            session.settings.file.write_case_data(file_name='final.cas.h5')
            actual = saved_iteration(directory/'final.dat.h5')
            require(actual==end+requested,'Fluent 实际步数与请求不符，已停止。')
            residuals = parse_residuals((directory/'fluent.trn').read_text(encoding='utf-8',errors='replace'),end,actual)
            point = block_reports(session,settings,previous)
            point.update(iteration=actual,residuals=residuals)
            point['criteria_met'] = passes(point,criteria)
            state['history'].append(point)
            save(directory/'partial.json',state)
            consecutive = consecutive+1 if point['criteria_met'] else 0
            previous,end = point,actual
            tracker.capture(session)
            if end>=100 and consecutive>=2:
                state['numerical_criteria_met'] = True
                break
        state['status'] = 'numerical_criteria_met' if state['numerical_criteria_met'] else 'iteration_budget_reached'
        state['iterations_completed'] = end
        verify_physics(snapshot(session),settings,laminar=True)
        state['saved_data_crosscheck'] = verify_saved_fluxes(directory,state['history'][-1])
        with (directory/'history.csv').open('w',encoding='utf-8',newline='') as stream:
            names = ['iteration',*EQUATIONS,'relative_mass_imbalance','h2_relative_convective_imbalance','relative_pressure_drop_change']
            writer = csv.DictWriter(stream,fieldnames=names)
            writer.writeheader()
            for point in state['history']:
                writer.writerow({key:point[key] for key in names if key not in EQUATIONS}|point['residuals'])
    finally:
        tracker.capture(session)
        if session is not None:
            try:
                session.settings.file.stop_transcript()
            finally:
                session.exit(timeout=10,wait=10)
        cleanup = cleanup_records(list(tracker.owned.values()))
        save(directory/'cleanup.json',cleanup)
    require(cleanup['all_tracked_processes_exited'],'本次 Fluent 进程退出尚未核实。')
    state['cleanup'] = cleanup
    return state
