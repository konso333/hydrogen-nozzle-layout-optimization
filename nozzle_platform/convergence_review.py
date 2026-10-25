"""Present audited M18 measurements without counting historical steps as new work."""
from pathlib import Path
from nozzle_platform.workflows import sha256


def build_report(data,audit_path,key):
    if data.get('status')!='bounded_gpu_convergence_independently_audited' or data.get('scientific_eligible') is not False:
        raise ValueError('Not an audited M18 verification record')
    if data.get('strict_convergence_verified') and not data.get('full_species_flux_verified'):
        raise ValueError('Strict convergence cannot pass without complete species flux evidence')
    steps=data['new_added_gpu_iterations'];count=data['new_saved_checkpoints_verified']
    if steps not in (0,1,100) or count!={0:0,1:1,100:11}[steps]:raise ValueError('Incomplete M18 saved checkpoints')
    diagnostic=data.get('diagnostic_added_gpu_iterations',0)
    continuation=data.get('new_continuation_gpu_iterations',100 if steps==100 else 0)
    if diagnostic+continuation!=steps or diagnostic not in (0,1) or steps>100:
        raise ValueError('Inconsistent or exceeded M18 iteration budget')
    if steps==1 and len(data.get('fresh_gpu_probes',[]))!=1:raise ValueError('Missing one-step diagnostic evidence')
    baseline=data['baseline_added_gpu_iterations']
    rows=[]
    def pct(v):return None if v is None else f'{v*100:.5f}%'
    for row in data['rows']:
        p=row['point'];drop=p['area_weighted_total_pressure_drop_Pa']
        rows.append(['M18 本轮' if row['phase']=='m18' else 'M17 历史基线',p['iteration'],
                     f"{drop['fuel_inlet']:.7g}",f"{drop['oxidizer_inlet']:.7g}",pct(p['h2_relative_convective_imbalance']),
                     None,pct(p['outlet_profile_relative_drift']),pct(p['interior_profile_relative_drift'])])
    for probe in data.get('fresh_gpu_probes',[]):
        rows.append(['M18 单步报告诊断',probe['saved_iteration'],None,None,
                     pct(probe['saved_summary']['h2_relative_convective_imbalance']),None,None,None])
    numerical=data['decision']['numerical_assessment']
    native=audit_path.parent/'native_reports.txt'
    saved_detail=(f'本轮 {count} 个保存点通过完整单元、边界和实际黏度核查；M17 基线另有 11 个保存点。' if steps
                  else 'M17 历史基线的 11 个保存点已复核；M18 尚未产生新的求解保存点。')
    if steps==1:saved_detail+=' 本轮为单步报告诊断，不代表已完成整批续算。'
    missing=['完整组分通量仍未取得独立可靠证据，缺失值保留为空。',
             '传统组分报告须通过纯氢入口及全部边界交叉检查，总质量流量相符不代表组分正确。',
             '真实喷嘴导热耦合、CPU/GPU 收敛解一致性、多喷嘴燃烧和网格独立性仍未验证。']
    if not numerical['numerical_criteria_met']:missing.append('残差、场变化或连续稳定性仍未达到原有严格标准。')
    if not steps:missing.append('本轮未追加 GPU 迭代；表格和曲线中的 100 步来自已审核的 M17 历史基线。')
    if steps==1:missing.append('本轮只新增 1 步 GPU 报告诊断；曲线中的完整 25 步区间来自 M17，未执行整批 100 步续算。')
    if any(r.get('license_rejected') for r in data.get('reader_probes',[])):
        recovered=any(r.get('result',{}).get('status')=='completed' for r in data.get('reader_probes',[]))
        missing.append('早期读取曾被许可证拒绝，失败记录和清理结果保留；'+('许可恢复后已完成后续读取。' if recovered else '许可恢复后再核查。'))
    report=dict(report_id=key,title='M18 · 单步 GPU 报告核查与收敛门控' if steps==1 else 'M18 · 组分报告交叉核查与有界收敛判定',
        summary=f'本轮新增 GPU {steps} 步；独立复核 M17 基线，并把完整通量、数值收敛和物理适用范围分开判定。',
        scientific_eligible=False,statuses=dict(
            saved_fields=dict(state='passed',label='保存点已复核' if steps else '历史基线已复核',detail=saved_detail),
            convergence=dict(state='passed' if data['strict_convergence_verified'] else 'not_met',
                             label='严格收敛通过' if data['strict_convergence_verified'] else '严格收敛未通过',
                             detail=f"已保存 GPU 链共 {data.get('actual_total_chain_gpu_iterations',baseline+steps)} 步，其中 {data['decision']['added_gpu_iterations']} 步有完整 25 步区间指标；要求至少 200 步、连续三批，并取得可靠完整通量。"),
            physical_scope=dict(state='pending',label='冷流验证阶段',detail='正式三档和原物性 GPU 正式模式继续关闭；本记录不用于方案排名。')),
        metrics=[dict(label='M18 新增 GPU 迭代',value=steps,unit='步 · 最多 100'),
                 dict(label='本轮保存检查点',value=count,unit='个 · 历史基线另列'),
                 dict(label='M17 历史 GPU 步数',value=baseline,unit='步 · 不重复计入本轮'),
                 dict(label='完整氢气通量',value=None,unit='未取得独立证据')],
        method='真实单喷嘴冷流 · 原混合公式 · 二阶 · 组分松弛因子 0.1 · 双精度 GPU；CPU 读取对照不初始化、不迭代。',
        missing_evidence=missing,
        table=dict(columns=['来源','保存步数','燃料实际总压差 / Pa','空气实际总压差 / Pa','氢气对流不平衡','完整氢气通量 / kg·s⁻¹','出口场变化','内部截面变化'],rows=rows),
        metric_scope='总压来自 Fluent 原生报告；氢气不平衡只含对流。完整通量缺失时，数值趋势接近稳定也不自动通过严格验收。',
        charts=[dict(file='trends.png',label='已核对的实际保存数据趋势；历史基线与本轮分开标注')],
        conclusion='报告交叉校验和有界收敛判定已接入；正式比较资格仍未通过。',
        next_step='定位完整组分通量报告的矛盾，并依据有界 GPU 结果决定后续工作；不自动扩大预算或回退 CPU。',
        raw_excerpt_file='native_journal.txt',provenance=dict(audit_sha256=sha256(audit_path),native_journal_sha256=sha256(native)))
    return report,{'audit.json':audit_path,'metrics.csv':audit_path.parent/'convergence_trends.csv',
                   'trends.png':audit_path.parent/'convergence_trends.png','native_journal.txt':native}
