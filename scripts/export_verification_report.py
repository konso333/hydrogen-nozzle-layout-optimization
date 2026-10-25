"""Install audited M16/M17 records for the local platform's read-only report page."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nozzle_platform.workflows import sha256
from nozzle_platform.review_reports import install_review

def pct(value):return None if value is None else f'{value*100:.5f}%'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',type=Path,required=True);p.add_argument('--kind',choices=['m16','m17'],required=True)
    p.add_argument('--report-id',required=True);p.add_argument('--output-root',type=Path,required=True)
    a=p.parse_args();audit_path=a.audit.resolve();data=json.loads(audit_path.read_text(encoding='utf-8'))
    for n,d in data['artifacts_sha256'].items():assert sha256(audit_path.parent/n)==d
    assert not data['scientific_eligible'] and not data['formal_physics_validated']
    if a.kind=='m16':
        assert data['status']=='bounded_gpu_continuation_independently_audited' and data['accepted_added_iterations']==100
        title='M16 · 四批 GPU 保存重启验证';steps=100;checkpoints=20
        summary='同一原物性工况，每批计算 25 步，完整检查后再启动下一批。四批已完成，数值收敛仍未通过。'
        method='真实单喷嘴冷流 · 851803 单元 · 原混合公式 · 组分松弛因子 0.1 · 四次独立会话 · 双精度 GPU'
        rows=[]
        for b in data['batches']:
            if not b['accepted']:continue
            r=b['observation']['point'];pressure=r['area_weighted_static_pressure_drop_Pa']
            rows.append([r['iteration'],f"{pressure['fuel_inlet']:.6f}",f"{pressure['oxidizer_inlet']:.6f}",pct(r['h2_relative_convective_imbalance']),None,pct(b['common_profile_drift']['outlet']),pct(b['common_profile_drift']['interior'])])
        columns=['保存步数','燃料静压差 / Pa','空气静压差 / Pa','氢气对流不平衡','完整氢气通量 / kg·s⁻¹','出口场变化','内部截面变化']
        missing=['实际总压报告未在该节点取得；表中的静压不能替代总压。','氢气只核对对流项，未取得可靠的完整组分通量。','残差、共同截面变化及连续稳定性尚未满足正式标准。','单会话长期稳定、真实导热耦合、CPU/GPU 收敛解一致性和网格独立性未验证。']
        chart='continuation_trends.png';csv='batch_trends.csv';native=Path(data['batches'][-1]['audit']['directory'])/'journal.trn'
        conclusion='20 个保存检查点合格；出口场与内部截面仍明显变化，不能用于喷嘴方案优劣比较。'
        next_step='下一步核查单会话计算、实际总压和完整组分通量；正式三档网格研究保持停止。'
        scope='静压由保存面场重建；氢气不平衡只含对流。共同场变化取速度与氢质量分数相对 L2 变化的较大者。'
    else:
        assert data['status']=='single_session_100_steps_independently_audited' and data['actual_native_added_iterations']==100
        title='M17 · 单会话 GPU 对照与实际总压';steps=100;checkpoints=data['saved_checkpoints_verified']
        summary='使用与 M16 相同的初值和数值设置，在一个 Fluent 会话内完成 100 步，并取得实际总压报告。'
        method='真实单喷嘴冷流 · 851803 单元 · 原混合公式 · 组分松弛因子 0.1 · 单个会话 · 全部求解结束后读取报告'
        rows=[]
        for r in data['rows']:
            point=r['point'];pressure=point['area_weighted_total_pressure_drop_Pa']
            rows.append([r['saved_iteration'],f"{pressure['fuel_inlet']:.7g}",f"{pressure['oxidizer_inlet']:.7g}",pct(point['h2_relative_convective_imbalance']),None,pct(point['outlet_profile_relative_drift']),pct(point['interior_profile_relative_drift'])])
        columns=['保存步数','燃料实际总压差 / Pa','空气实际总压差 / Pa','氢气对流不平衡','完整氢气通量 / kg·s⁻¹','出口场变化','内部截面变化']
        missing=['完整组分通量报告接口与保存的入口组分矛盾，未作为守恒证据；未取得的值保持为空。','本工况入口扩散实际关闭；该开关不表示域内扩散被关闭。','本轮 100 步尚不足以满足至少 200 步及连续三批的严格验证要求，残差和场变化也仍需核对。','真实喷嘴导热耦合、CPU/GPU 收敛解一致性、燃烧和网格独立性未验证。']
        chart='single_session_comparison.png';csv='single_session_comparison.csv';native=Path(data['candidate'])/'journal.trn'
        conclusion='单会话和保存点已通过独立检查；正式收敛和物理适用性仍未通过，暂不用于喷嘴方案排名。'
        next_step='对照单会话与重启轨迹，继续定位未收敛原因，取得可靠完整通量证据后再决定追加迭代。'
        scope='总压来自 Fluent 实际面积加权报告，保留打印精度；氢气只含对流，完整通量未取得。共同场变化采用既有 M13 相同物理采样点。'
    report=dict(report_id=a.report_id,title=title,summary=summary,scientific_eligible=False,
        statuses=dict(saved_fields=dict(state='passed',label='保存点检查通过',detail=f'{checkpoints} 个已保存检查点的完整单元与边界数据已核对；未代表所有未保存步骤。'),
            convergence=dict(state='not_met',label='正式收敛未通过',detail='同时核对残差、通量与场分布变化；达到预算不自动通过。'),
            physical_scope=dict(state='pending',label='冷流验证阶段',detail='单喷嘴验证记录，尚未获准用于多喷嘴燃烧或网格独立性结论。')),
        metrics=[dict(label='本轮新增 GPU 迭代',value=steps,unit='步 · 有界运行'),dict(label='保存检查点',value=checkpoints,unit='个 · 独立核对'),
            dict(label='计算精度',value='双精度',unit='GPU · 1 个 CPU 设置进程'),dict(label='原物性',value='原混合公式',unit='尚未正式开放')],
        method=method,missing_evidence=missing,table=dict(columns=columns,rows=rows),metric_scope=scope,
        charts=[dict(file='trends.png',label=title+'的真实保存数据趋势')],conclusion=conclusion,next_step=next_step,
        raw_excerpt_file='native_journal.txt',provenance=dict(audit_sha256=sha256(audit_path),native_journal_sha256=sha256(native)))
    folder=install_review(a.output_root,report,{'audit.json':audit_path,'metrics.csv':audit_path.parent/csv,'trends.png':audit_path.parent/chart,'native_journal.txt':native})
    print('Installed immutable report',folder)

if __name__=='__main__':main()
