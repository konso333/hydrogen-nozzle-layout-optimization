"""Bounded single-component air run, separate from H2/species qualification."""
import csv,math,time
from pathlib import Path
from coldflow_trial.processes import OwnedProcesses
from coldflow_trial.solver import snapshot,require,mesh_fingerprint,_constant
from nozzle_platform import air_model as air
from nozzle_platform.workflows import save
from nozzle_platform.solver_device import launch
from nozzle_platform.flow_solver import close_flow_session
from nozzle_platform.flow_validation import case_openings,case_volume
from nozzle_platform.mesh_quality import condition_mesh

def configure(session,config):
    setup=session.settings.setup;props=air.properties(config['temperature_K'])
    setup.general.solver.type='pressure-based';setup.general.solver.time='steady';setup.general.operating_conditions.operating_pressure=101325.
    setup.models.species.model.option='off';setup.models.energy.enabled=False;setup.models.viscous.model='laminar'
    material=setup.materials.fluid['air'];material.density.option='value';material.density.value=props['density_kg_m3']
    material.viscosity.option='value';material.viscosity.value=props['viscosity_Pa_s']
    for name in setup.cell_zone_conditions.fluid.get_object_names():setup.cell_zone_conditions.fluid[name].general.material='air'
    bc=setup.boundary_conditions
    for role,key in [('fuel_inlet','fuel_velocity_m_s'),('oxidizer_inlet','oxidizer_velocity_m_s')]:
        inlet=bc.velocity_inlet[role];inlet.momentum.velocity_specification_method='Magnitude, Normal to Boundary';inlet.momentum.velocity_magnitude=config[key]
    bc.pressure_outlet['outlet'].momentum.gauge_pressure=0.
    methods=session.settings.solution.methods;methods.p_v_coupling.flow_scheme='SIMPLE';methods.warped_face_gradient_correction.enable=False
    methods.spatial_discretization.gradient_scheme='least-square-cell-based';methods.spatial_discretization.discretization_scheme['mom']='second-order-upwind';methods.spatial_discretization.discretization_scheme['pressure']='second-order'
    return props

def verify(configured,config):
    props=air.properties(config['temperature_K']);models=configured['models'];material=configured['materials']['fluid']['air']
    require(configured['general']['operating_conditions']['operating_pressure']==props['operating_pressure_Pa'],'实际工作压力与空气模板不符。')
    require(models['species']['model']['option']=='off' and models['energy']['enabled'] is False and models['viscous']['model']=='laminar','Fluent 未实际关闭组分／能量方程，或层流设置不同。')
    require(models['multiphase']['model']=='none' and models['radiation']['model']=='none' and configured['general']['operating_conditions']['gravity']['enable'] is False,'空气首版需要单相、无辐射、无重力模型。')
    for name,target in [('density',props['density_kg_m3']),('viscosity',props['viscosity_Pa_s'])]:
        require(material[name]['option']=='value' and math.isclose(material[name]['value'],target,rel_tol=1e-12),'Fluent 实际空气常物性与声明不符。')
    require(all(z['general']['material']=='air' for z in configured['cell_zones']['fluid'].values()),'存在未分配空气的流体区。')
    for role,key in [('fuel_inlet','fuel_velocity_m_s'),('oxidizer_inlet','oxidizer_velocity_m_s')]:
        bc=configured['boundary_conditions']['velocity_inlet'][role]['momentum'];require(bc['velocity_specification_method']=='Magnitude, Normal to Boundary','实际空气入口方向设置与输入不符。')
        _constant(bc['velocity_magnitude'],config[key],role+' air velocity')
    _constant(configured['boundary_conditions']['pressure_outlet']['outlet']['momentum']['gauge_pressure'],0.,'air outlet pressure')
    wall=configured['boundary_conditions']['wall']['wall']['momentum'];require(wall['wall_motion']=='Stationary Wall' and wall['shear_condition']=='No Slip','空气首版需要静止无滑移壁面。')
    schemes=configured['methods']['spatial_discretization']['discretization_scheme'];require(schemes['mom']=='second-order-upwind' and schemes['pressure']=='second-order','空气离散格式与模板不符。')

def run(payload,directory):
    import h5py,numpy as np
    config=air.normalize(payload['config']);device=config['compute_device'];props=air.properties(config['temperature_K'])
    tracker=OwnedProcesses(payload['fluent']['executable'],directory);session=None
    state=dict(physical_model=air.MODEL,model='single-component isothermal air; laminar constant-property incompressible',air_properties=props,
        scientific_eligible=False,formal_physics_validated=False,grid_independence_verified=False,chemical_reactions_enabled=False,species_transport=False,energy_equation=False,
        compute_device=device,transport_model=air.TRANSPORT,cpu_fallback=False,gpu_validation_only=False,equations=list(air.EQUATIONS),spatial_scheme='second-order-upwind',
        boundary_layers=payload['mesh'].get('boundary_layers','none'),history=[],numerical_criteria_met=False,convergence_criteria=air.CRITERIA,
        status='preparing',fluent_executed=False,initialization='fresh hybrid initialization',residual_scaling='local' if device=='gpu' else 'global',
        limitations=['单一空气、定温常物性层流筛选；不计算燃烧或组分性能','层流物理适用性和网格独立性未验证，不提供正式优选','中心供气通道与外围供气通道均为空气；fuel_inlet 是沿用的几何边界名称'])
    def progress(message,**extra):save(directory/'progress.json',dict(message=message,**extra))
    try:
        session=launch(dict(fluent=dict(payload['fluent'],processes=1),config=config),directory);tracker.capture(session)
        session.settings.file.start_transcript(file_name='fluent.trn');session.settings.file.read_mesh(file_name='flow.msh');session.settings.mesh.check()
        layered=payload['mesh'].get('mesh_method')=='layered-polyhex'
        state.update(condition_mesh(session,directory,progress,preserve_layers=layered,allow_conditioning=device!='gpu'))
        state['solver_cell_type']='mixed-polyhex' if layered else 'polyhedral' if state['polyhedral_conversion_performed'] else 'tetrahedral'
        configure(session,config);configured=snapshot(session);save(directory/'configured.json',configured);verify(configured,config)
        monitor=session.settings.solution.monitor.residual
        require(set(monitor.equations.get_object_names())==set(air.EQUATIONS),'空气活跃方程与模板不同。')
        monitor.options.normalize=False;monitor.options.residual_values.scale_residuals=True;monitor.options.residual_values.compute_local_scale=device=='gpu';monitor.options.print=True;monitor.options.plot=False
        for name in air.EQUATIONS:monitor.equations[name].monitor=True;monitor.equations[name].check_convergence=False
        session.settings.file.write_case(file_name='configured.cas.h5')
        with h5py.File(directory/'configured.cas.h5') as case:
            p=case['meshes/1/nodes/coords/1'][()];top=case['meshes/1/cells/zoneTopology'];state['solver_cell_count']=int(np.sum(top['maxId'][()]-top['minId'][()]+1))
        bounds=np.array([p.min(axis=0),p.max(axis=0)]);expected=np.array(payload['mesh']['bounds_m']);state['solver_bounds_m']=bounds.tolist()
        require(np.allclose(bounds,expected,rtol=1e-7,atol=1e-9) if device=='gpu' else np.max(abs(bounds-expected)/np.ptp(expected,axis=0))<.01,'空气求解导入后单位或外形边界发生变化。')
        openings=case_openings(directory/'configured.cas.h5');required={k:payload['domain']['boundary_patch_counts'][k] for k in ('fuel_inlet','oxidizer_inlet','outlet')}
        require(openings==required,'实际连通入口／出口数量与模型不符。');state['solver_opening_counts']=openings
        geom=air.saved_geometry(directory/'configured.cas.h5');state['boundary_area_m2']={role:float(geom['areas'][a:b].sum()) for role,(a,b) in geom['ranges'].items()}
        state['relative_boundary_area_errors']={role:abs(area/(payload['domain']['boundary_area_mm2'][role]*1e-6)-1) for role,area in state['boundary_area_m2'].items()}
        require(max(state['relative_boundary_area_errors'].values())<.02,'保存边界面积与 CAD 差超过 2%。')
        volume=case_volume(directory/'configured.cas.h5');state['boundary_integral_volume_m3']=volume;state['relative_CAD_volume_error']=abs(volume/(payload['domain']['volume_mm3']*1e-9)-1)
        require(state['relative_CAD_volume_error']<.01,'保存流体体积与 CAD 差超过 1%。')
        text=(directory/'fluent.trn').read_text(encoding='utf-8',errors='replace');volumes=air.re.findall(r'total volume \(m3\):\s*([\d.eE+-]+)',text)
        require(bool(volumes) and math.isclose(volume,float(volumes[-1]),rel_tol=.001),'独立边界体积积分与 Fluent 体积不一致。')
        save(directory/'air_model.json',dict(physical_model=air.MODEL,properties=props,conditions=config,active_equations=list(air.EQUATIONS),assumptions=state['limitations']))
        session.settings.solution.initialization.hybrid_initialize();state.update(fluent_executed=True,fluent_version=str(session.get_fluent_version()))
        if device=='gpu':
            text=(directory/'fluent.trn').read_text(encoding='utf-8',errors='replace');require('Preparing GPU solver' in text and 'NVIDIA' in text,'未取得原生 GPU 初始化证据。')
        end=0;history=[];cpu_started=None;execution=dict(compute_device=device,physical_model=air.MODEL,iteration_wall_seconds=[],cpu_compute_limit_seconds=60,cpu_fallback=False)
        while end<config['iterations']:
            requested=min(25,config['iterations']-end);start=time.monotonic()
            if device=='cpu' and cpu_started is None:cpu_started=start
            progress(f'正在计算单一空气冷流：{end} / {config["iterations"]} 步',compute_started_monotonic=cpu_started if device=='cpu' else None,cpu_compute_limit_seconds=60 if device=='cpu' else None)
            session.settings.solution.run_calculation.iterate(iter_count=requested);elapsed=time.monotonic()-start
            execution['iteration_wall_seconds'].append(dict(requested_iterations=requested,seconds=elapsed));save(directory/'execution_profile.json',execution)
            progress('正在保存并独立核查空气流场')
            verify(snapshot(session),config)
            session.settings.file.write_case(file_name='final.cas.h5');session.settings.file.write_data(file_name='final.dat.h5')
            actual=air.iteration_count(directory/'final.dat.h5');require(end<actual<=end+requested,'空气实际迭代计数不在请求范围内。')
            rows=air.residuals((directory/'fluent.trn').read_text(encoding='utf-8',errors='replace'),end,actual);history.extend(dict(iteration=i,**rows[i]) for i in sorted(rows))
            point=air.saved_point(directory/'final.cas.h5',directory/'final.dat.h5',config,state['history'][-1] if state['history'] else None,geom)
            point.update(iteration=actual,block_steps=actual-end,residuals=rows[actual],block_maximum_residuals={n:max(v[n] for v in rows.values()) for n in air.EQUATIONS})
            pending=air.assessment([*state['history'],point]);point['criteria_met']=all(r['passed'] for r in pending['checks'])
            state['history'].append(point);state['convergence_assessment']=air.assessment(state['history']);state['numerical_criteria_met']=state['convergence_assessment']['numerical_criteria_met'];state['iterations_completed']=actual
            state['field_health']=dict(physical_model=air.MODEL,passed=True,scope='complete finite cell U/V/W/P; boundary SV_FLUX/U/V/W/P; optional stored cell constant density; no uncomputed species/temperature field',inlet_mass_crosscheck=point['inlet_mass_crosscheck'])
            save(directory/'field_health.json',state['field_health']);save(directory/'partial.json',state)
            tracker.capture(session);short=actual<end+requested;end=actual
            if short:
                text=(directory/'fluent.trn').read_text(encoding='utf-8',errors='replace');require(f'!    {actual} solution is converged' in text,'空气求解发生无法解释的提前停止。');state['native_early_stop']=True
            if short or state['numerical_criteria_met']:break
        require(mesh_fingerprint(directory/'configured.cas.h5')==mesh_fingerprint(directory/'final.cas.h5'),'空气求解期间网格发生变化。')
        state.update(iterations_completed=end,status='numerical_criteria_met' if state['numerical_criteria_met'] else 'iteration_budget_reached',execution_profile=execution,
            saved_data_crosscheck=dict(checked=True,source='saved boundary mass flux and independent rho*U*A inlet check; no species',maximum_inlet_relative_error=max(r['relative_error'] for r in state['history'][-1]['inlet_mass_crosscheck'].values())))
        with (directory/'residuals.csv').open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['iteration',*air.EQUATIONS]);w.writeheader();w.writerows(history)
        with (directory/'history.csv').open('w',encoding='utf-8',newline='') as f:
            keys=['iteration',*air.EQUATIONS,'relative_mass_imbalance','relative_pressure_drop_change'];w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
            for p in state['history']:w.writerow({n:p[n] for n in keys if n not in air.EQUATIONS}|p['residuals'])
    finally:state['cleanup']=close_flow_session(session,tracker,directory)
    return state
