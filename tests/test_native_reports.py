import pytest
from nozzle_platform.native_reports import parse_pressure,pressure_commands
from nozzle_platform.nozzle_gpu_probe import iteration_plan

def report(value='5.7753587',field='Total Pressure'):
    return 'Area-Weighted Average\n'+field+' [Pa]\n-----\n fuel_inlet '+value+'\n'

def test_native_printed_pressure_is_not_replaced_by_static_pressure():
    assert parse_pressure(report(),'fuel_inlet')==5.7753587
    with pytest.raises(ValueError):parse_pressure(report(field='Static Pressure'),'fuel_inlet')

@pytest.mark.parametrize('text',[report('nan'),report('inf'),report()+report(),report().replace('fuel_inlet','outlet'),report().replace('[Pa]','[psi]')])
def test_incomplete_ambiguous_nonfinite_or_wrong_units_rejected(text):
    with pytest.raises(ValueError):parse_pressure(text,'fuel_inlet')

def test_single_session_budget_and_preceding_composition_checkpoints():
    plan=iteration_plan(True,100);offset=0;positions={}
    for count,name in plan:offset+=count;positions[name]=offset
    assert list(positions.values())==[1,2,3,24,25,49,50,74,75,99,100]
    assert len(plan)==len(positions) and offset==100
    assert sum(n for n,_ in iteration_plan(True))==25
    with pytest.raises(ValueError):iteration_plan(False,100)
    with pytest.raises(ValueError):iteration_plan(True,101)

def test_report_commands_have_no_solver_iterations_and_only_known_files():
    text=pressure_commands(25)
    assert text.count('total-pressure')==3 and '/solve/' not in text
    with pytest.raises(ValueError):pressure_commands('25;quit')

def test_single_session_cannot_start_without_explicit_comparison_settings(tmp_path,monkeypatch):
    import sys
    import scripts.probe_nozzle_gpu_transport as runner
    target=tmp_path/'new'
    monkeypatch.setattr(sys,'argv',['probe','--cpu',str(tmp_path/'cpu'),'--gpu',str(tmp_path/'gpu'),'--output',str(target),'--session-steps','100'])
    with pytest.raises(AssertionError,match='M17 requires'):runner.main()
    assert not target.exists()
