"""Explicit native GPU validation; no CPU fallback or implicit material changes."""

import os
from pathlib import Path
import subprocess
import shutil


class GPUCompatibilityError(ValueError):
    pass


GPU_MODE = 'gpu-validation'
ORIGINAL_TRANSPORT = 'ideal-gas-mixing-law'
GPU_TRANSPORT = 'mass-weighted-mixing-law'
SPECIES_TRANSPORT = {
    'h2': (8.411e-6, .1672), 'o2': (1.919e-5, .0246),
    'h2o': (1.34e-5, .0261), 'n2': (1.663e-5, .0242),
}


def validate_selection(config):
    device = config.get('compute_device', 'cpu')
    transport = config.get('transport_model', ORIGINAL_TRANSPORT)
    mode = config.get('convergence_mode', 'trial')
    if not isinstance(device, str) or device not in {'cpu', 'gpu'}:
        raise ValueError('请选择 CPU 或 GPU。')
    if not isinstance(transport, str) or transport not in {ORIGINAL_TRANSPORT, GPU_TRANSPORT}:
        raise ValueError('混合物输运物性选项无效。')
    if device == 'gpu' and transport != GPU_TRANSPORT:
        raise GPUCompatibilityError('当前 GPU 不支持原理想气体混合黏度／导热率；请选择明确标注的 GPU 接入验证物性。程序不会自动修改物性或转用 CPU。')
    if (device == 'gpu' or transport != ORIGINAL_TRANSPORT) and mode != GPU_MODE:
        raise GPUCompatibilityError('GPU 与兼容物性目前仅开放接入短测，尚不能用于常规或三档网格收敛结论。')
    return device, transport


def launch(config, directory):
    """One native session; a failed GPU launch is never retried on CPU."""
    from coldflow_trial.solver import launch_session
    device, _ = validate_selection(config['config'])
    if device == 'cpu':
        return launch_session(config, directory)
    from importlib import metadata
    import ansys.fluent.core as pyfluent
    from nozzle_platform.workflows import save
    from nozzle_platform.layered_mesh import create_native_mapping
    if metadata.version('ansys-fluent-core') != '0.42.1':
        raise GPUCompatibilityError('GPU 接入需要已核验的 PyFluent 0.42.1。')
    fluent = config['fluent']
    gpu_id = fluent.get('gpu_id', 0)
    if type(gpu_id) is not int or not 0 <= gpu_id <= 7:
        raise GPUCompatibilityError('本机 GPU 编号需为 0～7 的整数。')
    command = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/nvidia-smi.exe'
    options = dict(capture_output=True, text=True, errors='replace', timeout=10)
    if os.name == 'nt': options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        probe = subprocess.run([str(command), f'--id={gpu_id}',
            '--query-gpu=index,name,memory.total,memory.free', '--format=csv,noheader,nounits'], **options)
        fields = probe.stdout.strip().split(',')
        if probe.returncode or len(fields) != 4 or float(fields[3]) < 1024:
            raise GPUCompatibilityError('GPU 不可用或当前空闲显存不足 1 GiB；已停止，不转用 CPU。')
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        if isinstance(error, GPUCompatibilityError): raise
        raise GPUCompatibilityError('无法核实本机 GPU 状态，已停止，不转用 CPU。') from error
    save(directory/'device_evidence.json', dict(compute_device='gpu', gpu_id=gpu_id,
        gpu_inventory=probe.stdout.strip(), precision='double', cpu_setup_processes=1,
        license_product='enterprise', cpu_fallback=False, memory_fit_guaranteed=False,
        purpose='GPU 接入短测，物性及收敛结果尚未正式验证'))
    appdata = os.environ.get('APPDATA')
    try:
        os.environ['APPDATA'] = str(directory/'controller-cache')
        pyfluent.config.fluent_server_info_dir = str(directory)
        pyfluent.config.launch_fluent_ip = '127.0.0.1'
        env = {'APPDATA': appdata} if appdata else {}
        if fluent.get('license_server'): env['ANSYSLMD_LICENSE_FILE'] = fluent['license_server']
        return pyfluent.launch_fluent(fluent_path=fluent['executable'], product_version=fluent['product_version'],
            dimension=3, precision='double', processor_count=1, gpu=[gpu_id], mode='solver', ui_mode='no_gui',
            cwd=create_native_mapping(directory), start_timeout=min(fluent['start_timeout_seconds'], 90), cleanup_on_exit=True,
            start_watchdog=False, start_transcript=False, py=False, additional_arguments='-license=enterprise', env=env)
    finally:
        if appdata is None: os.environ.pop('APPDATA', None)
        else: os.environ['APPDATA'] = appdata


def configure_transport(mix, transport):
    mix.viscosity.option = transport
    mix.thermal_conductivity.option = transport
    if transport == GPU_TRANSPORT:
        for name, (mu, k) in SPECIES_TRANSPORT.items():
            material = mix.species.volumetric_species[name]
            material.viscosity.set_state(dict(option='value', value=mu))
            material.thermal_conductivity.set_state(dict(option='value', value=k))


def verify_transport(state, config):
    device, transport = validate_selection(config)
    mix = state['materials']['mixture']['hydrogen-air']
    for prop in ['viscosity', 'thermal_conductivity']:
        if mix[prop]['option'] != transport:
            raise GPUCompatibilityError('Fluent 实际输运物性与所选模型不一致，已停止。')
    if mix['density']['option'] != 'incompressible-ideal-gas' or mix['mass_diffusivity']['option'] != 'kinetic-theory':
        raise GPUCompatibilityError('Fluent 实际密度或扩散模型发生变化，已停止。')
    if transport == GPU_TRANSPORT:
        for name, (mu, k) in SPECIES_TRANSPORT.items():
            species = mix['species']['volumetric_species'][name]
            for prop, value in [('viscosity', mu), ('thermal_conductivity', k)]:
                if species[prop] != dict(option='value', value=value):
                    raise GPUCompatibilityError('Fluent 实际组分物性与接入验证配置不一致。')
    if device == 'gpu' and state['methods']['warped_face_gradient_correction']['enable']:
        raise GPUCompatibilityError('GPU 接入验证要求显式关闭翘曲面梯度修正。')


def save_validation_data(session, directory, configured):
    """Static mesh/case stays immutable; write only new solution fields."""
    from coldflow_trial.solver import snapshot
    if snapshot(session) != configured:
        raise GPUCompatibilityError('接入验证期间配置发生变化，不能复用静态 case。')
    shutil.copyfile(directory/'configured.cas.h5', directory/'final.cas.h5')
    session.settings.file.write_data(file_name='final.dat.h5')
