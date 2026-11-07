"""Independent validation and strict coordinate-set identity for geometry records."""
import hashlib,json,math,re
from nozzle_platform.design import MAX_ARRAY_NOZZLES
from shape_boundary import MountingBoundary,validate_mounting_layout
from validation import require_count,require_finite

DESIGN_FIELDS={'model_id','N','family','region','radius_mm','start_angle_deg','sweep_angle_deg','diameter_mm','edge_gap_mm','wall_clearance_mm','spacing_mode','spacing_mm','rotation_deg'}

def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()

def verify_scheme(scheme):
    if not isinstance(scheme,dict) or scheme.get('feasible') is not True:raise ValueError('记录不是已核验的几何方案。')
    if not isinstance(scheme.get('scheme_id'),str) or not re.fullmatch('[0-9a-f]{64}',scheme['scheme_id']):raise ValueError('几何方案编号无效。')
    r=scheme.get('request');m=scheme.get('model')
    if not isinstance(r,dict) or set(r)!=DESIGN_FIELDS or not isinstance(m,dict) or r['model_id']!=m.get('model_id'):raise ValueError('几何记录的参数或模型关联不完整。')
    n=require_count(r['N'],'N')
    if n>MAX_ARRAY_NOZZLES or r['family'] not in {'rectangular','honeycomb','sunflower','ring'} or r['spacing_mode'] not in {'auto','manual'}:raise ValueError('几何记录超出当前设计范围。')
    for key in DESIGN_FIELDS-{'model_id','N','family','region','spacing_mode'}:require_finite(r[key],key)
    if r['radius_mm']>5000 or r['diameter_mm']+1e-6<require_finite(m.get('envelope_diameter_mm'),'模型包络'):raise ValueError('安装半径或实际模型包络核验不通过。')
    if r['spacing_mode']=='manual' and r['family'] in {'rectangular','honeycomb'} and r['spacing_mm']+1e-9<r['diameter_mm']+r['edge_gap_mm']:raise ValueError('指定间距违反包络与净间隙。')
    b=MountingBoundary(r['region'],r['radius_mm'],r['start_angle_deg'],r['sweep_angle_deg'])
    points=scheme.get('coordinates_mm')
    check=validate_mounting_layout(points,boundary=b,N=n,outer_diameter_mm=r['diameter_mm'],edge_gap_mm=r['edge_gap_mm'],wall_clearance_mm=r['wall_clearance_mm'])
    if not check['feasible']:raise ValueError('记录坐标未通过独立数量／边界／间距核验。')
    minimum=min((math.dist(a,c) for i,a in enumerate(points) for c in points[i+1:]),default=None)
    expected=dict(nozzle_count=n,minimum_center_distance_mm=minimum,minimum_edge_gap_mm=None if minimum is None else minimum-r['diameter_mm'],minimum_wall_clearance_mm=check['minimum_wall_clearance_mm'],boundary_area_mm2=b.area_mm2)
    summary=scheme.get('summary',{})
    for key,value in expected.items():
        actual=summary.get(key)
        if (value is None and actual is not None) or (value is not None and (actual is None or not math.isclose(require_finite(actual,key),value,rel_tol=1e-10,abs_tol=1e-9))):raise ValueError('记录的几何指标与坐标不一致。')
    if scheme.get('simulation_status')!='not_started' or scheme.get('scientific_eligible') is not False:raise ValueError('几何库不接受求解或科学验证通过的状态声明。')
    return check

def geometry_key(scheme):
    r=scheme['request'];b=MountingBoundary(r['region'],r['radius_mm'],r['start_angle_deg'],r['sweep_angle_deg'])
    points=sorted([[0. if x==0 else float(x),0. if y==0 else float(y)] for x,y in scheme['coordinates_mm']])
    return digest(dict(model_id=r['model_id'],boundary=b.to_dict(),diameter_mm=float(r['diameter_mm']),edge_gap_mm=float(r['edge_gap_mm']),wall_clearance_mm=float(r['wall_clearance_mm']),coordinates_mm=points))
