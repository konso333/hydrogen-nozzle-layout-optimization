"""Same-input family comparison; no physics ranking or approximate deduplication."""
import copy
from nozzle_platform.design import generate
from nozzle_platform.geometry_records import geometry_key,verify_scheme

FAMILIES=('rectangular','honeycomb','sunflower','ring')

def compare(payload,model):
    if not isinstance(payload,dict) or set(payload)!={'design'} or not isinstance(payload['design'],dict):raise ValueError('排列比较需要完整的 design 参数。')
    original=copy.deepcopy(payload['design'])
    first=generate(original,model)
    if original['spacing_mode']!='auto':raise ValueError('多排列比较使用自动定位；指定间距与旋转请单独生成。')
    rows=[];suggestions=[];groups={}
    for family in FAMILIES:
        scheme=first if family==original['family'] else generate(dict(original,family=family),model)
        row=dict(family=family,feasible=scheme['feasible'],N=original['N'],scheme=None)
        if scheme['feasible']:
            verify_scheme(scheme);key=geometry_key(scheme);duplicate=groups.get(key)
            groups.setdefault(key,family)
            row.update(scheme=scheme,geometry_key=key,same_coordinates_as=duplicate,summary=scheme['summary'])
            suggestions.append(dict(kind='comparison',label=family,request=copy.deepcopy(scheme['request']),verified=True,scheme_id=scheme['scheme_id']))
        else:row.update(reason=scheme['message'],search_capacity_found=scheme.get('search_capacity_found'))
        rows.append(row)
    return dict(kind='geometry_comparison',schema_version=1,requested_design=original,rows=rows,suggestions=suggestions,unique_coordinate_sets=len(groups),solver_started=False,recommended_family=None,
        scope='同模型、数量、区域和几何约束；仅比较几何指标，不排名燃烧或流动性能。相同坐标只忽略编号顺序，不进行平移／旋转对齐或近似匹配。')
