"""User-operated design-to-Fluent tasks, serial comparisons and portable results."""
import copy
import csv
import json
import math
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile

from nozzle_platform.workflows import normalize_mesh,normalize_flow,save,sha256,ROOT,IMPLEMENTATION
from nozzle_platform.domains import FIELDS as DOMAIN_FIELDS,normalize as normalize_domain
from nozzle_platform.design import layout_svg
from validation import require_count,require_finite

STAGES=('design','assembly','domain','mesh','flow','maps')
FAMILIES={'rectangular','honeycomb','sunflower','ring'}
class ResourcesUnavailable(ValueError):pass
SOURCES=tuple(dict.fromkeys(IMPLEMENTATION+('nozzle_platform/pipelines.py','nozzle_platform/bounded.py',
    'nozzle_platform/field_maps.py','nozzle_platform/design.py','nozzle_platform/domains.py','nozzle_platform/models.py','nozzle_platform/presets.py')))


def normalize(request):
    required={'design','domain','mesh','flow','comparison','section'}
    if not isinstance(request,dict) or set(request)!=required or any(not isinstance(request[n],dict) for n in required):
        raise ValueError('请提供完整的阵列、流体域、网格、工况、比较规则和截面设置。')
    result=copy.deepcopy(request);design=result['design'];comparison=result['comparison']
    if set(result['domain'])!=DOMAIN_FIELDS-{'scheme_id'} or result['domain'].get('template_confirmed') is not True:
        raise ValueError('请填写流体域参数，并确认供气模板与实际模型一致。')
    if set(result['mesh'])&{'scheme_id','domain_id'} or set(result['flow'])&{'scheme_id','domain_id','mesh_id'}:
        raise ValueError('一键任务自动关联方案与网格，请勿指定历史结果编号。')
    mesh=normalize_mesh(dict(result['mesh'],scheme_id='planned',domain_id='planned'))
    flow=normalize_flow(dict(result['flow'],scheme_id='planned',domain_id='planned',mesh_id='planned'))
    if flow.get('compute_device','cpu')=='cpu' and flow.get('convergence_mode')!='gpu-validation':
        raise ValueError('本机一键 CPU 运行仅支持接入短测（最多 25 步、计算段 60 秒）；请选择接入短测或 GPU。')
    result['mesh']={k:v for k,v in mesh.items() if k not in ('scheme_id','domain_id')}
    result['flow']={k:v for k,v in flow.items() if k not in ('scheme_id','domain_id','mesh_id')}
    if set(comparison)-{'basis','cases','fuel_heat_input_kW','fuel_lhv_MJ_kg'} or comparison.get('basis') not in {'single','same-area','same-heat'}:
        raise ValueError('请选择单方案、相同安装面积或相同燃料热输入。')
    if not isinstance(comparison.get('cases',[]),list):raise ValueError('附加方案需为列表。')
    cases=[dict(N=design.get('N'),family=design.get('family'))]+comparison.get('cases',[])
    if not 1<=len(cases)<=3 or (comparison['basis']=='single' and len(cases)!=1):raise ValueError('每次支持 1～3 个串行方案，单方案模式不接受附加方案。')
    if comparison['basis']!='single' and len(cases)<2:raise ValueError('对比至少需要两个不同方案。')
    for case in cases:
        if not isinstance(case,dict) or set(case)!={'N','family'}:raise ValueError('附加方案只设置喷嘴数量和排列方式。')
        case['N']=require_count(case['N'],'N')
        if case['N']>25 or case['family'] not in FAMILIES:raise ValueError('一键流体域目前支持 1～25 个喷嘴及界面列出的排列。')
    if len({(c['N'],c['family']) for c in cases})!=len(cases):raise ValueError('请删除数量和排列相同的重复方案。')
    lhv=require_finite(comparison.get('fuel_lhv_MJ_kg',120.),'fuel_lhv_MJ_kg')
    if not 100<=lhv<=150:raise ValueError('氢气低位热值需为 100～150 MJ/kg，请核对所用参考值。')
    comparison['fuel_lhv_MJ_kg']=lhv
    if comparison['basis']=='same-heat':
        q=require_finite(comparison.get('fuel_heat_input_kW'),'fuel_heat_input_kW')
        if not 0<q<=10000:raise ValueError('燃料热输入需大于 0 且不超过 10000 kW。')
        comparison['fuel_heat_input_kW']=q
    section=result['section']
    if set(section)!={'axis','position_mm'} or section['axis'] not in ('x','y'):raise ValueError('请选择 X 或 Y 截面并填写位置。')
    section['position_mm']=require_finite(section['position_mm'],'position_mm')
    result['cases']=cases
    return result


def heat_velocity(heat_kW,lhv_MJ_kg,area_mm2,temperature_K):
    """Initial velocity for pure H2; measured inlet mass flow verifies the rule."""
    # Same H2 molecular weight as the current Fluent hydrogen-air template.
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in [heat_kW,lhv_MJ_kg,area_mm2,temperature_K]):
        raise ValueError('热输入、热值、实际入口面积和温度必须为正的有限数值。')
    density=101325.*2.01594/(8314.46261815324*temperature_K)
    target_mass=heat_kW/(lhv_MJ_kg*1000.)
    velocity=target_mass/(density*area_mm2*1e-6)
    if not math.isfinite(velocity) or not .001<=velocity<=100:
        raise ValueError('目标热输入对应入口速度超出 0.001～100 m/s；请调整热输入或喷嘴数量。')
    return dict(fuel_velocity_m_s=velocity,target_fuel_mass_flow_kg_s=target_mass,
                estimated_pure_h2_density_kg_m3=density,hydrogen_molecular_weight_kg_kmol=2.01594,
                operating_pressure_Pa=101325.,fuel_area_mm2=area_mm2)


class PipelineStore:
    def __init__(self,root,designs,domains,workflows):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.output_root=self.root.parent;self.designs=designs;self.domains=domains;self.workflows=workflows
        self.folders={};self.export_lock=threading.Lock()
        for folder in self.root.glob('run_*'):
            try:
                record=json.loads((folder/'task.json').read_text(encoding='utf-8'));key=record['job_id']
                if folder.name!='run_'+key or not re.fullmatch('[0-9a-f]{32}',key):continue
                if record['status'] in ('queued','running'):
                    record.update(status='interrupted',message='上次运行被中断，已完成文件保留；未自动恢复求解。')
                    save(folder/'task.json',record)
                self.workflows.jobs[key]=record;self.folders[key]=folder
            except (OSError,ValueError,KeyError,TypeError):continue

    def publish(self,state):
        with self.workflows.lock:
            self.workflows.jobs[state['job_id']]=copy.deepcopy(state)
            save(self.folders[state['job_id']]/'task.json',state)

    def catalog(self):
        with self.workflows.lock:
            ordered=sorted(self.folders,key=lambda key:self.workflows.jobs[key].get('started_at',0),reverse=True)
            return [dict(job_id=key,status=self.workflows.jobs[key]['status'],message=self.workflows.jobs[key]['message']) for key in ordered]

    def preflight(self,request):
        """Parameter/resource checks only; CAD ports, licence and fields remain pending."""
        import psutil
        with self.workflows.lock:
            if self.workflows.active or self.workflows.reservation:raise ValueError('已有本机任务运行，请结束后再检查其他配置。')
        original=copy.deepcopy(request)
        if not isinstance(original,dict) or not isinstance(original.get('domain'),dict) or type(original['domain'].get('template_confirmed')) is not bool:
            raise ValueError('请提供完整参数和供气模板确认状态。')
        confirmed=original['domain']['template_confirmed'];original['domain']['template_confirmed']=True
        config=normalize(original);model=self.designs.models.get(config['design']['model_id'])
        source=self.designs.models.root/(model['source_id']+'.step')
        if not source.is_file() or sha256(source)!=model['source_id']:raise ValueError('当前模型文件缺失或哈希改变，请重新导入。')
        rows=[]
        for i,case in enumerate(config['cases'],1):
            row=dict(index=i,**case,parameters_valid=False,geometry_verified=False)
            try:
                scheme=self.designs.create(dict(config['design'],**case))
                if not scheme['feasible']:raise ValueError(scheme['message'])
                normalize_domain(dict(config['domain'],scheme_id=scheme['scheme_id']),scheme,model)
                row.update(parameters_valid=True,message='阵列包络及腔体／入口参数检查通过；实际流道与入口数量将在 CAD 阶段核查。',
                    expected_oxidizer_ports=case['N']*config['domain']['oxidizer_ports_per_nozzle'],
                    expected_fuel_ports=case['N']*config['domain']['fuel_ports_per_nozzle'])
            except (ValueError,TypeError,KeyError) as error:row['message']=str(error)
            rows.append(row)
        memory=psutil.virtual_memory().available/1024**3;disk=shutil.disk_usage(self.root).free/1024**3
        capability=self.workflows.capability();ready=memory>=4 and disk>=5 and capability['fluent_available']
        return dict(cases=rows,parameters_valid=all(r['parameters_valid'] for r in rows),template_confirmed=confirmed,
            resources=dict(available_memory_GiB=memory,minimum_memory_GiB=4,output_disk_free_GiB=disk,minimum_disk_GiB=5,
                           fluent_available=capability['fluent_available'],ready=ready),
            ready_for_cad=all(r['parameters_valid'] for r in rows) and ready and confirmed,
            pending_checks=['实际 CAD 流道连通与入口数量','实际网格规模、单元与边界质量','启动时许可证及显存','求解与保存场核对'],
            note='本次未启动 CAD、网格或 Fluent。资源为当前快照，启动时重新检查；预检不保证几何、许可证、收敛或保存场通过。',
            source_sha256=model['source_id'],scientific_eligible=False)

    def status(self,key):
        if key not in self.folders:raise ValueError('找不到一键运行记录。')
        record=self.workflows.status(key)
        # Internal file paths never enter HTTP responses.
        record['files']=[dict(name=n,sha256=v['sha256'],bytes=v['bytes'],url='/pipeline-files/'+key+'/'+n) for n,v in record['files'].items()]
        return record

    def file(self,key,name):
        if key not in self.folders:raise ValueError('找不到运行记录。')
        record=self.workflows.status(key);item=record['files'].get(name)
        if item is None:raise ValueError('该文件尚未生成。')
        path=(self.output_root/item['path']).resolve()
        if not path.is_relative_to(self.output_root) or not path.is_file() or sha256(path)!=item['sha256']:
            raise ValueError('结果文件缺失或哈希改变，已停止下载。')
        return path

    def add_file(self,state,path,name,digest=None):
        path=Path(path).resolve()
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*\.(json|csv|png|svg|step|brep|geo|msh|npz|txt|md|zip|h5|trn|jou)',name) or '..' in name:raise ValueError('结果文件名无效。')
        if not path.is_relative_to(self.output_root):raise ValueError('结果路径超出本机项目目录。')
        state['files'][name]=dict(path=str(path.relative_to(self.output_root)),sha256=digest or sha256(path),bytes=path.stat().st_size)

    def start_from_mesh(self,key):
        old=self.status(key)
        if old['status'] not in {'failed','completed_with_failures'} or len(old['cases'])!=1:
            raise ValueError('仅支持单方案在冷流启动前中断后复用已检查网格。')
        row=old['cases'][0]
        if row.get('stage')!='flow' or not row.get('mesh_id') or row.get('flow_job_id'):
            raise ValueError('该任务不满足冷流启动前网格复用条件；已启动的求解不自动重算。')
        legacy_resource_error=str(row.get('error','')).startswith(('可用内存不足 4 GiB','输出磁盘可用空间不足 5 GiB'))
        if not row.get('resource_gate_stopped_before_solver') and not legacy_resource_error:
            raise ValueError('仅对明确在求解启动前被资源门限停止的任务复用网格。')
        self.resources();self.file(key,'single_nozzle.step')
        if self.workflows.active or self.workflows.reservation:raise ValueError('已有本机任务正在运行。')
        if row['mesh_id'] not in self.workflows.results:self.restore_mesh_record(key,old,row)
        mesh,_=self.workflows.get_result(row['mesh_id'],row['domain_id'],row['scheme_id'],'mesh')
        if mesh.get('implementation_sha256')!={n:sha256(ROOT/n) for n in IMPLEMENTATION}:
            raise ValueError('网格程序版本已变化，不能复用该历史网格。')
        request={k:copy.deepcopy(v) for k,v in old['config'].items() if k!='cases'}
        return self.start(request,reuse_mesh=dict(source_job_id=key,mesh_id=row['mesh_id'],domain_id=row['domain_id'],scheme_id=row['scheme_id']))

    def restore_mesh_record(self,key,old,row):
        """Restore only this lineage-bound completed mesh, never an active solver."""
        mid=row['mesh_id']
        if not isinstance(mid,str) or not re.fullmatch('[0-9a-f]{32}',mid):raise ValueError('历史网格编号无效。')
        folder=self.workflows.root/('mesh_'+mid)
        report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
        job=json.loads((folder/'job.json').read_text(encoding='utf-8'))
        expected=dict(old['config']['mesh'],domain_id=row['domain_id'],scheme_id=row['scheme_id'])
        current={n:sha256(ROOT/n) for n in IMPLEMENTATION}
        if (report.get('kind')!='mesh' or report.get('result_id')!=mid or report.get('config')!=expected
                or job.get('kind')!='mesh' or job.get('config')!=expected
                or report.get('implementation_sha256')!=current or job.get('implementation_sha256')!=current
                or report.get('engine')!=job.get('engine') or job.get('engine')!='mesh-flow-1'):
            raise ValueError('历史网格配置或程序版本不匹配。')
        domain=job['domain'];source=old['model_reference']['source_id']
        if (domain.get('domain_id')!=row['domain_id'] or domain.get('scheme_id')!=row['scheme_id']
                or domain.get('source_sha256')!=source or report.get('source_sha256')!=source
                or report.get('domain_id')!=row['domain_id'] or report.get('scheme_id')!=row['scheme_id']
                or report.get('cells')!=row['mesh_cells'] or report.get('domain_artifact_sha256')!=domain.get('artifact_sha256')):
            raise ValueError('历史网格来源不匹配。')
        for name,digest in report['artifact_sha256'].items():
            if Path(name).name!=name or not (folder/name).is_file() or sha256(folder/name)!=digest:raise ValueError('历史网格文件已改变。')
        if sha256(self.file(key,'case1_flow.msh'))!=report['artifact_sha256']['flow.msh']:raise ValueError('历史网格与原任务文件不一致。')
        domain_folder=None
        for name,digest in domain['artifact_sha256'].items():
            path=self.file(key,'case1_'+name)
            if sha256(path)!=digest:raise ValueError('历史流体域文件已改变。')
            if domain_folder is not None and domain_folder!=path.parent:raise ValueError('历史流体域文件目录不一致。')
            domain_folder=path.parent
        try:self.designs.models.get(old['config']['design']['model_id'])
        except ValueError:self.restore_source(key)
        scheme=self.designs.create(old['config']['design'])
        if scheme['scheme_id']!=row['scheme_id'] or not scheme['feasible']:raise ValueError('历史几何参考未能恢复。')
        with self.domains.lock:
            self.domains.domains[row['domain_id']]=domain;self.domains.directories[row['domain_id']]=domain_folder
        with self.workflows.lock:
            self.workflows.results[mid]=report;self.workflows.folders[mid]=folder
            self.workflows.jobs[mid]=dict(job_id=mid,kind='mesh',status='completed',message='历史网格已恢复并核对',result=report)

    def start(self,request,*,reuse_mesh=None):
        config=normalize(request);self.designs.models.get(config['design'].get('model_id'))
        if not self.workflows.capability()['fluent_available']:raise ValueError(self.workflows.capability()['note'])
        with self.workflows.lock:
            if self.workflows.active or self.workflows.reservation:raise ValueError('已有本机任务正在运行，请等待或停止。')
            if shutil.disk_usage(self.root).free<5*1024**3:raise ValueError('一键运行需至少 5 GiB 可用输出空间。')
            key=uuid.uuid4().hex;folder=self.root/('run_'+key);folder.mkdir();self.folders[key]=folder
            cancel=self.workflows.cancel_events[key]=threading.Event();self.workflows.reservation=key
            state=dict(job_id=key,kind='pipeline',status='queued',message='等待自动生成阵列',config=config,cases=[],files={},
                       scientific_eligible=False,grid_independence_verified=False,comparison_eligible=False,cpu_fallback=False,
                       implementation_sha256={n:sha256(ROOT/n) for n in SOURCES},started_at=time.time())
            if reuse_mesh:state['mesh_resume']=copy.deepcopy(reuse_mesh)
            for i,item in enumerate(config['cases']):
                state['cases'].append(dict(index=i+1,**item,status='queued',stages=[dict(name=n,status='pending',message='等待执行') for n in STAGES]))
            save(folder/'request.json',config);self.add_file(state,folder/'request.json','request.json');self.publish(state)
            try:threading.Thread(target=self.run,args=(state,cancel),daemon=True).start()
            except Exception as error:
                self.workflows.reservation=None;state.update(status='failed',message='后台任务未启动：'+str(error));self.publish(state)
                raise ValueError(state['message']) from error
        return self.status(key)

    def stage(self,state,row,name,status,message):
        item=next(s for s in row['stages'] if s['name']==name)
        if item['status']==status and item['message']==message:return
        if status=='running':row['status']='running'
        item.update(status=status,message=message)
        row['stage']=name;state['message']=f"方案 {row['index']}／{len(state['cases'])}：{message}"
        self.publish(state)

    def check(self,state,cancel):
        if cancel.is_set():raise InterruptedError('已停止一键任务，已完成文件保留。')
        for n,d in state['implementation_sha256'].items():
            if sha256(ROOT/n)!=d:raise ValueError('运行期间程序文件发生变化，已停止后续计算。')

    def resources(self):
        import psutil
        if psutil.virtual_memory().available<4*1024**3:raise ResourcesUnavailable('可用内存不足 4 GiB，已在下一计算步骤前停止；已有文件保留。')
        if shutil.disk_usage(self.root).free<5*1024**3:raise ResourcesUnavailable('输出磁盘可用空间不足 5 GiB，已停止后续步骤。')

    def wait(self,state,row,stage,child,cancel):
        row[stage+'_job_id']=child['job_id'];stop_requested=False
        while True:
            task=self.workflows.status(child['job_id'])
            self.stage(state,row,stage,'running',task['message'])
            with self.workflows.lock:cleaned=self.workflows.active!=child['job_id']
            if task['status'] not in ('queued','running') and cleaned:break
            if cancel.is_set():
                if not stop_requested and not cleaned:self.workflows.stop(child['job_id']);stop_requested=True
                time.sleep(.1)
            else:cancel.wait(.3)
        if task['status']!='completed':
            row['diagnostics']=task.get('diagnostics',{});row['error_code']=task.get('error_code','worker_failed')
            count=row['diagnostics'].get('iterations_completed')
            if type(count) is int and count>=0:row['iterations']=count
            self.diagnostic_files(state,row,stage,child['job_id'])
            if cancel.is_set():raise InterruptedError('已停止一键任务，子任务进程已清理。')
            raise ValueError(task['message'])
        if cancel.is_set():raise InterruptedError('已停止一键任务，子任务进程已清理。')
        return task['result']

    def diagnostic_files(self,state,row,stage,child_id):
        """Keep failed/stopped raw data separate from accepted case/data exports."""
        if stage not in ('mesh','flow') or not isinstance(child_id,str) or not re.fullmatch('[0-9a-f]{32}',child_id):return []
        folder=self.workflows.root/(stage+'_'+child_id);added=[]
        standard=[('worker.log','worker.txt'),('fluent.trn','fluent.txt'),('mesh_quality.json','mesh_quality.json'),('quality_history.json','quality_history.json'),('partial.json','partial.json')]
        unverified=[(n,n) for n in ['final.cas.h5','final.dat.h5','configured.json','execution_profile.json','device_evidence.json','cleanup.json','field_health.json','species_controls.json']]
        for group,prefix in [(standard,f"case{row['index']}_{stage}_"),(unverified,f"case{row['index']}_unverified_{stage}_")]:
            for source,suffix in group:
                path=folder/source;name=prefix+suffix
                if path.is_file() and name not in state['files']:
                    self.add_file(state,path,name);added.append(name)
        return added

    def post(self,state,row,spec,cancel,suffix):
        from nozzle_platform.bounded import run
        folder=self.folders[state['job_id']]/suffix;folder.mkdir()
        save(folder/'postprocess.json',spec)
        code=run([sys.executable,'-u',str(ROOT/'nozzle_platform/field_maps.py'),str(folder)],
                 cwd=ROOT,log=folder/'postprocess.log',timeout=120,cancel=cancel)
        result=json.loads((folder/'postprocess_result.json').read_text(encoding='utf-8'))
        if code or not result['ok']:raise ValueError(result.get('error','云图处理未完成。'))
        return result['result']

    def run(self,state,cancel):
        state['status']='running';self.publish(state);key=state['job_id'];folder=self.folders[key];config=state['config']
        try:
            model=self.designs.models.get(config['design']['model_id'])
            state['model_reference']={k:v for k,v in model.items() if k not in ('vertices','triangles')}
            self.add_file(state,self.designs.models.root/(model['source_id']+'.step'),'single_nozzle.step',model['source_id'])
            if model.get('vertices'):
                import numpy as np
                np.savez_compressed(folder/'model_preview.npz',vertices=np.asarray(model['vertices'],float),triangles=np.asarray(model['triangles'],np.int64))
                self.add_file(state,folder/'model_preview.npz','model_preview.npz')
            for row in state['cases']:
                local=folder/('case_'+str(row['index']));local.mkdir();name='case'+str(row['index']);self.check(state,cancel)
                try:
                    self.stage(state,row,'design','running','生成并检查阵列')
                    scheme=self.designs.create(dict(config['design'],N=row['N'],family=row['family']))
                    if not scheme['feasible']:
                        row['geometry_failure_reasons']=scheme.get('failure_reasons',[])
                        raise ValueError(scheme['message'])
                    row['scheme_id']=scheme['scheme_id'];row['coordinates_mm']=scheme['coordinates_mm'];row['geometry_summary']=scheme['summary']
                    save(local/'scheme.json',scheme);(local/'layout.svg').write_text(layout_svg(scheme),encoding='utf-8')
                    with (local/'coordinates.csv').open('w',encoding='utf-8-sig',newline='') as f:
                        w=csv.writer(f);w.writerow(['nozzle_id','x_mm','y_mm','z_mm']);w.writerows((i+1,*p,0) for i,p in enumerate(scheme['coordinates_mm']))
                    for n in ['scheme.json','layout.svg','coordinates.csv']:self.add_file(state,local/n,name+'_'+n)
                    self.stage(state,row,'design','completed','阵列数量、边界与间距检查通过')
                    self.check(state,cancel);self.stage(state,row,'assembly','running','复制实体并导出三维装配')
                    row['assembly']=self.designs.models.export_array(model['model_id'],scheme['coordinates_mm'],local/'array.step',cancel=cancel)
                    self.add_file(state,local/'array.step',name+'_assembly.step')
                    if model.get('vertices'):
                        row['assembly_image']=self.post(state,row,dict(operation='assembly',preview=str(folder/'model_preview.npz'),
                            coordinates=scheme['coordinates_mm'],output=str(local/'assembly.png')),cancel,'assembly_'+str(row['index']))
                        for n in ['assembly.png','assembly.json']:self.add_file(state,local/n,name+'_'+n)
                    self.stage(state,row,'assembly','completed','实体装配和三维图已导出并核对')
                    self.check(state,cancel);self.stage(state,row,'domain','running','构建公共流体域与命名边界')
                    domain=self.domains.create(dict(config['domain'],scheme_id=scheme['scheme_id']),cancel=cancel)
                    row['domain_id']=domain['domain_id'];row['installation_area_mm2']=domain['array_specification']['boundary']['radius_mm']**2*math.pi*domain['array_specification']['boundary']['sweep_angle_deg']/360
                    current,source=self.workflows.domain(domain['domain_id'],scheme['scheme_id'])
                    for n,d in domain['artifact_sha256'].items():self.add_file(state,source/n,name+'_'+n,d)
                    self.stage(state,row,'domain','completed','流体域连通与四类边界检查通过')
                    self.check(state,cancel);self.stage(state,row,'mesh','running','自动生成并检查体网格');self.resources()
                    mesh_config=dict(config['mesh'],domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'])
                    seed=state.get('mesh_resume')
                    if seed:
                        if domain['domain_id']!=seed['domain_id'] or scheme['scheme_id']!=seed['scheme_id']:raise ValueError('恢复后的几何或工况引用改变，不能复用网格。')
                        mesh,_=self.workflows.get_result(seed['mesh_id'],domain['domain_id'],scheme['scheme_id'],'mesh')
                        if mesh.get('implementation_sha256')!={n:sha256(ROOT/n) for n in IMPLEMENTATION}:raise ValueError('网格程序版本已变化，已停止复用。')
                        row.update(mesh_job_id=seed['mesh_id'],mesh_reused_from_job=seed['source_job_id'])
                    else:
                        child=self.workflows._start('mesh',mesh_config,current,source,owner=key)
                        mesh=self.wait(state,row,'mesh',child,cancel)
                    row['mesh_id']=mesh['result_id'];row['mesh_cells']=mesh['cells']
                    mesh,source=self.workflows.get_result(mesh['result_id'],domain['domain_id'],scheme['scheme_id'],'mesh')
                    self.add_file(state,source/'flow.msh',name+'_flow.msh',mesh['artifact_sha256']['flow.msh'])
                    self.stage(state,row,'mesh','completed','复用同参数且哈希已核验的历史网格' if seed else '体网格与边界检查通过')
                    self.check(state,cancel);self.stage(state,row,'flow','running','自动配置并运行 Fluent 冷流');self.resources()
                    flow_config=dict(config['flow'],domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'],mesh_id=mesh['result_id'])
                    if config['comparison']['basis']=='same-heat':
                        area=mesh['boundary_mesh_area_m2']['fuel_inlet']*1e6
                        row['heat_input_plan']=heat_velocity(config['comparison']['fuel_heat_input_kW'],config['comparison']['fuel_lhv_MJ_kg'],area,flow_config['temperature_K'])
                        row['heat_input_plan']['area_basis']='actual generated mesh boundary area, not CAD envelope area'
                        flow_config['fuel_velocity_m_s']=row['heat_input_plan']['fuel_velocity_m_s']
                    flow_config=normalize_flow(flow_config)
                    row['applied_flow_config']=flow_config
                    child=self.workflows._start('flow',flow_config,current,source,mesh,owner=key)
                    flow=self.wait(state,row,'flow',child,cancel);row['flow_id']=flow['result_id'];row['iterations']=flow['iterations_completed']
                    row['numerical_criteria_met']=flow['numerical_criteria_met'];row['gpu_validation_only']=flow.get('gpu_validation_only',False)
                    last=flow['history'][-1];row['pressure_drop_Pa']=last['area_weighted_total_pressure_drop_Pa']
                    row['actual_fuel_mass_flow_kg_s']=last['mass_flow_kg_s']['fuel_inlet']
                    row['actual_air_mass_flow_kg_s']=last['mass_flow_kg_s']['oxidizer_inlet']
                    row['actual_fuel_heat_input_kW']=last['mass_flow_kg_s']['fuel_inlet']*config['comparison']['fuel_lhv_MJ_kg']*1000
                    if config['comparison']['basis']=='same-heat':
                        target=config['comparison']['fuel_heat_input_kW'];row['heat_input_rule_verified']=abs(row['actual_fuel_heat_input_kW']/target-1)<=.001
                        if not row['heat_input_rule_verified']:row['comparison_error']='实际入口热输入与目标偏差超过 0.1%，该方案不满足同热输入比较规则。'
                    flow,source=self.workflows.get_result(flow['result_id'],domain['domain_id'],scheme['scheme_id'],'flow')
                    for n,d in flow['artifact_sha256'].items():
                        self.add_file(state,source/n,name+'_'+n,d)
                    if flow_config.get('species_solver_control')=='strict-absolute':
                        from nozzle_platform.restart_check import run_native,message,journal,RestartCleanupError
                        self.check(state,cancel);self.resources();self.stage(state,row,'flow','running','保存场已检查，正在零迭代重读；不追加求解')
                        try:reopen=run_native(source/'final.cas.h5',source/'final.dat.h5',self.workflows.fluent,flow_config,local/'restart',cancel)
                        except InterruptedError:raise
                        except RestartCleanupError as error:raise ResourcesUnavailable(str(error)) from error
                        except Exception as error:reopen=dict(native_read_completed=False,field_preservation_passed=False,added_iterations=0,error=str(error))
                        row['restart_check']=reopen;row['restart_message']=message(reopen)
                        save(local/'restart_check.json',reopen);self.add_file(state,local/'restart_check.json',name+'_restart_check.json')
                        if reopen.get('native_read_completed'):
                            (local/'reopen_gpu.jou').write_text(journal(name+'_final.cas.h5'),encoding='ascii')
                            self.add_file(state,local/'reopen_gpu.jou',name+'_reopen_gpu.jou')
                    self.stage(state,row,'flow','completed','计算结束：'+('达到数值检查标准' if row['numerical_criteria_met'] else '保存数据可查看，尚未收敛'))
                    self.check(state,cancel);self.stage(state,row,'maps','running','从实际保存场生成切面云图')
                    try:
                        row['section']=self.post(state,row,dict(operation='extract',case=str(source/'final.cas.h5'),data=str(source/'final.dat.h5'),
                            output=str(local/'maps'),**config['section']),cancel,'extract_'+str(row['index']))
                        row['maps_pending']=True;row['status']='completed' if row['numerical_criteria_met'] else 'not_converged'
                    except InterruptedError:raise
                    except Exception as error:
                        row['map_error']=str(error);row['status']='completed_with_map_error';self.stage(state,row,'maps','failed',str(error))
                except InterruptedError:raise
                except Exception as error:
                    row.update(status='failed',error=str(error));stage=row.get('stage','design');self.stage(state,row,stage,'failed',str(error))
                    if isinstance(error,ResourcesUnavailable):row['resource_gate_stopped_before_solver']=stage=='flow' and not row.get('flow_job_id')
                    for item in row['stages']:
                        if item['status']=='pending':item.update(status='skipped',message='前序步骤未通过')
                    self.publish(state)
                    if isinstance(error,ResourcesUnavailable):raise
            ready=[row for row in state['cases'] if row.get('maps_pending')]
            if ready:
                self.check(state,cancel)
                try:
                    self.post(state,ready[0],dict(operation='render',folders=[str(folder/('case_'+str(r['index']))/'maps') for r in ready],
                        labels=[f"Case {r['index']}: N={r['N']}, {r['family']}" for r in ready]),cancel,'render')
                    for row in ready:
                        maps=folder/('case_'+str(row['index']))/'maps';metadata=json.loads((maps/'maps.json').read_text(encoding='utf-8'))
                        for n in ['section.npz','section_values.csv','maps.json',*metadata['images_sha256']]:self.add_file(state,maps/n,'case'+str(row['index'])+'_'+n)
                        row['maps_pending']=False;self.stage(state,row,'maps','completed','真实切面云图已生成，多方案使用共同色标')
                except InterruptedError:raise
                except Exception as error:
                    for row in ready:row['map_error']=str(error);self.stage(state,row,'maps','failed',str(error));row['status']='completed_with_map_error'
            state['status']='completed' if all(r['status']!='failed' for r in state['cases']) else 'completed_with_failures'
            state['message']='运行结束：请查看各方案的收敛状态、云图及失败原因'
        except Exception as error:
            state.update(status='cancelled' if isinstance(error,InterruptedError) else 'failed',message=str(error))
        finally:
            with self.workflows.lock:
                child=self.workflows.active
                if child:self.workflows.cancel_events[child].set()
            while child:
                time.sleep(.1)
                with self.workflows.lock:child=self.workflows.active
            for row in state['cases']:
                if row['status'] in ('queued','running'):row.update(status='cancelled' if state['status']=='cancelled' else 'skipped',error=state['message'])
                for item in row['stages']:
                    if item['status']=='running':item.update(status='cancelled',message='任务已停止')
                    elif item['status']=='pending':item.update(status='skipped',message='本次未执行')
            state['finished_at']=time.time()
            try:self.summary(state);self.publish(state)
            except Exception as error:
                state.update(status='failed',message='结果整理未完成：'+str(error));self.publish(state)
            finally:
                with self.workflows.lock:self.workflows.reservation=None

    def summary(self,state):
        folder=self.folders[state['job_id']]
        basis=state['config']['comparison']['basis'];rows=state['cases']
        if basis=='same-area':
            areas=[r.get('installation_area_mm2') for r in rows];rule=all(a is not None for a in areas) and max(areas)-min(areas)<=1e-8*max(areas)
        elif basis=='same-heat':rule=all(r.get('heat_input_rule_verified') is True for r in rows)
        else:rule=None
        state.update(comparison_rule_verified=rule,recommended_case_id=None,comparison_eligible=False)
        with (folder/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            fields=['case','N','family','status','installation_area_mm2','actual_fuel_heat_input_kW','actual_air_mass_flow_kg_s','iterations','numerical_criteria_met','fuel_pressure_drop_Pa','air_pressure_drop_Pa','reason']
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            for row in rows:writer.writerow(dict(case=row['index'],N=row['N'],family=row['family'],status=row['status'],installation_area_mm2=row.get('installation_area_mm2'),actual_fuel_heat_input_kW=row.get('actual_fuel_heat_input_kW'),actual_air_mass_flow_kg_s=row.get('actual_air_mass_flow_kg_s'),iterations=row.get('iterations'),numerical_criteria_met=row.get('numerical_criteria_met'),fuel_pressure_drop_Pa=row.get('pressure_drop_Pa',{}).get('fuel_inlet'),air_pressure_drop_Pa=row.get('pressure_drop_Pa',{}).get('oxidizer_inlet'),reason=row.get('error',row.get('map_error',row.get('comparison_error','')))))
        report={k:v for k,v in state.items() if k!='files'};save(folder/'summary.json',report)
        self.add_file(state,folder/'comparison.csv','comparison.csv');self.add_file(state,folder/'summary.json','summary.json')
        def cell(value):return '未取得' if value is None else str(value).replace('|','／').replace('\n',' ')
        lines=['# 多喷嘴冷流方案报告','',f"任务：{state['job_id']}",f"比较规则：{basis}；条件核对：{rule}",'',
               '入口燃料热输入按用户低位热值计算，不代表模拟了燃烧放热。空气入口速度保持设定值，总流量逐方案列出。',
               '当前数据为有界冷流试算；未收敛、物理适用性和网格独立性未验证，不提供正式方案排名。','',
               '| 方案 | N | 排列 | 安装面积 / mm² | 燃料热输入 / kW | 空气流量 / kg·s⁻¹ | 步数 | 状态 |',
               '|---|---|---|---|---|---|---|---|']
        for row in rows:
            lines.append('| '+' | '.join(cell(row.get(n)) for n in ['index','N','family','installation_area_mm2','actual_fuel_heat_input_kW','actual_air_mass_flow_kg_s','iterations','status'])+' |')
            reason=row.get('error',row.get('map_error',row.get('comparison_error')))
            if reason:lines.extend(['',f"方案 {row['index']}：{cell(reason)}",''])
            if row.get('restart_message'):lines.extend(['',f"方案 {row['index']} 重读检查：{cell(row['restart_message'])}",''])
        lines.extend(['','各方案的坐标、装配、场图与 Fluent 文件按 case 编号对应；云图采用共同色标，来源与质量记录见 summary.json、maps.json 和哈希清单。'])
        (folder/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');self.add_file(state,folder/'report.md','report.md')
        (folder/'README.txt').write_text('多喷嘴一键冷流任务\n单喷嘴模型、实体装配、坐标、流体域、网格和 Fluent case/data 按方案编号对应。\n字段图为保存迭代的真实切面，采用分片常数单元值，共同色标；达到预算不表示收敛。\ncomparison.csv 比较条件及试算指标，不作燃烧性能排名。相同热输入采用用户低位热值，实际入口流量另核对。\n未收敛、失败、未取得指标及未生成云图均保留状态与原因；不得用于正式方案优选。\n',encoding='utf-8')
        self.add_file(state,folder/'README.txt','README.txt')

    def export(self,key):
        if key not in self.folders:raise ValueError('找不到运行记录。')
        with self.export_lock:
            state=self.workflows.status(key)
            if state['status'] in ('queued','running'):raise ValueError('任务仍在运行，请等待完成或停止后导出已生成文件。')
            if 'results.zip' in state['files']:
                self.file(key,'results.zip')
            else:
                added=[]
                for row in state['cases']:
                    stage=row.get('stage');child=row.get(str(stage)+'_job_id')
                    if row['status'] in ('failed','cancelled') and child:added.extend(self.diagnostic_files(state,row,stage,child))
                if any('_unverified_' in n for n in state['files']):
                    notice=self.folders[key]/'diagnostic_README.txt'
                    if 'diagnostic_README.txt' not in state['files']:
                        notice.write_text('未通过验收的原始文件\n名称含 unverified 的 case/data、设置和记录仅用于失败或中断诊断，不表示数据正确、收敛或可用于方案优选。\n原失败状态和原始记录保留，不自动生成成功场图，不修补或归一化保存数据。成对 final.cas.h5／final.dat.h5 可供人工诊断读取。\n',encoding='utf-8')
                        self.add_file(state,notice,'diagnostic_README.txt')
                if added or 'diagnostic_README.txt' in state['files']:self.publish(state)
                output=self.folders[key]/'results.zip';started=time.monotonic()
                with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as bundle:
                    manifest={}
                    for name,item in list(state['files'].items()):
                        path=self.file(key,name);manifest[name]=item['sha256']
                        with path.open('rb') as source,bundle.open(name,'w',force_zip64=True) as target:
                            while True:
                                if time.monotonic()-started>120:raise ValueError('导出超过 120 秒，原始结果仍保留，可下载单个文件。')
                                block=source.read(1024**2)
                                if not block:break
                                target.write(block)
                    bundle.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                self.add_file(state,output,'results.zip');self.publish(state)
            return dict(download_url='/pipeline-files/'+key+'/results.zip',filename='nozzle_pipeline_'+key[:10]+'.zip')

    def restore_source(self,key):
        from nozzle_platform.models import digest,MODEL_ENGINE
        if key not in self.folders:raise ValueError('找不到运行记录。')
        state=self.workflows.status(key);ref=state.get('model_reference')
        if not ref:raise ValueError('该任务尚未记录模型参考信息。')
        path=self.file(key,'single_nozzle.step')
        if sha256(path)!=ref['source_id']:raise ValueError('模型来源与任务快照不一致。')
        auto=digest(json.dumps([ref.get('model_engine',MODEL_ENGINE),ref['source_id'],ref['axis'],None],sort_keys=True).encode())
        origin=ref['origin_input_mm'] if 'origin_input_mm' in ref else None if auto==ref['model_id'] else ref['origin_mm']
        if 'origin_input_mm' not in ref and origin is not None:
            # Old CAD metadata converted integral coordinates to floats. Recover
            # their exact input representation only when its saved ID matches.
            from itertools import product
            options=[[v,int(v)] if type(v) is float and v.is_integer() else [v] for v in origin]
            for candidate in product(*options):
                candidate=list(candidate)
                if digest(json.dumps([MODEL_ENGINE,ref['source_id'],ref['axis'],candidate],sort_keys=True).encode())==ref['model_id']:
                    origin=candidate;break
        source=self.designs.models.add_source(ref.get('name','single_nozzle.step'),path.read_bytes(),
            label=ref.get('label'),origin=origin,archive_hash=ref.get('archive_sha256'))
        with self.designs.models.lock:
            self.designs.models.sources[source['source_id']]['default_axis']=ref['axis']
        model=self.designs.models.load(source['source_id'],axis=ref['axis'],origin=origin)
        if model['model_id']!=ref['model_id']:raise ValueError('历史模型参考坐标未能精确恢复，仍可下载原始装配与查看保存场图。')
        return dict(source=source,models=self.designs.models.catalog(),model=model)
