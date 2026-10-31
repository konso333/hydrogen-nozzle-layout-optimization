"""Explicit linear-solver experiment; never changes physics or acceptance limits."""
import math
import re

STANDARD='standard'
STRICT='strict-linear'
STRICT_ABSOLUTE='strict-absolute'
KEYS=('gpuapp/solver/absolute-tolerance-double','species/amg-alpha',
      'species/iter-tolerance','species/max-iter','species/relax')


def selection(config):
    value=config.get('species_solver_control',STANDARD)
    if not isinstance(value,str) or value not in {STANDARD,STRICT,STRICT_ABSOLUTE}:
        raise ValueError('组分求解设置无效。')
    if value!=STANDARD and (config.get('compute_device')!='gpu' or config.get('convergence_mode')!='gpu-validation'):
        raise ValueError('组分线性加强仅用于 GPU 接入短测。')
    return value


def apply(session,config):
    """Fixed experiments; the absolute profile additionally tightens global tolerance."""
    if selection(config)==STANDARD:return
    # These are fixed, tested Scheme symbols/values, never user-supplied code.
    for key,values in [('amg-alpha','1e-6 1e-6 1e-6'),
                       ('iter-tolerance','1e-6 1e-6 1e-6'),('max-iter','100 100 100')]:
        session.scheme_eval.eval("(rpsetvar 'species/"+key+" '("+values+"))")
    if selection(config)==STRICT_ABSOLUTE:
        session.scheme_eval.eval("(rpsetvar 'gpuapp/solver/absolute-tolerance-double 1e-20)")


def saved(path,config):
    """Verify actual native case values, independently of requested JSON."""
    import h5py
    with h5py.File(path,'r') as case:text=case['settings/Rampant Variables'][0].decode()
    values={}
    for key in KEYS:
        array=key.startswith('species/')
        pattern=r'\('+re.escape(key)+(r'\s+\(([^()]*)\)\)' if array else r'\s+([^()\s]+)\)')
        matches=re.findall(pattern,text)
        if len(matches)!=1:raise ValueError('保存的组分求解控制缺失或不唯一：'+key)
        row=[float(x) for x in matches[0].split()]
        if len(row)!=(3 if array else 1) or any(not math.isfinite(x) or x<=0 for x in row):
            raise ValueError('保存的组分求解控制无效：'+key)
        values[key]=row if array else row[0]
    if selection(config)!=STANDARD:
        absolute=1e-20 if selection(config)==STRICT_ABSOLUTE else 1e-16
        expected={KEYS[0]:absolute,KEYS[1]:[1e-6]*3,KEYS[2]:[1e-6]*3,KEYS[3]:[100.]*3,KEYS[4]:[1.]*3}
        if values!=expected:raise ValueError('实际组分线性设置与所选实验不一致，已停止。')
    return dict(profile=selection(config),actual_values=values,validation_only=True)


def verify_live(session,record):
    if record['profile']==STANDARD:return
    actual={key:session.rp_vars(key) for key in KEYS}
    if actual!=record['actual_values']:raise ValueError('计算期间组分线性设置发生变化，已停止。')
