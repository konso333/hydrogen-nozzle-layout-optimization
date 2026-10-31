"""Explicit profiles remain bounded and native saved settings cannot be forged."""
from types import SimpleNamespace
import h5py
import pytest
from nozzle_platform import species_controls as controls
from nozzle_platform.workflows import normalize_flow
from tests.test_platform_gpu import request
from tests.test_platform_workflows import flow_request


def test_legacy_requests_keep_their_canonical_contract():
    assert 'species_solver_control' not in normalize_flow(request())
    assert 'species_solver_control' not in normalize_flow(flow_request())


@pytest.mark.parametrize('value',[None,True,{},'custom','(rpsetvar x 0)'])
def test_profile_is_a_fixed_enum(value):
    with pytest.raises(ValueError):normalize_flow(request(species_solver_control=value))


@pytest.mark.parametrize('device,mode',[('cpu','trial'),('cpu','gpu-validation'),('gpu','grid-verification')])
def test_experiment_cannot_escape_gpu_short_test(device,mode):
    data=flow_request(compute_device=device,convergence_mode=mode,species_solver_control=controls.STRICT)
    with pytest.raises(ValueError):normalize_flow(data)


def test_experiment_preserves_existing_iteration_limit():
    data=request(species_solver_control=controls.STRICT);data['iterations']=100
    assert normalize_flow(data)['species_solver_control']==controls.STRICT
    data['iterations']=101
    with pytest.raises(ValueError):normalize_flow(data)


def test_standard_never_writes_solver_controls():
    session=SimpleNamespace(scheme_eval=SimpleNamespace(eval=lambda _:pytest.fail('Changed standard controls')))
    controls.apply(session,request())


def native_case(path,**changes):
    values={controls.KEYS[0]:1e-16,controls.KEYS[1]:[1e-6]*3,controls.KEYS[2]:[1e-6]*3,
            controls.KEYS[3]:[100]*3,controls.KEYS[4]:[1]*3};values.update(changes)
    text=' '.join('('+k+' '+('('+' '.join(map(str,v))+')' if isinstance(v,list) else str(v))+')' for k,v in values.items())
    with h5py.File(path,'w') as f:f['settings/Rampant Variables']=[text.encode()]
    return values


def test_saved_case_and_live_settings_both_have_to_match(tmp_path):
    path=tmp_path/'native.cas.h5';values=native_case(path)
    record=controls.saved(path,request(species_solver_control=controls.STRICT))
    controls.verify_live(SimpleNamespace(rp_vars=values.__getitem__),record)
    values['species/relax']=[.1]*3
    with pytest.raises(ValueError,match='计算期间'):controls.verify_live(SimpleNamespace(rp_vars=values.__getitem__),record)


@pytest.mark.parametrize('key,value',[(controls.KEYS[0],1e-20),(controls.KEYS[1],[.1]*3),
    (controls.KEYS[2],[.1]*3),(controls.KEYS[3],[5]*3),(controls.KEYS[4],[.1]*3)])
def test_saved_profile_cannot_silently_change_global_tolerance_or_relaxation(tmp_path,key,value):
    path=tmp_path/'native.cas.h5';native_case(path,**{key:value})
    with pytest.raises(ValueError,match='实际组分'):controls.saved(path,request(species_solver_control=controls.STRICT))


def test_absolute_profile_is_explicit_and_preserves_relaxation(tmp_path):
    data=request(species_solver_control=controls.STRICT_ABSOLUTE)
    assert normalize_flow(data)['species_solver_control']=='strict-absolute'
    path=tmp_path/'native.cas.h5';native_case(path,**{controls.KEYS[0]:1e-20})
    actual=controls.saved(path,data)
    assert actual['actual_values']['species/relax']==[1.]*3
    with pytest.raises(ValueError):controls.saved(path,request(species_solver_control=controls.STRICT))
    calls=[];controls.apply(SimpleNamespace(scheme_eval=SimpleNamespace(eval=calls.append)),data)
    assert len(calls)==4 and calls[-1]=="(rpsetvar 'gpuapp/solver/absolute-tolerance-double 1e-20)"
    assert all('species/relax' not in command for command in calls)


@pytest.mark.parametrize('fault', ['tolerance','relaxation'])
def test_absolute_profile_rejects_saved_parameter_drift(tmp_path,fault):
    path=tmp_path/'native.cas.h5';changes={controls.KEYS[0]:1e-20}
    changes[controls.KEYS[0] if fault=='tolerance' else controls.KEYS[4]]=1e-16 if fault=='tolerance' else [.1]*3
    native_case(path,**changes)
    with pytest.raises(ValueError):controls.saved(path,request(species_solver_control=controls.STRICT_ABSOLUTE))


@pytest.mark.parametrize('device,steps',[('cpu',25),('gpu',101)])
def test_absolute_profile_has_no_cpu_or_extra_iteration_escape(device,steps):
    data=request(species_solver_control=controls.STRICT_ABSOLUTE);data.update(compute_device=device,iterations=steps)
    with pytest.raises(ValueError):normalize_flow(data)
