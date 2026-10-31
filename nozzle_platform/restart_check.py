"""Separate native readability, field preservation and scientific convergence."""
import math
import re
import shutil
from pathlib import Path

CELL_FIELDS=('SV_U','SV_V','SV_W','SV_P','SV_T','SV_DENSITY','SV_MU_LAM','SV_Y')
FACE_FIELDS=('SV_FLUX','SV_Y')


class RestartCleanupError(ValueError):pass


def compare(case_path,before_path,after_path):
    import h5py
    import numpy as np
    from coldflow_trial.solver import saved_iteration
    from nozzle_platform.workflows import sha256
    checks=[];closure={}
    with h5py.File(case_path,'r') as case,h5py.File(before_path,'r') as before,h5py.File(after_path,'r') as after:
        top=case['meshes/1/cells/zoneTopology']
        if list(top['minId'][()])!=[1] or len(top['maxId'])!=1:raise ValueError('重读检查要求完整的单个单元区。')
        count=int(top['maxId'][0])
        for part,keys in [('cells',CELL_FIELDS),('faces',FACE_FIELDS)]:
            for key in keys:
                groups=[data['results/1/phase-1/'+part+'/'+key] for data in [before,after]]
                if any(set(g)!={'1'} for g in groups):raise ValueError('重读场值分块未核验：'+key)
                datasets=[g['1'] for g in groups];arrays=[d[()] for d in datasets]
                ids=[]
                for d,a in zip(datasets,arrays):
                    low=np.asarray(d.attrs.get('minId',[]));high=np.asarray(d.attrs.get('maxId',[]))
                    if low.shape!=(1,) or high.shape!=(1,) or low[0]<1 or high[0]<low[0] or not np.isfinite(a).all():raise ValueError('重读场值编号或数值无效。')
                    expected=(int(high[0]-low[0]+1),4) if key=='SV_Y' else (int(high[0]-low[0]+1),)
                    if key!='SV_Y':a.shape=(-1,)
                    if a.shape!=expected or (part=='cells' and (int(low[0])!=1 or int(high[0])!=count)):raise ValueError('重读场值不完整：'+key)
                    ids.append((int(low[0]),int(high[0])))
                a,b=arrays
                if ids[0]!=ids[1] or a.shape!=b.shape:raise ValueError('重读前后字段编号改变。')
                delta=float(np.max(abs(a-b)));tolerance=max(1e-12 if key=='SV_Y' else 1e-30,float(np.max(abs(a)))*1e-12)
                checks.append(dict(part=part,field=key,maximum_absolute_change=delta,absolute_tolerance=tolerance,passed=delta<=tolerance,exactly_equal=bool(np.array_equal(a,b))))
                if key=='SV_Y':
                    closure[part]={label:float(abs(x.sum(axis=1)-1).max()) for label,x in [('before',a),('after',b)]}
    first,last=saved_iteration(before_path),saved_iteration(after_path)
    if first!=last:raise ValueError('重读前后实际迭代号改变。')
    return dict(native_read_completed=True,added_iterations=0,saved_iteration=first,
        field_preservation_passed=all(c['passed'] for c in checks),checks=checks,species_sum_max_error=closure,
        source_case_sha256=sha256(case_path),source_data_sha256=sha256(before_path),reopened_data_sha256=sha256(after_path),
        formal_physics_validated=False,numerical_criteria_met=False,grid_independence_verified=False)


def message(result):
    if not result.get('native_read_completed'):return '文件重读未完成；严格重启一致性未通过。'
    if result.get('field_preservation_passed'):return '文件可读取，所检字段重读保持通过；不代表求解收敛。'
    return '文件可读取，但部分字段重读有变化；严格重启一致性未通过。原始保存文件未改写。'


def journal(case_name):
    if not isinstance(case_name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.cas\.h5',case_name):raise ValueError('重读文件名无效。')
    return '; Native GPU, double precision. Read paired files without initialization or iteration.\n/file/read-case-data '+case_name+'\n'


def run_native(case_path,data_path,fluent,config,folder,cancel):
    """One isolated zero-step reader; failures do not rewrite accepted source data."""
    import json,sys
    from nozzle_platform.bounded import run
    from nozzle_platform.workflows import ROOT,IMPLEMENTATION,save,sha256
    from coldflow_trial.processes import cleanup_records
    from nozzle_platform.layered_mesh import cleanup_native_mapping
    folder.mkdir()
    spec=dict(case=str(case_path),data=str(data_path),fluent=fluent,config=config,
        reference_settings=json.loads((Path(case_path).parent/'configured.json').read_text(encoding='utf-8')),
        input_sha256={'case':sha256(case_path),'data':sha256(data_path)},
        implementation_sha256={n:sha256(ROOT/n) for n in IMPLEMENTATION})
    save(folder/'restart_job.json',spec);report=None
    try:
        code=run([fluent['python'],'-X','utf8','-u',str(ROOT/'nozzle_platform/restart_worker.py'),str(folder)],cwd=ROOT,log=folder/'worker.log',timeout=180,cancel=cancel)
        if not (folder/'restart_check.json').exists():raise ValueError('重读程序没有返回完整检查记录。')
        report=json.loads((folder/'restart_check.json').read_text(encoding='utf-8'))
        if code:report.update(native_read_completed=False,field_preservation_passed=False)
    finally:
        records=folder/'owned_processes.json'
        if records.exists():
            cleanup=cleanup_records(json.loads(records.read_text(encoding='utf-8')));save(folder/'cleanup.json',cleanup)
            if not cleanup['all_tracked_processes_exited']:raise RestartCleanupError('重读进程退出尚未核实。')
        if (folder/'native_drive.json').exists():
            try:cleanup_native_mapping(folder)
            except Exception as error:raise RestartCleanupError('重读临时路径清理失败。') from error
    if sha256(case_path)!=spec['input_sha256']['case'] or sha256(data_path)!=spec['input_sha256']['data']:raise ValueError('原始文件在重读期间改变。')
    return report
