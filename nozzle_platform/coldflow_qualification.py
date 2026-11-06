"""Evidence for selecting a cold-flow model, separate from execution success."""
import re

from nozzle_platform.species_reports import parse_species_report, assess_species_report

ORIGINAL = 'ideal-gas-mixing-law'
COMPATIBLE = 'mass-weighted-mixing-law'


def report_domain(allowed, transcript):
    """Empty command metadata is not proof that a loaded domain is absent."""
    if not isinstance(allowed,list) or any(not isinstance(x,str) for x in allowed):
        raise ValueError('Invalid report domain metadata')
    for name in ['phase-1','mixture']:
        if name in allowed:return name
    if allowed:raise ValueError('No supported explicit report domain')
    blocks=re.findall(r'^\s*domains,\s*\n(.*?)^\s*zones,',transcript,re.M|re.S)
    if len(blocks)!=1 or [x.strip() for x in blocks[0].splitlines() if x.strip()]!=['mixture']:
        raise ValueError('Empty metadata without one independently loaded mixture domain')
    return 'mixture'


def transport_review(reference, composition):
    """Compare laws at the actual, unchanged saved compositions.

    Sample statistics are not CFD error bounds or a validation of component
    properties. An algebraic agreement cannot qualify a production model.
    """
    from nozzle_platform.transport_audit import components, evaluate, differences
    material = components(reference)
    actual = reference['materials']['mixture']['hydrogen-air']
    selected = {name: actual[name]['option'] for name in ['viscosity','thermal_conductivity']}
    if any(v not in {ORIGINAL, COMPATIBLE} for v in selected.values()):
        raise ValueError('This review requires explicit original or compatible mixture laws')
    properties = evaluate(composition, material)
    return dict(
        preferred_formal_candidate='original-ideal-mixture',
        original_gpu_implementation='explicit experimental Python UDF; qualification required',
        saved_transport=selected, species_order=material['names'],
        differences_on_saved_composition={
            'viscosity': differences(properties['ideal_viscosity'], properties['mass_viscosity']),
            'thermal_conductivity': differences(properties['ideal_conductivity'], properties['mass_conductivity'])},
        compatible_law_is_equivalent=False, formal_transport_qualified=False,
        missing_evidence=[
            'Actual original-law viscosity and heat transport in the real nonuniform nozzle',
            'Component property applicability to the intended temperature/composition range',
            'Sustained numerical convergence and independent complete species balance'],
        scientific_eligible=False, grid_independence_verified=False)


def report_commands(label, *, explicit_domain=None):
    """Bounded markers for two documented interfaces; never initialize/iterate."""
    if not isinstance(label,str) or not re.fullmatch('[a-z0-9_]+',label):
        raise ValueError('Invalid report label')
    if explicit_domain is not None and explicit_domain not in {'mixture','phase-1'}:
        raise ValueError('Only known single-phase domain identifiers are allowed')
    # Used by native controllers for transcript delimiters. The explicit-domain
    # report itself is invoked through the generated settings command.
    return ('(display "\\nM23_BEGIN_'+label+'\\n")',
            '(display "\\nM23_END_'+label+'\\n")')


def assess_report(text, label, saved_summary, *, inlet_diffusion_enabled):
    report_commands(label)
    # Some 25.2 Scheme interfaces print literal backslash-n delimiters. Decode
    # only complete marker-only lines; never unescape field data or echoed code.
    lines=[]
    for line in text.splitlines():
        if re.fullmatch(r'(?:\\nM23_(?:BEGIN|END)_[a-z0-9_]+\\n)+',line.strip()):
            line=line.strip().replace('\\n','\n')
        lines.append(line)
    text='\n'.join(lines)+'\n'
    match = re.search(r'^M23_BEGIN_'+label+r'\s*$\n(.*?)^M23_END_'+label+r'\s*$',text,re.M|re.S)
    if match is None:
        raise ValueError('Missing or incomplete actual report delimiters')
    block=match[1]
    if re.search(r'^M23_BEGIN_',block,re.M):
        raise ValueError('Overlapping report delimiters')
    if len(re.findall(r'^M23_BEGIN_'+label+r'\s*$',text,re.M))!=1 or len(re.findall(r'^M23_END_'+label+r'\s*$',text,re.M))!=1:
        raise ValueError('Repeated report delimiters')
    parsed=parse_species_report(block)
    return dict(native_report=parsed,assessment=assess_species_report(
        parsed,saved_summary,inlet_diffusion_enabled=inlet_diffusion_enabled))


def continuation_decision(*, saved_fields_healthy, transport_qualified,
                          fresh_reports_consistent, independent_diffusion_verified):
    """Fail closed: two interfaces alone cannot prove full diffusive transport."""
    evidence=dict(saved_fields_healthy=saved_fields_healthy,transport_qualified=transport_qualified,
                  fresh_reports_consistent=fresh_reports_consistent,
                  independent_diffusion_verified=independent_diffusion_verified)
    if any(type(v) is not bool for v in evidence.values()):
        raise ValueError('Explicit evidence booleans required; missing is not passed')
    missing=[name for name,value in evidence.items() if not value]
    return dict(controlled_continuation_eligible=not missing,missing_evidence=missing,
                full_species_flux_verified=fresh_reports_consistent and independent_diffusion_verified,
                formal_physics_validated=False,numerical_convergence_verified=False,
                grid_independence_verified=False,scientific_eligible=False)


def build_report(data,audit_path,key):
    """Reuse the platform's immutable report viewer, without solver controls."""
    from nozzle_platform.workflows import sha256
    if data.get('status')!='coldflow_qualification_independently_audited' or data.get('scientific_eligible') is not False:
        raise ValueError('Expected an independently audited cold-flow qualification record')
    if data['actual_added_gpu_iterations'] not in [0,1] or data['cpu_solver_iterations']!=0:
        raise ValueError('This report accepts only a bounded zero/one-step diagnostic')
    decision=data['decision'];model=data['transport_review']
    if model['formal_transport_qualified'] or decision['full_species_flux_verified'] or decision['controlled_continuation_eligible']:
        raise ValueError('This diagnostic has not independently qualified transport or diffusion')
    rows=[]
    for label,item in data.get('reports',{}).items():
        parsed=item['native_report'];assessment=item['assessment']
        for role,values in parsed['boundary_rates_kg_s'].items():
            rows.append([label,role,values[0],values[3],assessment['report_consistent_with_saved_boundary']])
    difference=model['differences_on_saved_composition']
    saved_ok=data['saved_fields_healthy']
    report=dict(report_id=key,title='M23 · 冷流物性选择与完整收支核查',
        summary='核查原混合公式与 GPU 兼容物性，并以显式计算域接口对照组分报告；证据不足时停止续算。',
        scientific_eligible=False,statuses=dict(
            saved_fields=dict(state='passed' if saved_ok else 'not_met',label='所检保存场通过' if saved_ok else '保存场尚未通过',
                detail='原始保存文件不改写；诊断新增迭代与历史100步分开记录。'),
            convergence=dict(state='not_met',label='正式收敛未通过',detail='本节点核查完整通量与物性前提，不以一步诊断替代连续收敛区间。'),
            physical_scope=dict(state='pending',label='保留原混合公式为正式候选',detail='质量加权物性仅用于显式接入诊断；尚不能作为原模型等价替代。')),
        metrics=[dict(label='本轮新增 GPU 迭代',value=data['actual_added_gpu_iterations'],unit='步 · 不含M22历史100步'),
            dict(label='实际组成上黏度最大相对差异',value=round(difference['viscosity']['absolute_relative_max']*100,5),unit='% · 等样本比较，非CFD误差'),
            dict(label='实际组成上导热率最大相对差异',value=round(difference['thermal_conductivity']['absolute_relative_max']*100,5),unit='% · 等样本比较，非CFD误差'),
            dict(label='可靠完整氢气通量',value=None,unit='缺少独立扩散证据')],
        method='真实B0双喷嘴保存场 · 原网格/边界/二阶/严格绝对容差不变 · 正常成对读取 · 不初始化/无在线SVAR',
        missing_evidence=['真实非均匀喷嘴原混合公式的黏度和实际导热耦合验证尚未完成。',
            '组分报告必须通过纯氢入口及逐边界核对，接口成功不代表完整收支通过。',
            '完整通量的独立扩散证据、正式收敛和网格独立性尚未取得。']+
            (['原执行器在日志标记解析阶段失败；离线兼容解析后核查原始字段和报告，原失败状态保留。'] if data.get('native_protocol_status')=='failed' else []),
        table=dict(columns=['阶段/接口','边界','报告H2 / kg·s⁻¹','报告N2 / kg·s⁻¹','报告一致性'],rows=rows),
        metric_scope='物性差异使用同一份未归一化的实际组成；表中为未经修补的原生报告，矛盾值保留但不作守恒证据。',
        charts=[],conclusion='正式物性与完整收支证据不足，受控续算保持停止。',
        next_step='补足实际原混合公式的非均匀导热证据及可靠完整通量，再另行制定收敛验收预算。',
        raw_excerpt_file='native_reports.txt',provenance=dict(audit_sha256=sha256(audit_path)))
    return report,{'audit.json':audit_path,'native_reports.txt':audit_path.parent/'native_reports.txt'}
