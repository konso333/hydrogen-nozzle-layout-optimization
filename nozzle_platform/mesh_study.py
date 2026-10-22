"""Serial three-grid pressure sensitivity study with explicit evidence limits."""

import copy
import csv
import math
import threading
import time
import uuid

from validation import require_finite
from nozzle_platform.workflows import normalize_mesh, normalize_flow, save, sha256, IMPLEMENTATION, ROOT
from nozzle_platform.grid_convergence import assess_profiles, richardson


LEVELS = ('coarse', 'medium', 'fine')
MINIMUM_EFFECTIVE_RATIO = 1.1
STUDY_SECONDS = 7200


def normalize(request):
    required = {'domain_id','scheme_id','mesh','flow','refinement_ratio','tolerance_percent'}
    if not isinstance(request,dict) or not required<=set(request) or set(request)-required-{'refinement_mode'}:
        raise ValueError('三档对比参数缺失或包含未知字段。')
    if not isinstance(request['mesh'],dict) or not isinstance(request['flow'],dict):
        raise ValueError('请提供网格和冷流参数。')
    if (request['flow'].get('convergence_mode')=='gpu-validation' or request['flow'].get('compute_device','cpu')!='cpu'
            or request['flow'].get('transport_model','ideal-gas-mixing-law')!='ideal-gas-mixing-law'):
        raise ValueError('GPU 接入短测与兼容物性尚未用于正式三档网格验证。')
    if set(request['mesh']) & {'domain_id','scheme_id'} or set(request['flow']) & {'domain_id','scheme_id','mesh_id'}:
        raise ValueError('三档对比只使用同一个流体域和方案。')
    ids = {key:request[key] for key in ('domain_id','scheme_id')}
    mesh = normalize_mesh(dict(request['mesh'],**ids))
    mode=request.get('refinement_mode','chamber')
    if not isinstance(mode,str) or mode not in {'chamber','uniform'}:
        raise ValueError('请选择整体细化或公共腔敏感性模式。')
    flow_request=dict(request['flow'],**ids,mesh_id='planned')
    if mode=='uniform':flow_request['convergence_mode']='grid-verification'
    flow = normalize_flow(flow_request)
    if (flow.get('convergence_mode')=='gpu-validation' or flow.get('compute_device','cpu')!='cpu'
            or flow.get('transport_model','ideal-gas-mixing-law')!='ideal-gas-mixing-law'):
        raise ValueError('GPU 接入短测与兼容物性尚未用于正式三档网格验证。')
    if flow['iterations'] < 100:
        raise ValueError('三档对比每档至少允许计算 100 步。')
    if mode=='uniform' and flow['iterations']<200:
        raise ValueError('整体细化验证每档至少允许计算 200 步。')
    ratio = require_finite(request['refinement_ratio'],'refinement_ratio')
    tolerance = require_finite(request['tolerance_percent'],'tolerance_percent')
    if not 1.2 <= ratio <= 2:
        raise ValueError('相邻网格尺寸倍率需在 1.2～2 之间。')
    if not .1 <= tolerance <= 5:
        raise ValueError('压降变化容许值需在 0.1%～5% 之间。')
    plan = []
    for level,factor in zip(LEVELS,(ratio**2,ratio,1.)):
        scaled = dict(mesh)
        key='size_factor' if mode=='uniform' else 'max_size_mm'
        scaled[key] = round(mesh[key]*factor,10)
        # Uniform mode multiplies the complete Gmsh size field, including
        # curvature, by one factor. Physical refinement regions stay fixed.
        # Chamber mode is retained for replaying the earlier sensitivity study.
        plan.append(dict(level=level,factor=factor,mesh=normalize_mesh(scaled)))
    return dict(**ids,mesh=mesh,flow=flow,refinement_mode=mode,refinement_ratio=ratio,tolerance_percent=tolerance,plan=plan)


def layered_mesh_preflight(meshes):
    """Screen actual native meshes before spending three solver budgets."""
    result=dict(passed=False,effective_refinement_ratios=[],reasons=[],
        scope='原生壁面层网格预检；最终仍须核对实际求解网格及收敛结果')
    if not isinstance(meshes,list) or len(meshes)!=3 or any(mesh is None for mesh in meshes):
        result['reasons'].append('三档壁面层网格尚未全部通过，暂不开始求解。');return result
    try:
        for key in ['implementation_sha256','domain_artifact_sha256','source_geometry_sha256']:
            if not meshes[0][key] or any(m[key]!=meshes[0][key] for m in meshes):
                result['reasons'].append('原生网格的代码、流体域或固定几何表面不一致。');return result
        local=meshes[0].get('local_volume_refinement',False)
        if any(m.get('local_volume_refinement',False)!=local for m in meshes):raise ValueError('mixed refinement scope')
        if local:
            geometry=lambda mesh:[{k:r[k] for k in ['name','lower_mm','upper_mm']} for r in mesh['volume_refinement_regions']]
            reference=geometry(meshes[0])
            if not reference or any(geometry(m)!=reference for m in meshes):raise ValueError('different refinement boxes')
            for mesh in meshes:
                summaries=mesh['volume_refinement_summary']
                if len(summaries)!=len(reference):raise ValueError('missing measured local resolution')
                for region,summary in zip(mesh['volume_refinement_regions'],summaries):
                    target=region['target_size_mm'];mean=summary['volume_weighted_equivalent_h_mm']
                    if (summary['name']!=region['name'] or type(summary['cells']) is not int or summary['cells']<1 or
                            not math.isfinite(target) or target<=0 or not math.isfinite(mean) or not 0<mean<=2*target*(1+1e-10)):
                        raise ValueError('invalid measured local resolution')
        if any(m['mesh_method']!='layered-polyhex' or m['cell_type']!='mixed-polyhex' or
                type(m['cells']) is not int or m['cells']<1 or not math.isfinite(m['volume_m3']) or m['volume_m3']<=0 or
                not math.isfinite(m['minimum_fluent_orthogonal_quality']) or not .1<=m['minimum_fluent_orthogonal_quality']<=1 or
                not math.isfinite(m['maximum_fluent_aspect_ratio']) or not 1<=m['maximum_fluent_aspect_ratio']<=100 for m in meshes):
            raise ValueError('invalid native mesh')
        sizes=[(m['volume_m3']/m['cells'])**(1/3) for m in meshes]
        ratios=[sizes[i]/sizes[i+1] for i in range(2)];result['effective_refinement_ratios']=ratios
        result['passed']=all(r>=MINIMUM_EFFECTIVE_RATIO for r in ratios)
        if not result['passed']:result['reasons'].append('原生壁面层网格的相邻有效细化比不足 1.1；请调整尺寸倍率后重新生成，暂不开始求解。')
    except (KeyError,TypeError,ValueError,OverflowError):
        result['reasons'].append('原生网格预检数据缺失或无效，暂不开始求解。')
    return result


def compare(rows, tolerance_percent, refinement_mode='chamber'):
    """Report scoped pressure and sampled-field evidence with explicit limits."""
    result = dict(status='incomplete',pressure_sensitivity_passed=False,
        scoped_grid_convergence_passed=False,refinement_mode=refinement_mode,
        grid_independence_verified=False,scientific_eligible=False,recommended_level=None,
        tolerance_percent=tolerance_percent,minimum_effective_ratio=MINIMUM_EFFECTIVE_RATIO,
        effective_refinement_ratios=[],metrics={},reasons=[],gci=None,
        scope=('同一尺寸场整体细化；本工况两路压降、两个内部截面与出口共同采样点的速度和氢气分布，不代表所有空间位置或燃烧工况'
               if refinement_mode=='uniform' else
               '固定小孔入口和内部流道分辨率，改变公共腔网格；仅对两路总压差作敏感性检查，未验证全场独立性、渐近区间或 GCI'))
    if not isinstance(rows,list) or len(rows)!=3 or [r.get('level') for r in rows]!=list(LEVELS):
        result['reasons'].append('需要粗、中、细三档完整且顺序一致的结果。')
        return result
    if any(r.get('status')!='completed' or not r.get('numerical_criteria_met') or not r.get('saved_data_checked') for r in rows):
        result['reasons'].append('部分网格或求解未通过；请先处理失败或未收敛的档位。')
        return result
    required={'conditions','implementation_sha256','domain_artifact_sha256','cell_type','solver_cells',
              'solver_volume_m3','pressure_drop_Pa','pressure_drift_Pa'}
    if any(not required<=row.keys() for row in rows):
        result['status']='invalid_evidence';result['reasons'].append('三档结果缺少可复核字段。')
        return result
    if any(r['conditions']!=rows[0]['conditions'] or r['implementation_sha256']!=rows[0]['implementation_sha256']
           or r['domain_artifact_sha256']!=rows[0]['domain_artifact_sha256'] for r in rows):
        result['reasons'].append('三档的工况、实现或流体域不一致，不能作受控对比。')
        return result
    if len({r['cell_type'] for r in rows})!=1:
        result['reasons'].append('三档最终单元类型不同，不能仅归因于网格尺寸。')
        return result
    try:
        for row in rows:
            if (type(row['solver_cells']) is not int or row['solver_cells']<1
                    or not math.isfinite(row['solver_volume_m3']) or row['solver_volume_m3']<=0):
                raise ValueError('invalid solver size')
        sizes=[(r['solver_volume_m3']/r['solver_cells'])**(1/3) for r in rows]
        ratios=[sizes[i]/sizes[i+1] for i in range(2)]
        result['effective_refinement_ratios']=ratios
        separation=all(r>=MINIMUM_EFFECTIVE_RATIO for r in ratios)
        result['refinement_separation_met']=separation
        if not separation:
            result['reasons'].append('实际求解网格的相邻有效细化比不足 1.1，证据不足；请增大尺寸倍率。')
        checks=[]
        for role in ('fuel_inlet','oxidizer_inlet'):
            values=[r['pressure_drop_Pa'][role] for r in rows]
            if not all(math.isfinite(v) for v in values) or min(abs(v) for v in values)<1e-6:
                raise ValueError('zero or invalid pressure')
            changes=[abs(values[i]-values[i+1])/abs(values[i+1])*100 for i in range(2)]
            drifts=[r['pressure_drift_Pa'][role] for r in rows]
            if not all(math.isfinite(d) and d>=0 for d in drifts):
                raise ValueError('invalid iteration drift')
            drift=max(drifts)/abs(values[-1])*100
            if not math.isfinite(drift) or drift<0:
                raise ValueError('invalid iteration drift')
            passed=max(changes)<=tolerance_percent and drift<=tolerance_percent/10
            result['metrics'][role]=dict(values_Pa=values,adjacent_changes_percent=changes,
                iteration_drift_percent=drift,iteration_drift_limit_percent=tolerance_percent/10,passed=passed,
                trend='monotonic' if (values[1]-values[0])*(values[2]-values[1])>=0 else 'nonmonotonic')
            checks.append(passed)
        result['status']='pressure_sensitivity_passed' if separation and all(checks) else 'pressure_sensitivity_not_established'
        result['pressure_sensitivity_passed']=result['status']=='pressure_sensitivity_passed'
        if result['pressure_sensitivity_passed']:
            result['recommended_level']='medium'
        elif not all(checks):
            result['reasons'].append('相邻压降变化超出容许值，或迭代漂移相对容许值仍偏大。')
        if refinement_mode=='uniform':
            result['recommended_level']=None
            family=[dict(r.get('mesh_config',{})) for r in rows]
            factors=[f.pop('size_factor',None) for f in family]
            controlled=all(family) and all(f==family[0] for f in family) and all(isinstance(f,(int,float)) and math.isfinite(f) for f in factors) and factors[0]>factors[1]>factors[2]
            strict=all(r['conditions'].get('convergence_mode')=='grid-verification' for r in rows)
            result['uniform_family_checked']=bool(controlled and strict)
            result['outlet_fields']=assess_profiles(rows,tolerance_percent)
            result['interior_fields']=assess_profiles(rows,tolerance_percent,'interior_profile')
            gci={}
            for role,metric in result['metrics'].items():
                estimate=richardson(metric['values_Pa'],sizes)
                gaps=[abs(metric['values_Pa'][i]-metric['values_Pa'][i+1]) for i in range(2)]
                noise=[rows[i]['pressure_drift_Pa'][role]+rows[i+1]['pressure_drift_Pa'][role] for i in range(2)]
                estimate['iteration_noise_separated']=all(n<.1*g for n,g in zip(noise,gaps))
                estimate['passed']=bool(estimate['available'] and estimate['fine_gci_percent']<=tolerance_percent and estimate['iteration_noise_separated'])
                gci[role]=estimate
            result['gci']=gci
            accepted=bool(controlled and strict and result['pressure_sensitivity_passed'] and result['outlet_fields']['passed'] and result['interior_fields']['passed'] and all(g['passed'] for g in gci.values()))
            result['scoped_grid_convergence_passed']=accepted
            result['status']='scoped_grid_convergence_passed' if accepted else 'grid_convergence_not_established'
            if accepted:
                result['recommended_level']='fine'
            else:
                if not controlled or not strict:result['reasons'].append('缺少一致的整体细化族或严格迭代收敛记录。')
                if not result['outlet_fields']['passed']:result['reasons'].append('出口速度或氢气分布的共同采样对比未通过。')
                if not result['interior_fields']['passed']:result['reasons'].append('内部截面速度或氢气分布的共同采样对比未通过。')
                if not all(g['passed'] for g in gci.values()):result['reasons'].append('压降收敛阶、GCI 或迭代噪声分离未通过，需要继续细化或延长迭代。')
    except (KeyError,TypeError,ValueError,OverflowError):
        result['status']='invalid_evidence'
        result['reasons'].append('求解单元数、体积或压降数据无效，无法完成判定。')
    return result


def start(store, request):
    config=normalize(request)
    domain,_=store.domain(config['domain_id'],config['scheme_id'])
    if not store.capability()['fluent_available']:
        raise ValueError(store.capability()['note'])
    with store.lock:
        if store.active or store.reservation:
            raise ValueError('本机已有网格、求解或三档对比任务，请等待或停止。')
        study_id=uuid.uuid4().hex
        folder=store.root/('study_'+study_id);folder.mkdir()
        cancel=threading.Event()
        store.cancel_events[study_id]=cancel
        store.reservation=study_id
        store.jobs[study_id]=dict(job_id=study_id,kind='study',status='queued',result=None,
            domain_id=config['domain_id'],scheme_id=config['scheme_id'],message='等待三档网格对比',levels=[])
        try:
            save(folder/'plan.json',config)
            threading.Thread(target=run,args=(store,study_id,folder,config,domain,cancel),daemon=True).start()
        except Exception:
            store.reservation=None
            del store.jobs[study_id];del store.cancel_events[study_id]
            raise
        return copy.deepcopy(store.jobs[study_id])


def run(store, study_id, folder, config, domain, cancel):
    rows=[]
    started=time.monotonic()
    labels={'coarse':'粗网格','medium':'中网格','fine':'细网格'}
    candidate=None
    state='failed';message='三档对比未完成'
    flow_budget=config['flow'].get('time_budget_seconds',1800)
    total_budget=STUDY_SECONDS if flow_budget<=1800 else 3*(900+flow_budget)
    native_preflight=None
    def wait(child, level):
        while True:
            if time.monotonic()-started>total_budget:
                cancel.set()
            if cancel.is_set():
                with store.lock:
                    if store.active:
                        store.cancel_events[store.active].set()
            task=store.status(child['job_id'])
            with store.lock:
                store.jobs[study_id].update(status='running',message=labels[level]+' · '+task['message'],levels=copy.deepcopy(rows))
                done=task['status'] in {'completed','failed','cancelled'} and store.active is None
            if done:
                if cancel.is_set():
                    raise InterruptedError('三档对比已停止；已完成档位和原始记录保留。')
                return task
            time.sleep(.5)
    try:
        for item in config['plan']:
            if cancel.is_set():
                raise InterruptedError('三档对比已停止；已完成档位和原始记录保留。')
            level=item['level']
            row=dict(level=level,factor=item['factor'],status='preparing',mesh_config=item['mesh'])
            rows.append(row)
            current,source=store.domain(config['domain_id'],config['scheme_id'])
            child=store._start('mesh',item['mesh'],current,source,owner=study_id)
            task=wait(child,level)
            if task['status']!='completed':
                row.update(status='mesh_failed',message=task['message'])
                save(folder/'partial_study.json',dict(rows=rows))
                continue
            mesh=task['result']
            row.update(status='mesh_ready',mesh_id=mesh['result_id'],input_mesh_cells=mesh['cells'],mesh_seconds=mesh['elapsed_seconds'])
            save(folder/'partial_study.json',dict(rows=rows))
        # The three expensive solvers start only after mesh generation has
        # finished. Native layered meshes retain their cell count on import;
        # screen that family now rather than discover insufficient spacing
        # after hours of iteration. Tetra-to-poly counts remain solver checks.
        if config['mesh'].get('mesh_method')=='layered-polyhex':
            meshes=[store.get_result(row['mesh_id'],config['domain_id'],config['scheme_id'],'mesh')[0]
                    if row.get('mesh_id') else None for row in rows]
            native_preflight=layered_mesh_preflight(meshes)
            save(folder/'mesh_preflight.json',native_preflight)
        for row in rows:
            if native_preflight is not None and not native_preflight['passed']:
                if row['status']=='mesh_ready':row['message']=' '.join(native_preflight['reasons'])
                continue
            if row['status']!='mesh_ready':continue
            if cancel.is_set():raise InterruptedError('三档对比已停止；已完成档位和原始记录保留。')
            level=row['level'];current,_=store.domain(config['domain_id'],config['scheme_id'])
            mesh,source=store.get_result(row['mesh_id'],config['domain_id'],config['scheme_id'],'mesh')
            row['status']='solving'
            flow_config=dict(config['flow'],mesh_id=mesh['result_id'])
            child=store._start('flow',flow_config,current,source,mesh,owner=study_id)
            task=wait(child,level)
            if task['status']!='completed':
                row.update(status='flow_failed',message=task['message'])
                diagnostics=task.get('diagnostics',{})
                for source,target in [('minimum_fluent_orthogonal_quality','minimum_orthogonal_quality'),('maximum_fluent_aspect_ratio','maximum_aspect_ratio')]:
                    if source in diagnostics:row[target]=diagnostics[source]
                row['failure_diagnostics']=diagnostics
            else:
                flow=task['result'];last=flow['history'][-1]
                points=flow['history'][-3:]
                drift={role:max((abs(b['area_weighted_total_pressure_drop_Pa'][role]-a['area_weighted_total_pressure_drop_Pa'][role])
                    for a,b in zip(points,points[1:])),default=float('inf')) for role in ('fuel_inlet','oxidizer_inlet')}
                row.update(status='completed',flow_id=flow['result_id'],solver_cells=flow['solver_cell_count'],
                    solver_volume_m3=flow['solver_volume_m3'],cell_type=flow.get('solver_cell_type','polyhedral' if flow['polyhedral_conversion_performed'] else 'tetrahedral'),
                    effective_h_m=(flow['solver_volume_m3']/flow['solver_cell_count'])**(1/3),
                    flow_seconds=flow['elapsed_seconds'],iterations=flow['iterations_completed'],
                    minimum_orthogonal_quality=flow['minimum_fluent_orthogonal_quality'],maximum_aspect_ratio=flow['maximum_fluent_aspect_ratio'],
                    numerical_criteria_met=flow['numerical_criteria_met'],saved_data_checked=flow['saved_data_crosscheck']['checked'],
                    pressure_drop_Pa=last['area_weighted_total_pressure_drop_Pa'],pressure_drift_Pa=drift,
                    relative_mass_imbalance=last['relative_mass_imbalance'],h2_relative_convective_imbalance=last['h2_relative_convective_imbalance'],
                    conditions={k:v for k,v in flow_config.items() if k!='mesh_id'},
                    implementation_sha256=flow['implementation_sha256'],domain_artifact_sha256=flow['domain_artifact_sha256'])
                if 'outlet_profile' in flow:row['outlet_profile']=flow['outlet_profile']
                if 'interior_profile' in flow:row['interior_profile']=flow['interior_profile']
            save(folder/'partial_study.json',dict(rows=rows))
        comparison=compare(rows,config['tolerance_percent'],config['refinement_mode'])
        if native_preflight is not None:
            comparison['mesh_preflight']=native_preflight
            if not native_preflight['passed']:comparison['reasons']=native_preflight['reasons']+comparison['reasons']
        candidate=dict(kind='study',result_id=study_id,domain_id=config['domain_id'],scheme_id=config['scheme_id'],
            config=config,rows=rows,comparison=comparison,source_sha256=domain['source_sha256'],
            domain_artifact_sha256=domain['artifact_sha256'],implementation_sha256={p:sha256(ROOT/p) for p in IMPLEMENTATION},
            scientific_eligible=False,grid_independence_verified=False,elapsed_seconds=time.monotonic()-started)
        candidate['maximum_seconds']=total_budget
        save(folder/'comparison.json',candidate)
        with (folder/'comparison.csv').open('w',encoding='utf-8',newline='') as stream:
            names=['level','status','input_mesh_cells','solver_cells','effective_h_m','iterations','minimum_orthogonal_quality',
                'maximum_aspect_ratio','mesh_seconds','flow_seconds','numerical_criteria_met','fuel_pressure_drop_Pa','air_pressure_drop_Pa']
            writer=csv.DictWriter(stream,fieldnames=names);writer.writeheader()
            for row in rows:
                writer.writerow({key:row.get(key,'') for key in names if not key.endswith('pressure_drop_Pa')}|
                    {'fuel_pressure_drop_Pa':row.get('pressure_drop_Pa',{}).get('fuel_inlet',''),
                     'air_pressure_drop_Pa':row.get('pressure_drop_Pa',{}).get('oxidizer_inlet','')})
        artifacts=['comparison.json','comparison.csv','plan.json']
        if native_preflight is not None:artifacts.append('mesh_preflight.json')
        candidate['artifact_sha256']={name:sha256(folder/name) for name in artifacts}
        save(folder/'report.json',candidate)
        state='completed';message='三档对比已结束，请查看判定依据'
    except Exception as error:
        state='cancelled' if isinstance(error,InterruptedError) else 'failed'
        message=str(error)
    finally:
        # A failed orchestrator must also wait for its bounded child cleanup.
        with store.lock:
            child_id=store.active
            if child_id:store.cancel_events[child_id].set()
        while child_id:
            time.sleep(.1)
            with store.lock:child_id=store.active
        with store.lock:
            if cancel.is_set():
                candidate=None;state='cancelled';message='三档对比已停止；已完成档位和记录保留。'
            if state=='completed' and candidate:
                store.results[study_id]=candidate;store.folders[study_id]=folder
            store.jobs[study_id].update(status=state,message=message,result=candidate if state=='completed' else None,levels=rows)
            try:
                save(folder/'task.json',store.jobs[study_id])
            finally:
                store.reservation=None
