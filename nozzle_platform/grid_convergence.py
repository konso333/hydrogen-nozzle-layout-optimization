"""Saved-file outlet profiles and conservative, scoped grid convergence evidence."""

import math

import numpy as np
from scipy.optimize import brentq


def outlet_profile(directory, domain):
    """Interpolate saved outlet face values onto identical interior physical points.

    These are samples of the saved boundary-face reconstruction, not independent
    solver probes or a claim about the entire internal volume.
    """
    from nozzle_platform.outlet_sampling import prepare,sample
    return sample(directory/'final.dat.h5',prepare(directory/'final.cas.h5',domain))


def richardson(values, sizes):
    """Unequal-r three-grid estimate; reject oscillation, noise and extreme p."""
    coarse,medium,fine = map(float,values)
    hc,hm,hf = map(float,sizes)
    if not hc>hm>hf>0 or not all(math.isfinite(v) for v in values):
        return dict(available=False,reason='网格尺度或指标无效')
    dcm,dmf = coarse-medium,medium-fine
    if dcm*dmf<=0 or abs(dmf)<max(abs(fine)*1e-10,1e-14) or abs(fine)<1e-10:
        return dict(available=False,reason='非单调或差异接近数值噪声，不能估计收敛阶')
    rcm,rmf = hc/hm,hm/hf
    def equation(p):
        return rmf**p*math.expm1(p*math.log(rcm))/math.expm1(p*math.log(rmf))-abs(dcm/dmf)
    try:
        p = brentq(equation,.1,2.5)
    except (ValueError,OverflowError,ZeroDivisionError):
        return dict(available=False,reason='未获得可信的正收敛阶（本二阶验证模板筛选范围 0.1～2.5）')
    denominator = math.expm1(p*math.log(rmf))
    extrapolated = fine+(fine-medium)/denominator
    gci = 1.25*abs((fine-medium)/fine)/denominator*100
    return dict(available=True,observed_order=p,extrapolated=extrapolated,
                fine_gci_percent=gci,safety_factor=1.25,
                note='三网格 Richardson/GCI 估计；三点本身不能独立证明已进入渐近区间')


def assess_profiles(rows, tolerance, key='outlet_profile'):
    label='内部截面' if key=='interior_profile' else '出口'
    if any(key not in row for row in rows):
        return dict(passed=False,reason='缺少共同物理位置的'+label+'场数据')
    profiles=[r[key] for r in rows]
    if any(p['points_m']!=profiles[0]['points_m'] or p['columns']!=profiles[0]['columns'] for p in profiles):
        return dict(passed=False,reason=label+'采样位置或变量不一致')
    arrays=[np.array(p['values'],dtype=float) for p in profiles]
    if any(a.shape!=arrays[0].shape or a.ndim!=2 or a.shape[1]!=4 or not np.isfinite(a).all() for a in arrays):
        return dict(passed=False,reason=label+'采样数据无效')
    result={}
    for name,selection in [('velocity',slice(0,3)),('h2_mass_fraction',slice(3,4))]:
        changes=[]
        for a,b in zip(arrays,arrays[1:]):
            denominator=np.linalg.norm(b[:,selection])
            if denominator<1e-12:
                return dict(passed=False,reason='出口场归一化参考接近零')
            changes.append(float(np.linalg.norm(a[:,selection]-b[:,selection])/denominator*100))
        result[name]=dict(adjacent_relative_l2_percent=changes,passed=max(changes)<=tolerance)
    sections={}
    points=np.array(profiles[0]['points_m'])
    if points.ndim==2 and points.shape[1]==3:
        for z in np.unique(points[:,2]):
            mask=points[:,2]==z
            selected=[dict(profile,points_m=points[mask,:2].tolist(),values=array[mask].tolist()) for profile,array in zip(profiles,arrays)]
            sections[str(float(z))]=assess_profiles([{key:p} for p in selected],tolerance,key)
    return dict(passed=all(v['passed'] for v in result.values()) and all(s['passed'] for s in sections.values()),metrics=result,sections=sections,
                scope=label+'共同采样点的速度矢量与氢气质量分数；内部截面逐个检查，不代表所有空间位置')
