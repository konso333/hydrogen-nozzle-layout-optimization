import numpy as np
import pytest
from nozzle_platform.species_flux_benchmark import channel_mesh,parse_report,assess_report,classification,EXPECTED_RATE


def test_mesh_uses_consistent_native_boundary_types_and_known_rate(tmp_path):
    p=tmp_path/'channel.msh';m=channel_mesh(p);text=p.read_text()
    assert m['cells']==256 and m['expected_mass_flow_kg_s']==pytest.approx(EXPECTED_RATE)
    for zone,kind,name in [(4,'velocity-inlet','inlet'),(5,'pressure-outlet','outlet'),(6,'symmetry','sides')]:
        assert f'(39 ({zone} {kind} {name} 1)())' in text
    assert m['max_area_closure_error_m2']<1e-16
    with pytest.raises(ValueError):channel_mesh(p)


def report(bad=False):
    q='0 0 0 1e-5' if bad else '1e-5 0 0 0'
    v='0 0 0 -1e-5' if bad else '-1e-5 0 0 0'
    return f'M24_BEGIN_fresh\nspecies: (h2 o2 h2o n2) kg/s\nzone 4 (inlet): ({q})\nzone 5 (outlet): ({v})\nnet species-mass-flow: (0 0 0 0)\nM24_END_fresh\n'


def test_zero_net_and_correct_total_do_not_hide_wrong_species():
    assert assess_report(parse_report(report(),'fresh'),1e-5)['passed']
    assert not assess_report(parse_report(report(True),'fresh'),1e-5)['passed']


@pytest.mark.parametrize('replacement',[('kg/s','g/s'),('h2 o2 h2o n2','n2 o2 h2o h2'),('zone 5','zone 4'),('1e-5 0 0 0','nan 0 0 0'),('net species-mass-flow: (0 0 0 0)','net species-mass-flow: (1 0 0 0)')])
def test_wrong_units_order_duplicates_nonfinite_or_sum_are_rejected(replacement):
    with pytest.raises(ValueError):parse_report(report().replace(*replacement),'fresh')


def test_comparison_does_not_claim_a_root_cause_or_nozzle_qualification():
    a=dict(saved_fields_passed=True,fresh_report_passed=False)
    assert 'not_exclusive_to_GPU' in classification(a,a)
    assert 'unconfirmed' in classification(dict(a,fresh_report_passed=True),a)
    assert 'remains open' in classification(dict(a,fresh_report_passed=True),dict(a,fresh_report_passed=True))
    assert 'inconclusive' in classification(dict(a,saved_fields_passed=False),a)


def test_zero_step_initial_file_requires_two_matching_native_counters(tmp_path):
    import h5py
    from nozzle_platform.species_flux_benchmark import iteration_count
    p=tmp_path/'initial.dat.h5'
    with h5py.File(p,'w') as f:f.create_dataset('settings/Data Variables',data=np.array([b'(residuals/wb-iteration-count 0) (numerics/flexible/iteration-count 0)']))
    assert iteration_count(p)==0
    with h5py.File(p,'w') as f:f.create_dataset('settings/Data Variables',data=np.array([b'(residuals/wb-iteration-count 0) (numerics/flexible/iteration-count 1)']))
    with pytest.raises(ValueError):iteration_count(p)


def test_read_only_report_preserves_limits_and_does_not_promote_nozzle_scope(tmp_path):
    from nozzle_platform.species_flux_benchmark import build_report
    from nozzle_platform.review_reports import install_review,ReviewStore
    from nozzle_platform.workflows import save
    path=tmp_path/'audit.json';save(path,{'scope':'synthetic test'})
    (tmp_path/'native_reports.txt').write_text('SYNTHETIC native reference',encoding='utf-8')
    cpu=dict(actual_added_iterations=5,saved_fields_passed=True,fresh_report_passed=True,compute_wall_seconds=.1,reports={})
    gpu=dict(cpu,fresh_report_passed=False)
    audit=dict(status='pure_channel_cpu_gpu_independently_audited',scientific_eligible=False,
        backends={'cpu':cpu,'gpu':gpu},classification=classification(cpu,gpu))
    report,files=build_report(audit,path,'m24_synthetic')
    assert report['statuses']['physical_scope']['state']=='pending' and not report['scientific_eligible']
    assert 'CPU报告与解析答案一致' in report['conclusion']
    install_review(tmp_path/'reports',report,files)
    assert ReviewStore(tmp_path/'reports').get('m24_synthetic')['metrics'][0]['value']==5
    gpu['actual_added_iterations']=1
    short,_=build_report(audit,path,'m24_synthetic');assert short['metrics'][1]['value']==1
    audit['classification']='all passed'
    with pytest.raises(ValueError):build_report(audit,path,'m24_synthetic')


def test_hidden_options_require_independent_native_values_instead_of_assumed_defaults():
    import copy
    from nozzle_platform.species_flux_benchmark import physical_signature
    options=dict(inlet_diffusion=False,diffusion_energy_source=True,thermal_diffusion=False,multi_component_diffusion=False)
    inlet=dict(momentum=dict(velocity_specification_method='normal',reference_frame='Absolute',velocity_magnitude=1),thermal={},species={})
    outlet=dict(momentum=dict(backflow_reference_frame='Absolute',gauge_pressure=0,backflow_dir_spec_method='normal',backflow_pressure_spec='total',prevent_reverse_flow=False),thermal={},species={})
    state=dict(general={},materials={},models=dict(viscous={'model':'laminar'},energy={},species=dict(reactions={'enable_volumetric_reactions':False},options=options,model={})),
        boundary_conditions=dict(velocity_inlet={'inlet':inlet},pressure_outlet={'outlet':outlet},symmetry={'sides':{}}))
    hidden=copy.deepcopy(state)
    for key in ['thermal_diffusion','multi_component_diffusion']:hidden['models']['species']['options'].pop(key)
    with pytest.raises(ValueError):physical_signature(hidden)
    assert physical_signature(hidden,dict(thermal_diffusion=False,multi_component_diffusion=False))==physical_signature(state)
    assert physical_signature(hidden,dict(thermal_diffusion=True,multi_component_diffusion=False))!=physical_signature(state)
