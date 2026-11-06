import copy
import numpy as np
import pytest
from nozzle_platform.coldflow_qualification import transport_review,assess_report,report_commands,continuation_decision
from tests.test_species_reports import text as native,summary


def state():
    from nozzle_platform.solver_device import SPECIES_TRANSPORT
    names=['h2','o2','h2o','n2']
    materials={name:{key:dict(option='value',value=value) for key,value in zip(
        ['molecular_weight','viscosity','thermal_conductivity'],[mw,*SPECIES_TRANSPORT[name]])}
        for name,mw in zip(names,[2.,32.,18.,28.])}
    return {'materials':{'mixture':{'hydrogen-air':dict(viscosity={'option':'mass-weighted-mixing-law'},
        thermal_conductivity={'option':'mass-weighted-mixing-law'},species=dict(volumetric=names,volumetric_species=materials))}}}


def test_empty_command_metadata_accepts_only_the_independently_loaded_single_domain():
    from nozzle_platform.coldflow_qualification import report_domain
    assert report_domain([], '     domains,\n\tmixture\n     zones,\n\twall')=='mixture'
    assert report_domain(['phase-1'],'')=='phase-1'
    for text in ['', 'domains,\nother\nzones,', 'domains,\nmixture\nother\nzones,']:
        with pytest.raises(ValueError):report_domain([],text)
    with pytest.raises(ValueError):report_domain(['unrecognized'],'domains,\nmixture\nzones,')


def test_actual_composition_comparison_does_not_promote_compatible_model():
    reference=state();before=copy.deepcopy(reference)
    y=np.array([[1.,0.,0.,0.],[.4,.1,0.,.5]])
    result=transport_review(reference,y)
    assert result['preferred_formal_candidate']=='original-ideal-mixture'
    assert result['differences_on_saved_composition']['viscosity']['absolute_relative_max']>0
    assert not result['formal_transport_qualified'] and not result['compatible_law_is_equivalent']
    assert reference==before and np.array_equal(y,np.array([[1.,0.,0.,0.],[.4,.1,0.,.5]]))


@pytest.mark.parametrize('bad',[np.array([[.9,0,0,0]]),np.array([[float('nan'),0,0,1]])])
def test_composition_is_never_repaired(bad):
    with pytest.raises(ValueError):transport_review(state(),bad)


@pytest.mark.parametrize('label',['x\n/solve/iterate 200','../x','x)'])
def test_report_delimiters_reject_command_injection(label):
    with pytest.raises(ValueError):report_commands(label)


def test_fresh_native_report_is_crosschecked_without_claiming_diffusion():
    text='M23_BEGIN_fresh\n'+native()+'\nM23_END_fresh\n'
    result=assess_report(text,'fresh',summary(),inlet_diffusion_enabled=False)
    assert result['assessment']['report_consistent_with_saved_boundary']
    assert not result['assessment']['full_species_flux_verified']
    with pytest.raises(ValueError):assess_report(text+text,'fresh',summary(),inlet_diffusion_enabled=False)


def test_same_total_with_all_fuel_assigned_to_nitrogen_is_rejected():
    text='M23_BEGIN_fresh\n'+native(bad=True)+'\nM23_END_fresh\n'
    result=assess_report(text,'fresh',summary(),inlet_diffusion_enabled=False)
    assert not result['assessment']['report_consistent_with_saved_boundary']


def test_actual_literal_scheme_delimiters_are_supported_without_decoding_echoes():
    raw='\\nM23_BEGIN_fresh\\n\n'+native()+'\\nM23_END_fresh\\n\\nM23_BEGIN_other\\n\n'+native()+'\\nM23_END_other\\n'
    assert assess_report(raw,'fresh',summary(),inlet_diffusion_enabled=False)['assessment']['report_consistent_with_saved_boundary']
    assert assess_report(raw,'other',summary(),inlet_diffusion_enabled=False)['assessment']['report_consistent_with_saved_boundary']
    echo='> (display "\\nM23_BEGIN_fresh\\n")\n'+native()+'> (display "\\nM23_END_fresh\\n")\n'
    with pytest.raises(ValueError):assess_report(echo,'fresh',summary(),inlet_diffusion_enabled=False)


@pytest.mark.parametrize('missing',['saved_fields_healthy','transport_qualified','fresh_reports_consistent','independent_diffusion_verified'])
def test_each_missing_evidence_stops_continuation(missing):
    evidence={name:True for name in ['saved_fields_healthy','transport_qualified','fresh_reports_consistent','independent_diffusion_verified']}
    evidence[missing]=False
    result=continuation_decision(**evidence)
    assert result['missing_evidence']==[missing] and not result['controlled_continuation_eligible']
    assert not result['scientific_eligible']


def test_consistent_interfaces_cannot_replace_independent_diffusion():
    result=continuation_decision(saved_fields_healthy=True,transport_qualified=True,fresh_reports_consistent=True,independent_diffusion_verified=False)
    assert not result['full_species_flux_verified'] and not result['controlled_continuation_eligible']
    with pytest.raises(ValueError):continuation_decision(saved_fields_healthy=True,transport_qualified=None,fresh_reports_consistent=True,independent_diffusion_verified=False)


def test_platform_report_shows_pending_physics_and_no_invented_full_flux(tmp_path):
    from nozzle_platform.coldflow_qualification import build_report
    from nozzle_platform.review_reports import install_review,ReviewStore
    from nozzle_platform.workflows import save
    audit=tmp_path/'audit.json';save(audit,{'scope':'synthetic test'})
    (tmp_path/'native_reports.txt').write_text('SYNTHETIC native diagnostic',encoding='utf-8')
    data=dict(status='coldflow_qualification_independently_audited',scientific_eligible=False,
        actual_added_gpu_iterations=1,cpu_solver_iterations=0,saved_fields_healthy=True,reports={},
        transport_review=transport_review(state(),np.array([[.5,.1,0,.4]])),
        decision=continuation_decision(saved_fields_healthy=True,transport_qualified=False,fresh_reports_consistent=False,independent_diffusion_verified=False))
    report,files=build_report(data,audit,'m23_synthetic')
    assert report['metrics'][0]['value']==1 and report['metrics'][-1]['value'] is None
    assert report['statuses']['physical_scope']['state']=='pending' and report['statuses']['convergence']['state']=='not_met'
    install_review(tmp_path/'reports',report,files)
    assert not ReviewStore(tmp_path/'reports').get('m23_synthetic')['scientific_eligible']
    data['decision']['full_species_flux_verified']=True
    with pytest.raises(ValueError):build_report(data,audit,'m23_synthetic')
