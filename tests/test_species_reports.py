import pytest
from nozzle_platform.species_reports import parse_species_report, assess_species_report, report_block


def text(bad=False):
    fuel = '0 0 0 1e-6' if bad else '1e-6 0 0 0'
    net = '-1e-6 0 0 1e-6' if bad else '0 0 0 0'
    return ('species: (h2 o2 h2o n2)  kg/s\n'
            f'zone 5 (fuel_inlet): ({fuel})\n'
            'zone 4 (oxidizer_inlet): (0 2e-6 0 8e-6)\n'
            'zone 6 (outlet): (-1e-6 -2e-6 0 -8e-6)\n'
            f'zone 7 (wall): (0 0 0 0)\nnet species-mass-flow: ({net})\n')


def summary():
    return {'boundary_rates': {r: dict(mass_flow_kg_s=q, species_convective_flow_kg_s=y)
                              for r, q, y in [('fuel_inlet', 1e-6, [1e-6, 0, 0, 0]),
                                             ('oxidizer_inlet', 1e-5, [0, 2e-6, 0, 8e-6]),
                                             ('outlet', -1.1e-5, [-1e-6, -2e-6, 0, -8e-6]),
                                             ('wall', 0, [0, 0, 0, 0])]}}


def test_correct_totals_do_not_prove_complete_flux():
    result = assess_species_report(parse_species_report(text()), summary(), inlet_diffusion_enabled=False)
    assert result['report_consistent_with_saved_boundary']
    assert not result['full_species_flux_verified'] and result['full_species_flux_kg_s'] is None


def test_all_nitrogen_report_rejected_even_when_total_mass_matches():
    result = assess_species_report(parse_species_report(text(True)), summary(), inlet_diffusion_enabled=False)
    assert not result['report_consistent_with_saved_boundary']
    assert set(result['rejected_checks']) == {'pure_fuel:h2', 'pure_fuel:n2'}


@pytest.mark.parametrize('replace', [('kg/s', 'g/s'), ('h2 o2 h2o n2', 'n2 o2 h2o h2'),
    ('1e-6 0 0 0', 'nan 0 0 0'), ('zone 7 (wall): (0 0 0 0)\n', ''),
    ('net species-mass-flow: (0 0 0 0)', 'net species-mass-flow: (1 0 0 0)')])
def test_bad_units_missing_boundaries_nonfinite_and_wrong_net_rejected(replace):
    with pytest.raises(ValueError): parse_species_report(text().replace(*replace))


def test_duplicate_reports_rejected():
    with pytest.raises(ValueError): parse_species_report(text() + text())


def test_sentinel_cannot_silently_apply_to_another_boundary_condition():
    s = summary(); s['boundary_rates']['fuel_inlet']['species_convective_flow_kg_s'][0] *= .5
    with pytest.raises(ValueError): assess_species_report(parse_species_report(text()), s, inlet_diffusion_enabled=False)


def test_markers_ignore_echo_and_allow_literal_newline_escape():
    tr = '> (display "BEGIN_SPECIES_input\\n")\nBEGIN_SPECIES_input\\n\n' + text() + '> (display "END_SPECIES_input\\n")\n'
    assert parse_species_report(report_block(tr, 'input'))['net_kg_s'] == [0, 0, 0, 0]
    with pytest.raises(ValueError): report_block(tr, 'other')


@pytest.mark.parametrize('value',[True,None,'false'])
def test_inlet_diffusion_must_be_independently_disabled(value):
    with pytest.raises(ValueError):
        assess_species_report(parse_species_report(text()),summary(),inlet_diffusion_enabled=value)


def test_duplicate_zone_ids_and_invalid_saved_values_fail_closed():
    with pytest.raises(ValueError):parse_species_report(text().replace('zone 7','zone 5'))
    s=summary();s['boundary_rates']['fuel_inlet']['mass_flow_kg_s']=float('nan')
    with pytest.raises(ValueError):assess_species_report(parse_species_report(text()),s,inlet_diffusion_enabled=False)


def test_real_m17_printed_diagnostic_is_rejected():
    raw='''species: (h2 o2 h2o n2)  kg/s
zone 5 (fuel_inlet): (-0 -0 -0 6.4696008e-07)
zone 6 (outlet): (-0 -0 -0 -2.1218828e-05)
zone 4 (oxidizer_inlet): (-0 -0 -0 2.0566707e-05)
zone 7 (wall): (-0 -0 -0 0)
net species-mass-flow: (0 0 0 -5.1608335e-09)
'''
    parsed=parse_species_report(raw)
    s=summary()
    for role,mass in [('fuel_inlet',6.4696008e-7),('oxidizer_inlet',2.0566707e-5),('outlet',-2.1218828e-5)]:
        s['boundary_rates'][role]['mass_flow_kg_s']=mass
    s['boundary_rates']['fuel_inlet']['species_convective_flow_kg_s']=[6.4696008e-7,0,0,0]
    r=assess_species_report(parsed,s,inlet_diffusion_enabled=False)
    assert r['rejected_checks']==['pure_fuel:h2','pure_fuel:n2']


def test_zero_step_reader_cannot_claim_preserved_fields_after_a_material_change():
    from nozzle_platform.species_reports import field_preservation
    before={'fields':{'velocity':[1.,2.]},'y':[[1.,0.],[0.,1.]]}
    after={'fields':{'velocity':[1.,2.1]},'y':[[1.,0.],[0.,1.]]}
    assert field_preservation(before,before)['verified']
    assert not field_preservation(before,after)['verified']


def test_tiny_report_write_roundoff_is_recorded_with_explicit_tolerance():
    from nozzle_platform.species_reports import field_preservation
    before={'fields':{'velocity':[1.]},'y':[[1.,0.]]}
    after={'fields':{'velocity':[1.]},'y':[[1.-8e-14,8e-14]]}
    result=field_preservation(before,after)
    assert result['verified'] and result['checks'][-1]['maximum_absolute_change']>0


def test_invalid_reader_field_shape_cannot_be_treated_as_preserved():
    from nozzle_platform.species_reports import field_preservation
    before={'fields':{'velocity':[1.]},'y':[[1.,0.]]}
    after={'fields':{'velocity':[1.,2.]},'y':[[1.,0.]]}
    with pytest.raises(ValueError):field_preservation(before,after)


def test_fresh_gpu_journal_uses_one_step_and_reports_before_any_solution_write():
    from scripts.probe_species_flux_report import reader_journal
    s=reader_journal(True)
    assert s.count('/solve/iterate 1\n')==1 and s.count('/solve/')==1
    assert s.index('/solve/iterate') < s.index('BEGIN_SPECIES_fresh') < s.index('/file/write-data fresh')
    assert s.index('/file/read-data fresh') < s.index('BEGIN_SPECIES_reloaded')
    assert '/solve/' not in reader_journal(False)


@pytest.mark.parametrize('options',[['--backend','cpu-reader','--fresh-gpu-step'],
    ['--backend','gpu','--data-origin','original-cpu']])
def test_diagnostic_cannot_implicitly_switch_to_cpu_solving(tmp_path,monkeypatch,options):
    import sys
    from scripts import probe_species_flux_report as script
    monkeypatch.setattr(sys,'argv',['probe','--source',str(tmp_path/'source'),'--cpu',str(tmp_path/'cpu'),
                                  '--output',str(tmp_path/'out'),*options])
    with pytest.raises(ValueError):script.main()
    assert not (tmp_path/'out').exists()
