"""Analytically known pure-species transport; not nozzle/mixture validation."""
import math,re
from pathlib import Path
import numpy as np

SPECIES=('h2','o2','h2o','n2')
DENSITY=101325.*2.01594/(8314.46261815324*300.)
VELOCITY=1.0
EXPECTED_RATE=DENSITY*VELOCITY*.01*.01


def iteration_count(path):
    """A freshly initialized data file has counters but no residual history."""
    import h5py
    with h5py.File(path) as f:
        history='results/residuals/phase-1/h2/iterations'
        if history in f and len(f[history]):return int(f[history][-1])
        raw=f['settings/Data Variables'][0].decode()
        counts=[]
        for name in ['residuals/wb-iteration-count','numerics/flexible/iteration-count']:
            match=re.findall(r'\('+re.escape(name)+r'\s+(\d+)\)',raw)
            if len(match)!=1:raise ValueError('Missing independent native iteration counter')
            counts.append(int(match[0]))
        if counts[0]!=counts[1]:raise ValueError('Native iteration counters disagree')
        return counts[0]


def channel_mesh(path):
    from nozzle_platform.transport_benchmark import slab_mesh
    path=Path(path)
    if path.exists():raise ValueError('Preserve existing benchmark mesh')
    mesh=slab_mesh(path)
    text=path.read_text(encoding='ascii')
    for zone,old,new,kind,code in [(4,'cold','inlet','velocity-inlet','a'),(5,'hot','outlet','pressure-outlet','5'),(6,'insulated','sides','symmetry','7')]:
        text,count=re.subn(r'\(13 \('+str(zone)+r' ([0-9a-f]+) ([0-9a-f]+) 3 4\)\(',
            lambda m:'(13 ('+str(zone)+' '+m[1]+' '+m[2]+' '+code+' 4)(',text)
        if count!=1:raise ValueError('Unexpected generated boundary layout')
        old_zone=f'(39 ({zone} wall {old} 1)())'
        if text.count(old_zone)!=1:raise ValueError('Unexpected generated zone')
        text=text.replace(old_zone,f'(39 ({zone} {kind} {new} 1)())')
    path.write_text(text,encoding='ascii',newline='\n')
    mesh.update(density_kg_m3=DENSITY,inlet_velocity_m_s=VELOCITY,
        expected_mass_flow_kg_s=DENSITY*VELOCITY*mesh['heated_area_m2'],
        case_scope='pure H2 incompressible ideal gas at 101325 Pa / 300 K, symmetry sides; no reactions')
    return mesh


def parse_report(text,label):
    if not re.fullmatch('[a-z0-9_]+',label):raise ValueError('Invalid marker')
    match=re.search(r'^M24_BEGIN_'+label+r'\s*$\n(.*?)^M24_END_'+label+r'\s*$',text,re.M|re.S)
    if match is None:raise ValueError('Missing actual report block')
    block=match[1]
    if len(re.findall(r'^M24_BEGIN_'+label+r'\s*$',text,re.M))!=1:raise ValueError('Duplicate report block')
    headers=re.findall(r'^\s*species:\s*\(([^()]*)\)\s+kg/s\s*$',block,re.M)
    if len(headers)!=1 or headers[0].split()!=list(SPECIES):raise ValueError('Wrong species order or report units')
    rates={};ids=set()
    for zid,role,raw in re.findall(r'^\s*zone\s+(\d+)\s+\(([^()]*)\):\s*\(([^()]*)\)\s*$',block,re.M):
        values=list(map(float,raw.split()))
        if role not in {'inlet','outlet','sides'} or role in rates or zid in ids or len(values)!=4 or not np.isfinite(values).all():raise ValueError('Invalid boundary report')
        rates[role]=values;ids.add(zid)
    if not {'inlet','outlet'}<=set(rates):raise ValueError('Missing inlet/outlet report')
    nets=re.findall(r'^\s*net species-mass-flow:\s*\(([^()]*)\)\s*$',block,re.M)
    if len(nets)!=1:raise ValueError('Missing net report')
    net=list(map(float,nets[0].split()))
    if len(net)!=4 or not np.isfinite(net).all():raise ValueError('Invalid net report')
    if not np.allclose(np.sum(list(rates.values()),axis=0),net,rtol=0,atol=2e-12):raise ValueError('Printed boundary sum differs from net')
    return dict(boundary_rates_kg_s=rates,net_kg_s=net)


def assess_report(parsed,expected):
    if not math.isfinite(expected) or expected<=0:raise ValueError('Positive analytic mass rate required')
    checks=[]
    for role,sign in [('inlet',1),('outlet',-1)]:
        wanted=[sign*expected,0.,0.,0.]
        for species,value,reference in zip(SPECIES,parsed['boundary_rates_kg_s'][role],wanted):
            checks.append(dict(boundary=role,species=species,value=value,reference=reference,
                absolute_tolerance=expected*2e-6,passed=abs(value-reference)<=expected*2e-6))
    if 'sides' in parsed['boundary_rates_kg_s']:
        checks.append(dict(boundary='sides',passed=max(abs(x) for x in parsed['boundary_rates_kg_s']['sides'])<=expected*2e-6))
    return dict(passed=all(x['passed'] for x in checks),checks=checks,
        scope='pure uniform-species benchmark only; no mixed-nozzle diffusion qualification')


def saved_state(case_path,data_path):
    import h5py
    with h5py.File(case_path) as case,h5py.File(data_path) as data:
        top=case['meshes/1/faces/zoneTopology'];names=top['name'][()][0].decode().split(';')
        if set(names)!={'interior','inlet','outlet','sides'}:raise ValueError('Wrong channel topology')
        cells=int(case['meshes/1/cells/zoneTopology/maxId'][0]);g=data['results/1/phase-1/cells']
        if cells!=256:raise ValueError('Only the documented 256-cell test is accepted')
        fields={}
        for name in ['SV_Y','SV_U','SV_V','SV_W','SV_P','SV_T','SV_DENSITY']:
            ds=g[name+'/1'];value=ds[()]
            if set(g[name])!={'1'} or int(ds.attrs['minId'][0])!=1 or int(ds.attrs['maxId'][0])!=cells or not np.isfinite(value).all():raise ValueError('Incomplete saved cell field')
            fields[name]=value if name=='SV_Y' else value.reshape(-1)
        y=fields['SV_Y']
        if y.shape!=(cells,4):raise ValueError('Wrong saved species shape')
        flux=data['results/1/phase-1/faces/SV_FLUX/1'][()].reshape(-1)
        rates={name:-float(flux[int(a)-1:int(b)].sum()) for name,a,b in zip(names,top['minId'][()],top['maxId'][()]) if name!='interior'}
        pure_error=float(abs(y-np.array([1,0,0,0])).max())
        expected=DENSITY*VELOCITY*.01*.01
        return dict(cells=cells,maximum_pure_composition_error=pure_error,
            species_sum_error=float(abs(y.sum(axis=1)-1).max()),mass_rates_kg_s=rates,
            maximum_velocity_error_m_s=max(float(abs(fields['SV_U']-VELOCITY).max()),float(abs(fields['SV_V']).max()),float(abs(fields['SV_W']).max())),
            temperature_range_K=[float(fields['SV_T'].min()),float(fields['SV_T'].max())],
            maximum_relative_density_error=float(abs(fields['SV_DENSITY']/DENSITY-1).max()),
            uniform_composition_passed=pure_error<=1e-12,
            mass_rates_passed=all(abs(rates[k]-v)<=expected*2e-6 for k,v in [('inlet',expected),('outlet',-expected),('sides',0)]))


def classification(cpu,gpu):
    """Do not assign a vendor root cause merely from one controlled comparison."""
    if not cpu['saved_fields_passed'] or not gpu['saved_fields_passed']:return 'saved_field_failure; report comparison inconclusive'
    a,b=cpu['fresh_report_passed'],gpu['fresh_report_passed']
    if a and b:return 'both_reports_pass_in_uniform_pure_case; mixed-nozzle cause remains open'
    if a and not b:return 'GPU_report_path_disagrees_with_CPU_and_analytic_reference; internal_root_cause_unconfirmed'
    if not a and not b:return 'both_report_paths_fail_the_pure_reference; not_exclusive_to_GPU'
    return 'CPU_report_path_fails_while_GPU_passes; internal_root_cause_unconfirmed'


def saved_passed(state):
    return (state['uniform_composition_passed'] and state['mass_rates_passed']
        and state['maximum_velocity_error_m_s']<=1e-8 and state['maximum_relative_density_error']<=2e-6
        and max(abs(t-300.) for t in state['temperature_range_K'])<=1e-8)


def saved_diffusion_flags(case_path):
    import h5py
    with h5py.File(case_path) as f:text=f['settings/Rampant Variables'][0].decode()
    result={}
    for key,symbol in [('thermal_diffusion','thermal-diffusion'),('multi_component_diffusion','multicomponent-diffusion')]:
        match=re.findall(r'\(species/'+symbol+r'\?\s+(#t|#f)\)',text)
        if len(match)!=1:raise ValueError('Actual diffusion option is missing or ambiguous')
        result[key]=match[0]=='#t'
    return result


def physical_signature(state,diffusion_flags=None):
    """Active physics of this stationary laminar benchmark, not GUI metadata.

    Disabled turbulence/expert controls are not a physical difference. Required
    species, thermal, inlet/outlet and material values must actually be present.
    """
    b=state['boundary_conditions'];m=state['models'];inlet=b['velocity_inlet']['inlet'];outlet=b['pressure_outlet']['outlet']
    if m['viscous']['model']!='laminar' or m['species']['reactions']['enable_volumetric_reactions'] is not False:
        raise ValueError('Only nonreacting laminar benchmark physics is accepted')
    options=m['species']['options']
    effective={k:options[k] for k in ['inlet_diffusion','diffusion_energy_source']}
    for key in ['thermal_diffusion','multi_component_diffusion']:
        if key in options:effective[key]=options[key]
        elif diffusion_flags is not None and type(diffusion_flags.get(key)) is bool:effective[key]=diffusion_flags[key]
        else:raise ValueError('Hidden diffusion flag requires actual native evidence')
    return dict(general=state['general'],materials=state['materials'],energy=m['energy'],
        species_model=m['species']['model'],species_options=effective,
        inlet_momentum={k:inlet['momentum'][k] for k in ['velocity_specification_method','reference_frame','velocity_magnitude']},
        inlet_thermal=inlet['thermal'],inlet_species=inlet['species'],
        outlet_momentum={k:outlet['momentum'][k] for k in ['backflow_reference_frame','gauge_pressure','backflow_dir_spec_method','backflow_pressure_spec','prevent_reverse_flow']},
        outlet_thermal=outlet['thermal'],outlet_species=outlet['species'],
        symmetry_names=sorted(b['symmetry']))


def state_differences(before,after,path=''):
    if isinstance(before,dict) and isinstance(after,dict):
        result=[]
        for key in sorted(set(before)|set(after)):
            if key not in before or key not in after:result.append(dict(path=path+'/'+key,before=before.get(key),after=after.get(key)))
            else:result.extend(state_differences(before[key],after[key],path+'/'+key))
        return result
    return [] if before==after else [dict(path=path,before=before,after=after)]


def build_report(audit,audit_path,key):
    from nozzle_platform.workflows import sha256
    if audit.get('status')!='pure_channel_cpu_gpu_independently_audited' or audit.get('scientific_eligible') is not False:
        raise ValueError('Independently audited synthetic comparison required')
    cpu,gpu=audit['backends']['cpu'],audit['backends']['gpu'];result=classification(cpu,gpu)
    if result!=audit['classification']:raise ValueError('Conclusion differs from measured evidence')
    if not all(1<=item['actual_added_iterations']<=5 for item in [cpu,gpu]):raise ValueError('Actual iteration count outside one-to-five diagnostic range')
    if not(cpu['saved_fields_passed'] and gpu['saved_fields_passed']):raise ValueError('Analytic field comparison incomplete')
    if cpu['fresh_report_passed'] and gpu['fresh_report_passed']:
        conclusion='两种设备在均匀纯氢小算例中的报告通过；真实混合喷嘴中的报告矛盾仍需定位。'
    elif not cpu['fresh_report_passed'] and not gpu['fresh_report_passed']:
        conclusion='CPU和GPU报告均未通过纯氢解析核查，问题不是GPU路径独有；内部根因仍未确认。'
    elif cpu['fresh_report_passed']:
        conclusion='CPU报告与解析答案一致，GPU报告不一致；异常范围缩小到GPU报告路径，不能据此判定其物理场失真。'
    else:conclusion='CPU报告未通过而GPU报告通过，需要继续定位接口及配置差异；尚不能确定内部根因。'
    rows=[]
    for backend,item in audit['backends'].items():
        for label,report in item['reports'].items():
            stage=('刚计算后' if label.startswith('fresh') else '读取后')+'／'+('显式计算域' if label.endswith('explicit') else '旧接口')
            for role,values in report['raw']['boundary_rates_kg_s'].items():
                rows.append([backend.upper(),stage,role,values[0],values[3],'通过' if report['assessment']['passed'] else '未通过'])
    report=dict(report_id=key,title='M24 · 纯氢小通道 CPU／GPU 对照',summary=conclusion,scientific_eligible=False,
        statuses=dict(saved_fields=dict(state='passed',label='解析保存场核查通过',detail='两边从同一初始文件出发，核查纯氢组成、已知质量流量、速度和温度。'),
            convergence=dict(state='pending',label='仅验证小算例',detail='每边最多5步，自动提前收敛按实际记录；不用于性能测速或真实喷嘴持续收敛。'),
            physical_scope=dict(state='pending',label='真实混合喷嘴尚未验收',detail='纯氢、不可压理想气体、对称侧面；未验证非均匀组分扩散或原混合物性。')),
        metrics=[dict(label='CPU新增迭代',value=cpu['actual_added_iterations'],unit='步 · 单进程参考对照'),dict(label='GPU新增迭代',value=gpu['actual_added_iterations'],unit='步 · 同一初值'),
            dict(label='解析氢气质量流量',value=EXPECTED_RATE,unit='kg/s'),dict(label='CPU实际计算耗时',value=cpu['compute_wall_seconds'],unit='秒 · 硬上限20秒')],
        method='256规则单元 · 0.01m立方 · 不可压理想气体／101325Pa · U=1m/s · 纯H2 · 300K · 无反应 · 对称侧面',
        missing_evidence=['本算例只提供纯组分已知通量参考，不代替混合喷嘴的独立扩散核查。','原混合公式真实非均匀导热、正式收敛、燃烧及网格独立性仍未验证。'],
        table=dict(columns=['设备','阶段／接口','边界','报告H2 / kg·s⁻¹','报告N2 / kg·s⁻¹','解析核查'],rows=rows),
        metric_scope='原始报告值保留；解析参考为H2承担全部入口流量、N2为0。保存场和报告是否一致分别审核。',
        charts=[],conclusion=conclusion,next_step='依据该对照缩小报告异常范围，再验证真实混合喷嘴的完整通量获取方法。',
        raw_excerpt_file='native_reports.txt',provenance=dict(audit_sha256=sha256(audit_path)))
    return report,{'audit.json':audit_path,'native_reports.txt':audit_path.parent/'native_reports.txt'}
