"""Device failure must stop; no hidden physics conversion or formal acceptance."""
import copy
import os
import sys
from types import SimpleNamespace,ModuleType

import pytest

from nozzle_platform import solver_device as gpu
from nozzle_platform.workflows import normalize_flow
from tests.test_platform_workflows import flow_request


def request(**changes):
    return flow_request(compute_device='gpu', transport_model=gpu.GPU_TRANSPORT,
        convergence_mode=gpu.GPU_MODE, iterations=25, **changes)


def test_original_cpu_request_keeps_original_contract():
    result=normalize_flow(flow_request())
    assert result['time_budget_seconds']==1800
    assert gpu.validate_selection(result)==('cpu',gpu.ORIGINAL_TRANSPORT)


@pytest.mark.parametrize('changes',[
    {'compute_device':'auto'}, {'compute_device':{}}, {'transport_model':{}},
    {'transport_model':gpu.ORIGINAL_TRANSPORT}, {'convergence_mode':'grid-verification'},
    {'convergence_mode':'trial'}, {'iterations':101}, {'time_budget_seconds':119},
    {'time_budget_seconds':301}, {'time_budget_seconds':True}])
def test_gpu_rejects_unsafe_or_unbounded_selection_before_launch(changes):
    data=request();data.update(changes)
    with pytest.raises(ValueError):normalize_flow(data)


def test_gpu_short_test_does_not_inherit_thirty_minute_budget():
    assert normalize_flow(request())['time_budget_seconds']==240
    data=request();data['compute_device']='cpu'
    assert normalize_flow(data)['iterations']==25
    data['iterations']=26
    with pytest.raises(ValueError):normalize_flow(data)
    data.update(compute_device='gpu',iterations=100)
    assert normalize_flow(data)['iterations']==100


@pytest.mark.parametrize('mode',['uniform','chamber'])
@pytest.mark.parametrize('device',['cpu','gpu'])
def test_study_rejects_short_mode_before_it_can_be_overwritten(mode,device):
    from nozzle_platform.mesh_study import normalize
    with pytest.raises(ValueError,match='接入短测'):
        normalize(dict(domain_id='d',scheme_id='s',mesh={},flow=dict(convergence_mode=gpu.GPU_MODE,
            compute_device=device),refinement_mode=mode,refinement_ratio=1.25,tolerance_percent=2))


def state():
    mixture=dict(density={'option':'incompressible-ideal-gas'},mass_diffusivity={'option':'kinetic-theory'},
        viscosity={'option':gpu.GPU_TRANSPORT},thermal_conductivity={'option':gpu.GPU_TRANSPORT},
        species={'volumetric_species':{n:dict(viscosity=dict(option='value',value=mu),
            thermal_conductivity=dict(option='value',value=k)) for n,(mu,k) in gpu.SPECIES_TRANSPORT.items()}})
    return dict(materials={'mixture':{'hydrogen-air':mixture}},methods={'warped_face_gradient_correction':{'enable':False}})


def test_live_material_guard_detects_import_or_solver_changes():
    s=state();gpu.verify_transport(s,request())
    for key in ['viscosity','thermal_conductivity','density','mass_diffusivity']:
        changed=copy.deepcopy(s);changed['materials']['mixture']['hydrogen-air'][key]['option']='value'
        with pytest.raises(gpu.GPUCompatibilityError):gpu.verify_transport(changed,request())
    changed=copy.deepcopy(s);changed['materials']['mixture']['hydrogen-air']['species']['volumetric_species']['h2']['viscosity']['value']*=2
    with pytest.raises(gpu.GPUCompatibilityError):gpu.verify_transport(changed,request())
    s['methods']['warped_face_gradient_correction']['enable']=True
    with pytest.raises(gpu.GPUCompatibilityError):gpu.verify_transport(s,request())


def test_gpu_rejects_low_quality_without_cpu_mesh_conditioning(tmp_path):
    from tests.test_platform_mesh_quality import FakeSession
    from nozzle_platform.mesh_quality import condition_mesh
    session=FakeSession(tmp_path,[(.05,40)])
    with pytest.raises(ValueError):condition_mesh(session,tmp_path,lambda _:None,allow_conditioning=False)
    assert session.calls==[]


@pytest.mark.parametrize('changed',[False,True])
def test_static_case_is_reused_only_when_all_live_settings_stay_identical(tmp_path,monkeypatch,changed):
    import coldflow_trial.solver
    configured={'methods':{'pressure':'second-order'}}
    actual=configured if not changed else {'methods':{'pressure':'first-order'}}
    monkeypatch.setattr(coldflow_trial.solver,'snapshot',lambda _:actual)
    calls=[]
    session=SimpleNamespace(settings=SimpleNamespace(file=SimpleNamespace(write_data=lambda **k:calls.append(k))))
    (tmp_path/'configured.cas.h5').write_bytes(b'immutable-case')
    if changed:
        with pytest.raises(gpu.GPUCompatibilityError):gpu.save_validation_data(session,tmp_path,configured)
        assert not (tmp_path/'final.cas.h5').exists() and calls==[]
    else:
        gpu.save_validation_data(session,tmp_path,configured)
        assert (tmp_path/'final.cas.h5').read_bytes()==b'immutable-case'
        assert calls==[dict(file_name='final.dat.h5')]


@pytest.mark.parametrize('launch_error',[False,True])
def test_native_gpu_launch_has_no_cpu_retry_and_restores_environment(tmp_path,monkeypatch,launch_error):
    import importlib.metadata
    import coldflow_trial.solver
    import nozzle_platform.layered_mesh
    calls=[]
    def launch(**kwargs):
        calls.append(kwargs)
        if launch_error:raise RuntimeError('GPU license rejected')
        return 'native-gpu-session'
    fake=SimpleNamespace(config=SimpleNamespace(),launch_fluent=launch)
    # Bind a tiny fake module; the test must never start Fluent or touch a drive mapping.
    monkeypatch.setitem(sys.modules,'ansys.fluent.core',fake)
    parent=ModuleType('ansys');parent.__path__=[]
    fluent_module=ModuleType('ansys.fluent');fluent_module.__path__=[]
    fluent_module.core=fake;parent.fluent=fluent_module
    monkeypatch.setitem(sys.modules,'ansys',parent)
    monkeypatch.setitem(sys.modules,'ansys.fluent',fluent_module)
    monkeypatch.setattr(importlib.metadata,'version',lambda _: '0.42.1')
    monkeypatch.setattr(gpu.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='0, Mock RTX, 8192, 6000'))
    monkeypatch.setattr(nozzle_platform.layered_mesh,'create_native_mapping',lambda _:str(tmp_path))
    monkeypatch.setattr(coldflow_trial.solver,'launch_session',lambda *a:pytest.fail('CPU fallback attempted'))
    monkeypatch.setenv('APPDATA','original-user-profile')
    settings=dict(config=request(),fluent=dict(executable='fluent.exe',product_version='25.2.0',
        processes=2,start_timeout_seconds=120,license_server=None))
    if launch_error:
        with pytest.raises(RuntimeError,match='license rejected'):gpu.launch(settings,tmp_path)
    else:assert gpu.launch(settings,tmp_path)=='native-gpu-session'
    assert len(calls)==1 and calls[0]['gpu']==[0] and calls[0]['processor_count']==1
    assert calls[0]['precision']=='double' and calls[0]['additional_arguments']=='-license=enterprise'
    assert calls[0]['env']['APPDATA']=='original-user-profile'
    assert os.environ['APPDATA']=='original-user-profile'
