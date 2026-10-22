"""Asynchronous local mesh/solver jobs with lineage and bounded child processes."""

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile

from fluent_execution.process import WindowsJob
from validation import require_finite, require_count

ROOT = Path(__file__).absolute().parents[1]
MESH_KEYS = {'domain_id','scheme_id','min_size_mm','max_size_mm','inlet_size_mm','refine_distance_mm'}
MESH_OPTIONS = {'algorithm','curvature_samples','passage_size_mm','size_factor','cell_budget',
    'mesh_method','layer_first_height_mm','layer_count','layer_thickness_mm','local_volume_refinement'}
FLOW_KEYS = {'mesh_id','domain_id','scheme_id','fuel_velocity_m_s','oxidizer_velocity_m_s',
             'temperature_K','oxidizer_o2_mass_fraction','iterations'}
FLOW_OPTIONS = {'convergence_mode','time_budget_seconds','compute_device','transport_model'}
IMPLEMENTATION = ('nozzle_platform/workflows.py','nozzle_platform/workflow_worker.py',
    'nozzle_platform/volume_mesh.py','nozzle_platform/layered_mesh.py','nozzle_platform/poly_mesh.py','nozzle_platform/domain_cad.py','nozzle_platform/flow_solver.py',
    'nozzle_platform/flow_validation.py','nozzle_platform/mesh_quality.py','nozzle_platform/convergence.py','nozzle_platform/mesh_study.py','nozzle_platform/grid_convergence.py','nozzle_platform/field_profiles.py',
    'nozzle_platform/probe_points.py','nozzle_platform/outlet_sampling.py',
    'nozzle_platform/solver_device.py','coldflow_trial/solver.py','coldflow_trial/processes.py',
    'fluent_execution/process.py')


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    # A Windows reader can briefly hold a handle that disallows replacement.
    # Keep atomic publication; retry only sharing/access violations, bounded
    # to less than a second. Persistent permission errors must still fail.
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError as error:
            if getattr(error,'winerror',None) not in {5,32,33} or attempt == 7:
                raise
            time.sleep(min(.01 * 2**attempt,.1))


def normalize_mesh(request):
    if not isinstance(request,dict) or not MESH_KEYS<=set(request) or set(request)-MESH_KEYS-MESH_OPTIONS:
        raise ValueError('网格参数缺失或包含未知字段。')
    result = dict(request)
    result.setdefault('mesh_method','tetra')
    if not isinstance(result['mesh_method'],str) or result['mesh_method'] not in {'tetra','layered-polyhex'}:
        raise ValueError('请选择四面体或轴向壁面层网格。')
    if result['mesh_method']=='tetra' and set(result)&{'layer_first_height_mm','layer_count','layer_thickness_mm','local_volume_refinement'}:
        raise ValueError('壁面层参数只适用于轴向壁面层网格。')
    for key in MESH_KEYS-{'domain_id','scheme_id'}:
        result[key] = require_finite(request[key],key)
    if not (.02<=result['min_size_mm']<=result['inlet_size_mm']<=result['max_size_mm']<=20):
        raise ValueError('网格尺寸需满足 0.02 ≤ 最小尺寸 ≤ 入口尺寸 ≤ 最大尺寸 ≤ 20 mm。')
    if not (result['inlet_size_mm']<result['refine_distance_mm']<=100):
        raise ValueError('入口加密范围需大于入口尺寸，且不超过 100 mm。')
    result.setdefault('algorithm','delaunay')
    result.setdefault('curvature_samples',20)
    result.setdefault('passage_size_mm',min(.5,result['max_size_mm']))
    result.setdefault('size_factor',1.)
    result['size_factor'] = require_finite(result['size_factor'],'size_factor')
    if not .4<=result['size_factor']<=2:
        raise ValueError('整体网格尺寸系数需在 0.4～2 之间。')
    result.setdefault('cell_budget',1500000)
    result['cell_budget']=require_count(result['cell_budget'],'cell_budget')
    if not 1500000<=result['cell_budget']<=3000000:
        raise ValueError('体网格单元预算需在 150 万～300 万之间。')
    result['passage_size_mm'] = require_finite(result['passage_size_mm'],'passage_size_mm')
    if not result['inlet_size_mm']<=result['passage_size_mm']<=result['max_size_mm']:
        raise ValueError('内部流道尺寸需介于入口尺寸与最大尺寸之间。')
    if not isinstance(result['algorithm'],str) or result['algorithm'] not in {'hxt','delaunay'}:
        raise ValueError('请选择 HXT 或 Delaunay 体网格算法。')
    result['curvature_samples'] = require_count(result['curvature_samples'],'curvature_samples')
    if not 16<=result['curvature_samples']<=64:
        raise ValueError('曲率采样需在 16～64 之间。')
    if result['mesh_method']=='layered-polyhex':
        result.setdefault('local_volume_refinement',False)
        if type(result['local_volume_refinement']) is not bool:raise ValueError('局部体积加密选项需为布尔值。')
        result['layer_first_height_mm']=require_finite(result.get('layer_first_height_mm',.025),'layer_first_height_mm')
        result['layer_thickness_mm']=require_finite(result.get('layer_thickness_mm',.25),'layer_thickness_mm')
        result['layer_count']=require_count(result.get('layer_count',5),'layer_count')
        if not (.005<=result['layer_first_height_mm']<=.1 and .05<=result['layer_thickness_mm']<=.35 and 3<=result['layer_count']<=12):
            raise ValueError('壁面层参考首层需为 0.005～0.1 mm、总厚度 0.05～0.35 mm、参考层数 3～12。')
        from nozzle_platform.layered_mesh import controls
        controls(result)
    return result


def normalize_flow(request):
    if not isinstance(request,dict) or not FLOW_KEYS<=set(request) or set(request)-FLOW_KEYS-FLOW_OPTIONS:
        raise ValueError('冷流参数缺失或包含未知字段。')
    result = dict(request)
    for key in FLOW_KEYS-{'mesh_id','domain_id','scheme_id','iterations'}:
        result[key] = require_finite(request[key],key)
    for key in ('fuel_velocity_m_s','oxidizer_velocity_m_s'):
        if not .001<=result[key]<=100:
            raise ValueError('入口速度需在 0.001～100 m/s 之间。')
    if not 200<=result['temperature_K']<=600 or not 0<result['oxidizer_o2_mass_fraction']<1:
        raise ValueError('冷流温度需在 200～600 K 之间，氧气质量分数需在 0～1 之间。')
    result['iterations'] = require_count(request['iterations'],'iterations')
    mode = result.get('convergence_mode','trial')
    if not isinstance(mode,str) or mode not in {'trial','grid-verification','gpu-validation'}:
        raise ValueError('冷流收敛模式无效。')
    from nozzle_platform.solver_device import validate_selection
    validate_selection(result)
    if mode=='gpu-validation':
        maximum=100 if result.get('compute_device')=='gpu' else 25
        if result['iterations']>maximum:raise ValueError(f'接入验证最多 {maximum} 步，不用于正式收敛判断。')
        result['time_budget_seconds']=require_count(result.get('time_budget_seconds',240),'time_budget_seconds')
        if not 120<=result['time_budget_seconds']<=300:raise ValueError('接入验证时限需为 120～300 秒。')
        return result
    if result['iterations']>(2000 if mode=='grid-verification' else 500):
        raise ValueError('普通冷流最多 500 步，网格验证最多 2000 步。')
    result['time_budget_seconds']=require_count(result.get('time_budget_seconds',1800),'time_budget_seconds')
    if not 1800<=result['time_budget_seconds']<=(7200 if mode=='grid-verification' else 1800):
        raise ValueError('普通试算运行时限为 1800 秒；网格验证可设为 1800～7200 秒。')
    return result


def detect_fluent(config=None):
    settings = dict(config or {})
    workspace = ROOT.parents[1]
    settings.setdefault('python',str(workspace/'分析/M8B最新模型_内部通流_20261008/tools/fluent_venv/Scripts/python.exe'))
    candidates = [Path(os.environ[k])/'fluent/ntbin/win64/fluent.exe' for k in ('AWP_ROOT252',) if os.environ.get(k)]
    candidates += [Path('G:/Ansys2025R2/v252/fluent/ntbin/win64/fluent.exe'),Path('C:/Program Files/ANSYS Inc/v252/fluent/ntbin/win64/fluent.exe')]
    settings.setdefault('executable',str(next((p for p in candidates if p.is_file()),candidates[0])))
    settings.setdefault('product_version','25.2.0')
    settings.setdefault('processes',2)
    settings.setdefault('license_product','premium')
    settings.setdefault('license_server',None)
    settings.setdefault('start_timeout_seconds',120)
    return settings


class WorkflowStore:
    def __init__(self, root, domains, *, fluent=None):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True,exist_ok=True)
        self.domains = domains
        self.fluent = detect_fluent(fluent)
        self.lock = threading.RLock()
        self.jobs,self.results,self.folders,self.cancel_events = {},{},{},{}
        self.active = None
        self.reservation = None

    def capability(self):
        available = all(Path(self.fluent[key]).is_file() for key in ('python','executable'))
        return {'fluent_available':available,'supported_version':'25.2','model':'非反应氢气／空气 · 稳态层流',
            'preferred_compute_device':self.fluent.get('preferred_compute_device','cpu'),
            'gpu_validation_available':available,'gpu_formal_physics_validated':False,
            'note':'已找到本机程序；许可证与网格质量在启动时检查。' if available else '未找到 Fluent 25.2 或 PyFluent 环境；仍可生成并导出体网格。'}

    def domain(self, domain_id, scheme_id):
        with self.domains.lock:
            domain = self.domains.domains.get(domain_id)
            folder = self.domains.directories.get(domain_id)
        if not domain or domain['scheme_id']!=scheme_id:
            raise ValueError('请先为当前阵列构建有效流体域。')
        for name,expected in domain['artifact_sha256'].items():
            if not (folder/name).is_file() or sha256(folder/name)!=expected:
                raise ValueError('流体域文件已改变，请重新构建。')
        return domain,folder

    def get_result(self, result_id, domain_id, scheme_id, kind=None):
        with self.lock:
            result = self.results.get(result_id)
            folder = self.folders.get(result_id)
        if (not result or result['domain_id']!=domain_id or result['scheme_id']!=scheme_id
                or (kind and result['kind']!=kind)):
            raise ValueError('结果与当前方案不一致，请重新运行。')
        self.domain(domain_id,scheme_id)
        for name,expected in result['artifact_sha256'].items():
            if not (folder/name).is_file() or sha256(folder/name)!=expected:
                raise ValueError('结果文件已改变，请重新运行。')
        if result['kind']=='study':
            for row in result['rows']:
                for key,child_kind in [('mesh_id','mesh'),('flow_id','flow')]:
                    if row.get(key):
                        self.get_result(row[key],domain_id,scheme_id,child_kind)
        return result,folder

    def start_study(self, request):
        from nozzle_platform.mesh_study import start
        return start(self,request)

    def start_mesh(self, request):
        config = normalize_mesh(request)
        if config['mesh_method']=='layered-polyhex' and not self.capability()['fluent_available']:
            raise ValueError('轴向壁面层需要本机 Fluent 25.2 和 PyFluent 环境。')
        domain,source = self.domain(config['domain_id'],config['scheme_id'])
        return self._start('mesh',config,domain,source)

    def start_flow(self, request):
        config = normalize_flow(request)
        if not self.capability()['fluent_available']:
            raise ValueError(self.capability()['note'])
        domain,_ = self.domain(config['domain_id'],config['scheme_id'])
        mesh,source = self.get_result(config['mesh_id'],config['domain_id'],config['scheme_id'],'mesh')
        return self._start('flow',config,domain,source,mesh)

    def _start(self, kind, config, domain, source, mesh=None, *, owner=None):
        with self.lock:
            if self.active or (self.reservation and owner!=self.reservation):
                raise ValueError('本机已有网格或求解任务正在运行，请等待完成或停止。')
            if shutil.disk_usage(self.root).free < 1024**3:
                raise ValueError('本机输出目录可用空间不足 1 GB，请清理已有结果或更换输出位置。')
            if kind=='mesh' and config.get('cell_budget',1500000)>1500000:
                import psutil
                if psutil.virtual_memory().available<4*1024**3:
                    raise ValueError('扩展网格预算需至少 4 GB 当前可用内存，请关闭占用较大的程序后重试。')
            job_id = uuid.uuid4().hex
            folder = self.root/(kind+'_'+job_id)
            folder.mkdir()
            self.jobs[job_id] = {'job_id':job_id,'kind':kind,'status':'queued','domain_id':domain['domain_id'],
                'scheme_id':domain['scheme_id'],'message':'等待本机处理','result':None}
            cancel = self.cancel_events[job_id] = threading.Event()
            self.active = job_id
            try:
                # Only immutable, verified inputs; no client-provided command or paths.
                if kind=='mesh':
                    shutil.copyfile(source/'fluid.brep',folder/'fluid.brep')
                    if sha256(folder/'fluid.brep')!=domain['artifact_sha256']['fluid.brep']:
                        raise ValueError('网格输入拷贝校验失败。')
                else:
                    shutil.copyfile(source/'flow.msh',folder/'flow.msh')
                    if sha256(folder/'flow.msh')!=mesh['artifact_sha256']['flow.msh']:
                        raise ValueError('求解输入拷贝校验失败。')
                payload = {'kind':kind,'config':config,'domain':{k:v for k,v in domain.items() if k not in {'vertices','triangles','triangle_roles'}},
                    'mesh':{k:v for k,v in (mesh or {}).items() if k!='preview'},'fluent':self.fluent,
                    'gmsh_path':self.domains.models.gmsh_path,'engine':'mesh-flow-1',
                    'implementation_sha256':{name:sha256(ROOT/name) for name in IMPLEMENTATION}}
                save(folder/'job.json',payload)
                threading.Thread(target=self._run,args=(job_id,folder,payload,cancel),daemon=True).start()
            except Exception:
                self.active = None
                del self.jobs[job_id]
                del self.cancel_events[job_id]
                raise
            return copy.deepcopy(self.jobs[job_id])

    def _run(self, job_id, folder, payload, cancel):
        process,job = None,None
        candidate = None
        try:
            if cancel.is_set():
                raise InterruptedError('任务已由用户停止。')
            kind = payload['kind']
            layered=payload.get('config',{}).get('mesh_method')=='layered-polyhex'
            python = self.fluent['python'] if kind=='flow' or layered else sys.executable
            argv = [python,'-u',str(ROOT/'nozzle_platform/workflow_worker.py'),str(folder)]
            job = WindowsJob() if os.name=='nt' else None
            with (folder/'worker.log').open('wb') as log:
                options = {'creationflags':subprocess.CREATE_NO_WINDOW} if job else {'start_new_session':True}
                process = subprocess.Popen(argv,cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,shell=False,**options)
                if job:
                    job.assign(process)
                with self.lock:
                    self.jobs[job_id].update(status='running',message='正在生成体网格' if kind=='mesh' else '正在启动 Fluent 并检查网格')
                started = time.monotonic()
                while process.poll() is None:
                    if cancel.wait(.5):
                        raise InterruptedError('任务已由用户停止。')
                    if time.monotonic()-started > (900 if kind=='mesh' else payload['config'].get('time_budget_seconds',1800)):
                        raise TimeoutError('任务超过运行时间预算，已终止。')
                    progress = folder/'progress.json'
                    progress_data={}
                    if progress.is_file():
                        try:
                            progress_data=json.loads(progress.read_text(encoding='utf-8'))
                            message=progress_data['message']
                            with self.lock:
                                self.jobs[job_id]['message'] = message
                        except (ValueError,KeyError,OSError):
                            pass
                    if (payload['config'].get('convergence_mode')=='gpu-validation'
                            and payload['config'].get('compute_device','cpu')=='cpu'
                            and progress_data.get('compute_started_monotonic') is not None
                            and time.monotonic()-progress_data['compute_started_monotonic']>60):
                        raise TimeoutError('CPU 接入诊断达到 60 秒计算上限，已停止。')
                if not (folder/'result.json').is_file():
                    raise ValueError('本次处理未返回完整结果，请检查本地 worker.log 后重试。')
                response = json.loads((folder/'result.json').read_text(encoding='utf-8'))
                if process.returncode or not response['ok']:
                    with self.lock:
                        self.jobs[job_id]['error_code'] = response.get('error_code','worker_failed')
                    raise ValueError(response.get('error','任务未完成，请检查本地记录。'))
                if cancel.is_set():
                    raise InterruptedError('任务已由用户停止。')
                result = response['result']
                names = ['flow.msh','flow.gmsh.msh','mesh_arrays.npz'] if kind=='mesh' else ['final.cas.h5','final.dat.h5','history.csv','residuals.csv','fluent.trn','configured.json','mesh_quality.json','quality_history.json']
                if kind=='mesh' and layered:
                    names=['flow.msh','mesh_arrays.npz','native_mesh.cas.h5','meshing.trn','mesh_controls.json',
                           'native_workflow.json','native_quality.json','geometry_quality.json','surface.msh','volume_refinement.json','volume_controls.json']
                if kind=='flow' and payload['config'].get('convergence_mode')=='grid-verification':
                    names.append('outlet_profile.json')
                    names.append('interior_profile.json')
                if kind=='flow' and payload['config'].get('convergence_mode')=='gpu-validation':
                    names.append('execution_profile.json')
                    if payload['config'].get('compute_device')=='gpu':names.append('device_evidence.json')
                result.update(kind=kind,result_id=job_id,**{k:payload['config'][k] for k in ('domain_id','scheme_id')},
                    elapsed_seconds=time.monotonic()-started,
                    config=payload['config'],source_sha256=payload['domain']['source_sha256'],
                    engine=payload['engine'],implementation_sha256=payload['implementation_sha256'],
                    domain_artifact_sha256=payload['domain']['artifact_sha256'],
                    artifact_sha256={name:sha256(folder/name) for name in names})
                if kind=='flow':
                    result['mesh_id'] = payload['config']['mesh_id']
                    result['mesh_artifact_sha256'] = payload['mesh']['artifact_sha256']
                save(folder/'report.json',{k:v for k,v in result.items() if k!='preview'})
                candidate = result
        except Exception as error:
            with self.lock:
                self.jobs[job_id].update(status='cancelled' if isinstance(error,InterruptedError) else 'failed',message=str(error),result=None)
                if isinstance(error,TimeoutError):
                    self.jobs[job_id]['error_code'] = 'time_budget_exceeded'
                elif isinstance(error,InterruptedError):
                    self.jobs[job_id]['error_code'] = 'user_cancelled'
        finally:
            try:
                if job:
                    job.close()
                elif process and process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL)
                if process:
                    process.wait(timeout=10)
            except Exception as error:
                candidate = None
                with self.lock:
                    self.jobs[job_id].update(status='failed',message='任务进程清理失败：'+str(error),result=None)
            # Fluent may have spawned tracked processes outside the launcher's tree.
            owned = folder/'owned_processes.json'
            if owned.exists():
                try:
                    from coldflow_trial.processes import cleanup_records
                    cleanup = cleanup_records(json.loads(owned.read_text(encoding='utf-8')))
                    save(folder/'cleanup.json',cleanup)
                    if not cleanup['all_tracked_processes_exited']:
                        candidate = None
                        with self.lock:
                            self.jobs[job_id].update(status='failed',message='Fluent 进程退出尚未核实，请检查本地记录。',result=None)
                            self.results.pop(job_id,None)
                            self.folders.pop(job_id,None)
                except Exception as error:
                    candidate = None
                    with self.lock:
                        self.jobs[job_id].update(status='failed',message='进程清理检查失败：'+str(error),result=None)
                        self.results.pop(job_id,None)
                        self.folders.pop(job_id,None)
            if (folder/'native_drive.json').exists():
                try:
                    from nozzle_platform.layered_mesh import cleanup_native_mapping
                    cleanup_native_mapping(folder)
                except Exception as error:
                    candidate=None
                    with self.lock:
                        self.jobs[job_id].update(status='failed',message='本次临时工作路径清理失败：'+str(error),result=None)
            with self.lock:
                if not candidate:
                    # Only whitelisted, server-generated diagnostics; never
                    # expose job.json, native paths or licensing credentials.
                    diagnostics = {}
                    for name,fields in [('mesh_quality.json',('mesh_quality_accepted','minimum_fluent_orthogonal_quality','maximum_fluent_aspect_ratio','mesh_quality_limits')),
                                        ('partial.json',('iterations_completed','convergence_assessment'))]:
                        try:
                            data = json.loads((folder/name).read_text(encoding='utf-8'))
                            diagnostics.update({key:data[key] for key in fields if key in data})
                        except (OSError,ValueError,KeyError):
                            pass
                    if diagnostics:
                        self.jobs[job_id]['diagnostics'] = diagnostics
                if candidate:
                    if cancel.is_set():
                        self.jobs[job_id].update(status='cancelled',message='任务已由用户停止。',result=None)
                    else:
                        self.results[job_id],self.folders[job_id] = candidate,folder
                        self.jobs[job_id].update(status='completed',message='体网格已生成并检查' if payload['kind']=='mesh' else '冷流试算已结束',result=candidate)
                self.active = None
                save(folder/'task.json',self.jobs[job_id])

    def status(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('任务不存在；重启平台后请重新运行。')
            return copy.deepcopy(self.jobs[job_id])

    def stop(self, job_id):
        with self.lock:
            if job_id!=self.active and job_id!=self.reservation:
                raise ValueError('该任务已结束或不存在。')
            self.cancel_events[job_id].set()
            if job_id==self.reservation and self.active:
                self.cancel_events[self.active].set()
            self.jobs[job_id]['message'] = '正在停止任务并清理本次进程'
        return {'stopping':True}

    def export(self, result_id, domain_id, scheme_id):
        result,folder = self.get_result(result_id,domain_id,scheme_id)
        output = folder/(result['kind']+'_'+result_id[:10]+'.zip')
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
            for name in result['artifact_sha256']:
                bundle.write(folder/name,name)
            bundle.writestr('report.json',json.dumps({k:v for k,v in result.items() if k!='preview'},ensure_ascii=False,indent=2))
            if result['kind']=='study':
                for row in result['rows']:
                    for key,child_kind in [('mesh_id','mesh'),('flow_id','flow')]:
                        if not row.get(key):continue
                        child,child_folder=self.get_result(row[key],domain_id,scheme_id,child_kind)
                        prefix=row['level']+'/'+child_kind+'/'
                        for name in child['artifact_sha256']:
                            bundle.write(child_folder/name,prefix+name)
                        bundle.writestr(prefix+'report.json',json.dumps({k:v for k,v in child.items() if k!='preview'},ensure_ascii=False,indent=2))
            bundle.writestr('README.txt',('M13 本地工程验证与三档网格对比\nflow.msh / mesh_arrays.npz 单位 m；平台显示 mm。\n'
                '网格方法和壁面层请求参数见各档 report.json；局部层数可能被原生网格器截断。Gmsh minSICN 与 Fluent 正交质量为不同指标。\n'
                '局部体积加密的实际参数见 volume_refinement.json，实际区域单元尺度见 geometry_quality.json。加密范围不改变物理边界。\n'
                '启用局部加密时显式启用体积尺寸场；请求及原生调整后的尺寸控制见 volume_controls.json。\n'
                '冷流使用非反应氢气/空气、稳态层流；普通试算为一阶迎风，整体网格验证为二阶迎风/二阶压力；实际格式见各档 report.json。达到步数预算不表示收敛。\n'
                '正交质量需 >= 0.1 且长宽比 <= 100（本模板筛选值），修复过程见 quality_history.json。\n'
                '普通试算连续两个完整 25 步批次通过且至少 100 步；整体网格验证采用更严格门限、连续三批次且至少 200 步，另查内部截面与出口场迭代漂移。逐步残差见 residuals.csv。\n'
                '三档对比见 comparison.json / comparison.csv，各档原始文件位于 coarse/medium/fine。\n'
                '整体验证检查两路压降、迭代噪声、三网格 GCI 及内部截面、出口共同采样场；通过仅适用于本工况所检指标，不等于全场/燃烧网格独立性或实验验证。判定范围与依据见 report.json。\n'
                '工况参数、来源哈希、质量指标与收敛状态见 report.json。不得用本试算判断燃烧性能。\n').encode('utf-8'))
        key = uuid.uuid4().hex
        with self.domains.designs.lock:
            self.domains.designs.downloads[key] = output
        return {'download_url':'/downloads/'+key,'filename':result['kind']+'_'+result_id[:10]+'.zip'}
