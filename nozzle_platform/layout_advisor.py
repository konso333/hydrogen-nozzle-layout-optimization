"""Geometry-only diagnosis and verified, explicitly adopted parameter changes."""
import copy,math
from nozzle_platform.design import generate

FAMILIES=('rectangular','honeycomb','sunflower','ring')
NAMES=dict(rectangular='方阵',honeycomb='蜂巢',sunflower='向日葵',ring='环形')

def annotate(scheme,original,policy):
    import hashlib,json
    changes=[dict(field=k,before=original[k],after=scheme['request'][k]) for k in original if original[k]!=scheme['request'][k]]
    base=scheme['scheme_id'];scheme['geometry_scheme_id']=base
    scheme['design_assistance']=dict(requested_design=copy.deepcopy(original),adopted_design=copy.deepcopy(scheme['request']),changed_parameters=changes,adoption_policy=policy,quantity_changed=original['N']!=scheme['request']['N'])
    scheme['scheme_id']=hashlib.sha256(json.dumps(dict(base=base,assistance=scheme['design_assistance']),sort_keys=True).encode()).hexdigest()
    return scheme

def advise(payload,model):
    if not isinstance(payload,dict) or set(payload)!={'design','explore_families'} or type(payload['explore_families']) is not bool:
        raise ValueError('智能排布需提供完整 design 与布尔 explore_families。')
    original=copy.deepcopy(payload['design']);request=copy.deepcopy(original)
    cache={};calls=0
    def evaluate(r):
        nonlocal calls
        key=tuple(sorted(r.items()))
        if key not in cache:
            if calls>=24:raise ValueError('本次建议搜索达到 24 次构造预算，请分步调整参数。')
            cache[key]=generate(r,model);calls+=1
        return cache[key]
    first=evaluate(request)  # The same validation gates as ordinary generation.
    result=dict(schema_version=1,requested_design=original,scheme=None,diagnostics=[],suggestions=[],changed_parameters=[],solver_started=False,global_capacity_proven=False,
        scope='有限确定性几何构造与保守必要条件；不保证最大容量或全局最优，不计算流动或燃烧性能。')
    if first['feasible']:
        result.update(status='feasible',feasibility='verified_feasible',scheme=first,message='指定数量与排列已找到可行方案，数量和约束保持不变。',search_calls=calls)
        return result
    automatic=request['spacing_mode']=='auto'
    outcomes={request['family']:first}
    if automatic:
        for family in FAMILIES:
            if family not in outcomes:outcomes[family]=evaluate(dict(request,family=family))
    alternatives=[(family,row) for family,row in outcomes.items() if row['feasible']]
    if alternatives and payload['explore_families']:
        family,scheme=max(alternatives,key=lambda pair:(pair[1]['summary']['minimum_wall_clearance_mm'],-FAMILIES.index(pair[0])))
        result.update(status='feasible',feasibility='verified_feasible',scheme=scheme,message=f'所选{NAMES[request["family"]]}未找到目标数量；已按你的选项改用{NAMES[family]}，保持 N={request["N"]}、安装区域和全部间隙约束。',changed_parameters=[dict(field='family',before=request['family'],after=family)],search_calls=calls)
        annotate(scheme,original,'explicit_cross_family_option')
        return result
    for family,row in alternatives:
        result['suggestions'].append(dict(kind='family',label=f'保持 N={request["N"]}，采用{NAMES[family]}',request=copy.deepcopy(row['request']),verified=True,scheme_id=row['scheme_id']))
    R=request['radius_mm'];n=request['N'];r=request['diameter_mm']/2;wall=request['wall_clearance_mm'];fraction=request['sweep_angle_deg']/360
    inradius=R if request['region']=='circle' else R*math.sin(math.radians(min(request['sweep_angle_deg'],180)/2))/(1+math.sin(math.radians(min(request['sweep_angle_deg'],180)/2)))
    area_upper=math.floor(fraction*R*R/(r*r)+1e-10)
    if r+wall>inradius+1e-9:
        result['diagnostics'].append(dict(code='single_envelope_does_not_fit',message=f'区域最大内切圆半径约 {inradius:.3f} mm，小于喷嘴半径加边界余量 {r+wall:g} mm，连单个完整包络也放不下。',proven_necessary_condition=True))
    if n>area_upper:
        result['diagnostics'].append(dict(code='area_bound_exceeded',message=f'仅喷嘴投影面积的必要条件就限制数量不超过 {area_upper}；这还没有扣除间隙与边缘损失，不能把该上界当作可行容量。',area_only_upper_bound=area_upper,proven_necessary_condition=True))
    proven=bool(result['diagnostics'])
    found_message=f'当前要求 N={n}；所选{NAMES[request["family"]]}本次搜索找到 {first.get("search_capacity_found") or 0} 个可用位置。'
    found_message+=('实际最大容量尚未求得，以下建议分别核验。' if proven else '未找到目标数量不等于证明所有非规则排布都不可能。')
    result['diagnostics'].append(dict(code='construction_not_found',message=found_message,proven_necessary_condition=False))
    if automatic:
        family,available=max(outcomes.items(),key=lambda item:(item[1].get('search_capacity_found') or 0,-FAMILIES.index(item[0])))
        count=min(n-1,available.get('search_capacity_found') or 0)
        if count>0:
            smaller=dict(request,N=count,family=family);verified=evaluate(smaller)
            if verified['feasible']:result['suggestions'].append(dict(kind='quantity',label=f'当前区域已验证可放 N={count}（{NAMES[family]}，不保证最大）',request=smaller,verified=True,scheme_id=verified['scheme_id']))
        # Find a feasible upper radius, not a claimed minimum radius. Preserve N,
        # family, envelope and clearances; rounding is followed by revalidation.
        low=R;high=max(R*1.2,r+wall+(request['diameter_mm']+request['edge_gap_mm'])*math.sqrt(n/max(fraction,1e-9)))
        high=min(high,5000.);enlarged=None
        for _ in range(5):
            if high<=R:break
            candidate=dict(request,radius_mm=high);row=evaluate(candidate)
            if row['feasible']:enlarged=row;break
            if high==5000:break
            high=min(5000.,high*1.6)
        if enlarged:
            for _ in range(7):
                middle=(low+high)/2;row=evaluate(dict(request,radius_mm=middle))
                if row['feasible']:high=middle;enlarged=row
                else:low=middle
            upper=math.ceil(high*10)/10;candidate=dict(request,radius_mm=upper);row=evaluate(candidate)
            if row['feasible']:result['suggestions'].append(dict(kind='radius',label=f'保持 N={n} 与{NAMES[request["family"]]}，采用已验证半径 {upper:g} mm（非最小值证明）',request=candidate,verified=True,scheme_id=row['scheme_id']))
    else:
        result['diagnostics'].append(dict(code='manual_layout_fixed',message='当前指定了间距和旋转角。智能搜索不修改这些参数；可切换自动定位后重新搜索。',proven_necessary_condition=False))
    message=('当前数量或包络违反区域的必要几何条件。' if proven else '本次有限搜索未取得目标排布。')
    result.update(status='needs_adjustment',feasibility='proven_infeasible' if proven else 'search_not_found',message=message+'下面列出原因与已独立核验的调整方案；点击采用前不会改变你的输入。',search_calls=calls)
    assert request==original
    return result
