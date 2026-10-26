"""Reject incomplete/non-finite fields and native temperature clipping."""
import re


def inspect(directory):
    import h5py
    import numpy as np
    result=dict(checked=True,fields={},temperature_clipping_detected=False)
    with h5py.File(directory/'final.cas.h5','r') as case,h5py.File(directory/'final.dat.h5','r') as data:
        cells=case['meshes/1/cells/zoneTopology']
        if list(cells['minId'][()])!=[1] or len(cells['maxId'])!=1:
            raise ValueError('场值检查要求完整的单连通单元区。')
        count=int(cells['maxId'][0]);group=data['results/1/phase-1/cells']
        for key in ['SV_U','SV_V','SV_W','SV_P','SV_T','SV_DENSITY','SV_MU_LAM']:
            if key not in group or set(group[key])!={'1'}:
                raise ValueError('保存的场值缺失或分块格式未核验：'+key)
            field=group[key+'/1'];low=np.asarray(field.attrs.get('minId',[]));high=np.asarray(field.attrs.get('maxId',[]))
            values=field[()].reshape(-1)
            if low.shape!=(1,) or high.shape!=(1,) or int(low[0])!=1 or int(high[0])!=count or values.shape!=(count,) or not np.isfinite(values).all():
                raise ValueError('保存的场值不完整或含非有限数：'+key)
            if key in ['SV_T','SV_DENSITY','SV_MU_LAM'] and values.min()<=0:
                raise ValueError('保存的温度、密度或黏度非正：'+key)
            result['fields'][key]=dict(minimum=float(values.min()),maximum=float(values.max()))
    text=(directory/'fluent.trn').read_text(encoding='utf-8',errors='replace')
    # The native message is evidence of bound clipping, not an inferred
    # temperature interval derived from the inlet-temperature input limits.
    clipped=re.findall(r'Solution values for temperature have been clipped[^\r\n]*',text)
    result['temperature_clipping_detected']=bool(clipped)
    result['temperature_clipping_messages']=len(clipped)
    return result


def require_healthy(result):
    if result['temperature_clipping_detected']:
        raise ValueError('Fluent 计算出现温度限幅，场值未通过质量检查；即使组分和通过，也不能验收。原始数据已保留。')
