import copy
import pytest
from nozzle_platform.convergence_review import build_report


def fixture(tmp_path):
    audit=tmp_path/'audit.json';audit.write_text('{}',encoding='utf-8')
    (tmp_path/'native_reports.txt').write_text('raw diagnostic',encoding='utf-8')
    p=dict(iteration=2075,area_weighted_total_pressure_drop_Pa={'fuel_inlet':5.7,'oxidizer_inlet':8.3},
           h2_relative_convective_imbalance=.05,outlet_profile_relative_drift=.15,interior_profile_relative_drift=.014)
    data=dict(status='bounded_gpu_convergence_independently_audited',scientific_eligible=False,
              new_added_gpu_iterations=0,new_saved_checkpoints_verified=0,baseline_added_gpu_iterations=100,
              decision=dict(added_gpu_iterations=100,numerical_assessment={'numerical_criteria_met':False}),
              strict_convergence_verified=False,rows=[dict(phase='m17_baseline',point=p)])
    return data,audit


def test_zero_new_iterations_are_not_presented_as_a_new_100_step_run(tmp_path):
    data,audit=fixture(tmp_path);r,files=build_report(data,audit,'m18_test')
    assert r['metrics'][0]['value']==0 and r['metrics'][1]['value']==0
    assert r['table']['rows'][0][0]=='M17 历史基线'
    assert r['table']['rows'][0][5] is None
    assert r['statuses']['convergence']['state']=='not_met'
    assert not r['scientific_eligible']


def test_incomplete_checkpoint_evidence_cannot_get_a_pass_card(tmp_path):
    data,audit=fixture(tmp_path);data['new_added_gpu_iterations']=100
    with pytest.raises(ValueError):build_report(data,audit,'m18_test')


def test_new_run_and_baseline_remain_distinct(tmp_path):
    data,audit=fixture(tmp_path);data.update(new_added_gpu_iterations=100,new_saved_checkpoints_verified=11)
    row=copy.deepcopy(data['rows'][0]);row['phase']='m18';row['point']['iteration']=2175;data['rows'].append(row)
    data['decision']['added_gpu_iterations']=200
    r,_=build_report(data,audit,'m18_test')
    assert r['metrics'][0]['value']==100 and r['metrics'][2]['value']==100
    assert [r[0] for r in r['table']['rows']]==['M17 历史基线','M18 本轮']


def test_license_failure_has_a_specific_explanation(tmp_path):
    data,audit=fixture(tmp_path);data['reader_probes']=[{'license_rejected':True}]
    r,_=build_report(data,audit,'m18_test')
    assert any('许可证拒绝' in s for s in r['missing_evidence'])


def test_missing_flux_cannot_be_hidden_by_a_strict_pass_flag(tmp_path):
    data,audit=fixture(tmp_path);data['strict_convergence_verified']=True
    with pytest.raises(ValueError):build_report(data,audit,'m18_test')


def test_one_step_diagnostic_is_counted_once_and_has_no_invented_pressure_or_profiles(tmp_path):
    data,audit=fixture(tmp_path)
    data.update(new_added_gpu_iterations=1,new_saved_checkpoints_verified=1,diagnostic_added_gpu_iterations=1,
                new_continuation_gpu_iterations=0,actual_total_chain_gpu_iterations=101,
                fresh_gpu_probes=[dict(saved_iteration=2151,saved_summary={'h2_relative_convective_imbalance':.001})])
    r,_=build_report(data,audit,'m18_test')
    assert r['metrics'][0]['value']==1 and r['metrics'][1]['value']==1
    row=r['table']['rows'][-1]
    assert row[:2]==['M18 单步报告诊断',2151]
    assert row[2:4]==[None,None] and row[5:]==[None,None,None]
    assert any('未执行整批' in s for s in r['missing_evidence'])


def test_diagnostic_plus_full_continuation_cannot_hide_an_extra_iteration(tmp_path):
    data,audit=fixture(tmp_path);data.update(new_added_gpu_iterations=100,new_saved_checkpoints_verified=11,
                                          diagnostic_added_gpu_iterations=1,new_continuation_gpu_iterations=100)
    with pytest.raises(ValueError):build_report(data,audit,'m18_test')
