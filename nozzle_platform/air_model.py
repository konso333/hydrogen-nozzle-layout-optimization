"""Explicit first-version isothermal air model; no species or energy equation.

Air R=287.05 J/(kg K). Viscosity is evaluated once at the requested fixed
temperature with Sutherland reference mu=1.716e-5, T=273.11 K, S=110.56 K
(PyAnsys geometry/mesh/Fluent example); both rho and mu are then constant.
"""
import math,re
from pathlib import Path
from validation import require_count,require_finite

MODEL='air-isothermal-v1'
MODE='air-coldflow'
TRANSPORT='air-constant'
EQUATIONS=('continuity','x-velocity','y-velocity','z-velocity')
ROLES=('fuel_inlet','oxidizer_inlet','outlet','wall')
FIELDS=('speed','pressure')
CRITERIA={'scaled_residuals':{n:1e-4 for n in EQUATIONS},'relative_mass_imbalance':.005,'relative_pressure_drop_change':.01}

def properties(temperature):
    t=require_finite(temperature,'temperature_K')
    if not 200<=t<=600:raise ValueError('定温空气温度需为 200～600 K。')
    return dict(temperature_K=t,density_kg_m3=101325./(287.05*t),viscosity_Pa_s=1.716e-5*(t/273.11)**1.5*(273.11+110.56)/(t+110.56),
        operating_pressure_Pa=101325.,gas_constant_J_kg_K=287.05,property_model='constant rho and mu evaluated at fixed temperature',
        energy_equation=False,species_transport=False,chemical_reactions=False)

def normalize(request):
    required={'mesh_id','domain_id','scheme_id','physical_model','fuel_velocity_m_s','oxidizer_velocity_m_s','temperature_K','iterations'}
    options={'compute_device','convergence_mode','transport_model','time_budget_seconds','species_solver_control'}
    if not required<=set(request) or set(request)-required-options:raise ValueError('空气冷流参数缺失或包含不适用字段。')
    r=dict(request)
    if r['physical_model']!=MODEL:raise ValueError('空气物理模型无效。')
    for n in ('fuel_velocity_m_s','oxidizer_velocity_m_s'):
        r[n]=require_finite(r[n],n)
        if not .001<=r[n]<=100:raise ValueError('两类通道的空气速度需为 0.001～100 m/s。')
    r['temperature_K']=properties(r['temperature_K'])['temperature_K']
    r.setdefault('compute_device','gpu');r.setdefault('transport_model',TRANSPORT);r.setdefault('convergence_mode',MODE)
    validate_selection(r)
    if r.get('species_solver_control','standard')!='standard':raise ValueError('单组分空气模式不使用组分诊断设置。')
    r['iterations']=require_count(r['iterations'],'iterations')
    if r['iterations']>(500 if r['compute_device']=='gpu' else 25):raise ValueError('空气冷流 GPU 最多 500 步；本机 CPU 仅允许最多 25 步限时短测。')
    r['time_budget_seconds']=require_count(r.get('time_budget_seconds',300),'time_budget_seconds')
    if not 120<=r['time_budget_seconds']<=900:raise ValueError('空气冷流运行时限需为 120～900 秒。')
    return r

def validate_selection(config):
    device=config.get('compute_device','gpu')
    if device not in ('cpu','gpu') or config.get('transport_model',TRANSPORT)!=TRANSPORT or config.get('convergence_mode',MODE)!=MODE:
        raise ValueError('空气冷流需明确选择空气常物性与空气冷流模式。')
    return device,TRANSPORT

def residuals(text,start,end):
    columns=None;rows={}
    for line in text.splitlines():
        p=line.split()
        if p[:2]==['iter','continuity']:columns=p[1:p.index('time/iter')] if 'time/iter' in p else None
        elif columns and len(p)>=len(columns)+2 and p[0].isdigit() and ':' in p[len(columns)+1]:
            i=int(p[0])
            if not start<i<=end:continue
            try:v=dict(zip(columns,map(float,p[1:len(columns)+1])))
            except ValueError:continue
            if set(v)!=set(EQUATIONS) or any(not math.isfinite(x) or x<0 for x in v.values()):raise ValueError('空气活跃方程或残差无效。')
            rows[i]=v
    if set(rows)!=set(range(start+1,end+1)):raise ValueError('空气逐步残差记录不完整。')
    return rows

def iteration_count(path):
    """Use all active-air equation histories; GPU legacy counters can stay 0."""
    import h5py,numpy as np
    with h5py.File(path) as h:
        histories=[]
        for name in EQUATIONS:
            key='results/residuals/phase-1/'+name+'/iterations'
            if key not in h:raise ValueError('缺少空气活跃方程的保存迭代历史。')
            values=np.asarray(h[key][()]).reshape(-1)
            if not len(values) or not np.isfinite(values).all() or np.any(values!=np.floor(values)) or values[0]<0 or values[-1]<1 or np.any(np.diff(values)!=1):raise ValueError('空气保存迭代历史无效。')
            histories.append(values)
        if any(not np.array_equal(histories[0],v) for v in histories[1:]):raise ValueError('四条空气方程的保存迭代计数不一致。')
        return int(histories[0][-1])

def assessment(history):
    def checks(p):
        latest=[]
        for n,limit in CRITERIA['scaled_residuals'].items():latest.append(dict(name='residual:'+n,value=p['block_maximum_residuals'][n],limit=limit,inclusive=True))
        for n in ('relative_mass_imbalance','relative_pressure_drop_change'):latest.append(dict(name=n,value=p[n],limit=CRITERIA[n],inclusive=False))
        for r in latest:r['passed']=bool(r['value'] is not None and math.isfinite(r['value']) and r['value']>=0 and (r['value']<=r['limit'] if r['inclusive'] else r['value']<r['limit']))
        return latest
    latest=checks(history[-1]) if history else []
    consecutive=0
    for p in reversed(history):
        if p['block_steps']!=25 or not all(r['passed'] for r in checks(p)):break
        consecutive+=1
    minimum=bool(history and history[-1]['iteration']>=100)
    return dict(numerical_criteria_met=minimum and consecutive>=2,minimum_iterations=100,minimum_iterations_met=minimum,consecutive_passing_blocks=consecutive,required_consecutive_blocks=2,
        sustained_checks_met=consecutive>=2,block_size=25,checks=latest,unmet_checks=[r['name'] for r in latest if not r['passed']],scope='air continuity and momentum only; no species/energy equations; grid independence not implied')

def saved_geometry(case):
    import h5py,numpy as np
    with h5py.File(case) as h:
        g=h['meshes/1'];top=g['faces/zoneTopology'];names=top['name'][()][0].decode().split(';')
        ranges={n:(int(a)-1,int(b)) for n,a,b in zip(names,top['minId'][()],top['maxId'][()]) if n in ROLES}
        if set(ranges)!=set(ROLES):raise ValueError('空气冷流四类几何边界不完整。')
        counts=g['faces/nodes/1/nnodes'][()].astype(int);ids=g['faces/nodes/1/nodes'][()].astype(int)-1;points=g['nodes/coords/1'][()];off=np.r_[0,np.cumsum(counts)]
        if off[-1]!=len(ids) or ids.min()<0 or ids.max()>=len(points):raise ValueError('面节点拓扑无效。')
        areas=np.zeros(len(counts))
        for n in np.unique(counts):
            if n<3:raise ValueError('边界面节点不足。')
            indices=np.flatnonzero(counts==n);p=points[ids[off[indices,None]+np.arange(n)[None,:]]]
            areas[indices]=np.linalg.norm(np.cross(p[:,1:-1]-p[:,:1],p[:,2:]-p[:,:1]),axis=2).sum(axis=1)/2
        if not np.isfinite(areas).all() or np.any(areas<=0):raise ValueError('保存网格面面积无效。')
        cells=int(g['cells/zoneTopology/maxId'][0])
    return dict(ranges=ranges,areas=areas,cells=cells)

def saved_point(case,data,config,previous=None,geometry=None):
    import h5py,numpy as np
    from nozzle_platform.nozzle_gpu_probe import _field
    geom=saved_geometry(case) if geometry is None else geometry;props=properties(config['temperature_K']);rho=props['density_kg_m3'];n=geom['cells']
    with h5py.File(data) as h:
        cells=h['results/1/phase-1/cells'];v={k:_field(cells,k,1,n) for k in ('SV_U','SV_V','SV_W','SV_P')}
        maxspeed=float(np.sqrt(v['SV_U']**2+v['SV_V']**2+v['SV_W']**2).max())
        if maxspeed/math.sqrt(1.4*287.05*props['temperature_K'])>=.3:raise ValueError('保存速度超过空气不可压缩首版的 Mach<0.3 适用范围。')
        density_checked=False
        if 'SV_DENSITY' in cells:
            d=_field(cells,'SV_DENSITY',1,n)
            if not np.allclose(d,rho,rtol=1e-10,atol=1e-12):raise ValueError('保存密度与声明的空气常密度不符。')
            density_checked=True
        faces=h['results/1/phase-1/faces'];lo=min(a for a,b in geom['ranges'].values());hi=max(b for a,b in geom['ranges'].values())
        def boundary(name):
            ds=faces[name+'/1'];first=int(ds.attrs['minId'][0]);last=int(ds.attrs['maxId'][0]);raw=_field(faces,name,first,last)
            if first>lo+1 or last<hi:raise ValueError('保存边界场覆盖不全：'+name)
            return raw,first-1
        fields={name:boundary(name) for name in ('SV_FLUX','SV_P','SV_U','SV_V','SV_W')}
        mass={};pressure={};areas={};inlet_checks={}
        for role,(a,b) in geom['ranges'].items():
            values={key:raw[a-start:b-start] for key,(raw,start) in fields.items()};area=geom['areas'][a:b];areas[role]=float(area.sum())
            mass[role]=-float(values['SV_FLUX'].sum());speed2=sum(values[k]**2 for k in ('SV_U','SV_V','SV_W'))
            pressure[role]=float(np.dot(area,values['SV_P']+.5*rho*speed2)/area.sum())
            if role in ('fuel_inlet','oxidizer_inlet'):
                velocity=config['fuel_velocity_m_s' if role=='fuel_inlet' else 'oxidizer_velocity_m_s'];expected=rho*velocity*areas[role]
                err=abs(mass[role]/expected-1);inlet_checks[role]=dict(expected_mass_flow_kg_s=expected,saved_mass_flow_kg_s=mass[role],relative_error=err,passed=err<=1e-5)
                if err>1e-5:raise ValueError('保存空气入口通量与指定密度、速度、面积不一致。')
            if role=='outlet':
                speed=np.sqrt(speed2);mean=float(np.dot(area,speed)/area.sum());std=float(np.sqrt(np.dot(area,(speed-mean)**2)/area.sum()))
                uniform=dict(area_weighted_mean_speed_m_s=mean,area_weighted_speed_std_m_s=std,speed_coefficient_of_variation=std/mean if mean>0 else None,scope='outlet face area-weighted speed distribution; not a nozzle-wise uniformity or grid-independence claim')
        incoming=mass['fuel_inlet']+mass['oxidizer_inlet']
        if not incoming>0 or mass['fuel_inlet']<=0 or mass['oxidizer_inlet']<=0 or mass['outlet']>=0 or abs(mass['wall'])>1e-12:raise ValueError('空气边界质量流向或壁面泄漏检查未通过。')
        drops={role:pressure[role]-pressure['outlet'] for role in ('fuel_inlet','oxidizer_inlet')}
        change=max(abs(value-previous['area_weighted_total_pressure_drop_Pa'][role])/max(abs(value),1.) for role,value in drops.items()) if previous else None
        return dict(mass_flow_kg_s=mass,relative_mass_imbalance=abs(sum(mass.values()))/incoming,area_weighted_total_pressure_drop_Pa=drops,relative_pressure_drop_change=change,
            area_weighted_total_pressure_Pa=pressure,boundary_area_m2=areas,outlet_speed_distribution=uniform,inlet_mass_crosscheck=inlet_checks,
            saved_fields_checked=True,cell_density_stored_and_checked=density_checked,maximum_speed_m_s=maxspeed,physical_model=MODEL,
            source='independent saved SV_FLUX and face P/U/V/W; total pressure=P+rho*|U|^2/2 with configured constant rho; no species report or boundary-density reconstruction')
