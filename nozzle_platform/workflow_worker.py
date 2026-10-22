"""Isolated native-library entry for volume meshing and Fluent execution."""

import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0,str(ROOT))


def main(directory):
    from nozzle_platform.workflows import save
    payload = json.loads((directory/'job.json').read_text(encoding='utf-8'))
    try:
        if payload['kind']=='mesh':
            if payload.get('gmsh_path'):
                sys.path.insert(0,payload['gmsh_path'])
            import gmsh
            if payload['config'].get('mesh_method')=='layered-polyhex':
                from nozzle_platform.layered_mesh import generate
            else:
                from nozzle_platform.volume_mesh import generate
            gmsh.initialize([],readConfigFiles=False,interruptible=False)
            gmsh.logger.start()
            try:
                gmsh.option.setNumber('General.Terminal',1)
                gmsh.option.setNumber('General.NumThreads',1)
                result = generate(gmsh,payload,directory)
            finally:
                (directory/'gmsh.log').write_text('\n'.join(gmsh.logger.get()),encoding='utf-8')
                gmsh.finalize()
        else:
            from nozzle_platform.flow_solver import run
            result = run(payload,directory)
        response = {'ok':True,'result':result}
    except BaseException as error:
        from nozzle_platform.solver_device import GPUCompatibilityError
        traceback.print_exc()
        native_logs = '\n'.join(path.read_text(encoding='utf-8',errors='replace') for path in directory.glob('*.trn'))
        if isinstance(error,GPUCompatibilityError):
            response={'ok':False,'error_code':'gpu_incompatible','error':str(error)}
        elif 'Unexpected license problem' in native_logs:
            response = {'ok':False,'error_code':'fluent_license_unavailable',
                'error':'Fluent 返回许可证错误，尚未开始冷流求解。请先确认本机 Fluent 能正常启动，或在本地配置中填写已确认的学校许可服务器，再重试。体网格仍可查看和导出。'}
        else:
            rejected = directory/'mesh_quality.json'
            mesh_rejected = rejected.exists() and not json.loads(rejected.read_text(encoding='utf-8'))['mesh_quality_accepted']
            response = {'ok':False,'error':str(error) or type(error).__name__,
                'error_code':'mesh_quality_rejected' if mesh_rejected else 'worker_failed'}
    save(directory/'result.json',response)
    return 0 if response['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main(Path(sys.argv[1])))
