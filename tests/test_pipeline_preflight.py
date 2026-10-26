"""Preflight checks never start a CAD/native worker or grant convergence."""
from types import SimpleNamespace
import copy
import pytest
from tests.test_pipelines import store,request


def resources(monkeypatch,memory=6):
    monkeypatch.setattr('psutil.virtual_memory',lambda:SimpleNamespace(available=memory*1024**3))
    monkeypatch.setattr('nozzle_platform.pipelines.shutil.disk_usage',lambda p:SimpleNamespace(free=20*1024**3))


def test_preflight_parameter_checks_report_pending_geometry_and_runtime(store,monkeypatch):
    resources(monkeypatch);config=request();before=copy.deepcopy(config)
    result=store.preflight(config)
    assert config==before and result['ready_for_cad']
    assert result['cases'][0]['expected_fuel_ports']==144
    assert result['cases'][0]['geometry_verified'] is False and not result['scientific_eligible']
    assert result['pending_checks'] and not store.workflows.calls and not store.folders


def test_unconfirmed_loaded_preset_can_be_checked_but_remains_unconfirmed(store,monkeypatch):
    resources(monkeypatch);config=request();config['domain']['template_confirmed']=False
    result=store.preflight(config)
    assert result['parameters_valid'] and not result['template_confirmed'] and not result['ready_for_cad']
    assert config['domain']['template_confirmed'] is False


@pytest.mark.parametrize('fault',['memory','geometry','chamber'])
def test_preflight_explains_resource_or_geometry_failures_without_solving(store,monkeypatch,fault):
    resources(monkeypatch,memory=3.9 if fault=='memory' else 6);config=request()
    if fault=='geometry':config['design']['radius_mm']=20
    elif fault=='chamber':config['domain']['chamber_length_mm']=1
    result=store.preflight(config);assert not result['ready_for_cad']
    if fault=='memory':assert not result['resources']['ready']
    else:assert not result['cases'][0]['parameters_valid'] and result['cases'][0]['message']
    assert not store.workflows.calls
