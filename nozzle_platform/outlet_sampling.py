"""Prepare immutable outlet geometry once and sample saved face fields."""

import numpy as np
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay

from nozzle_platform.probe_points import outlet_xy


def prepare(path,domain):
    import h5py
    with h5py.File(path,'r') as case:
        mesh=case['meshes/1'];top=mesh['faces/zoneTopology']
        zone=top['name'][()][0].decode().split(';').index('outlet')
        first,end=int(top['minId'][zone])-1,int(top['maxId'][zone])
        sizes=mesh['faces/nodes/1/nnodes']
        start=int(sizes[:first].sum(dtype=np.int64))
        counts=sizes[first:end].astype(np.int64)
        if len(counts)==0 or np.any(counts<3):raise ValueError('保存的出口面拓扑无效。')
        nodes=mesh['faces/nodes/1/nodes'][start:start+int(counts.sum())].astype(np.int64)-1
        xyz=mesh['nodes/coords/1'][()]
    if np.any(nodes<0) or np.any(nodes>=len(xyz)):raise ValueError('保存的出口节点编号越界。')
    offsets=np.r_[0,np.cumsum(counts)];triangles=counts-2
    anchor=np.repeat(offsets[:-1],triangles)
    local=np.arange(triangles.sum())-np.repeat(np.r_[0,np.cumsum(triangles)[:-1]],triangles)
    a,b,c=xyz[nodes[anchor]],xyz[nodes[anchor+1+local]],xyz[nodes[anchor+2+local]]
    area=np.linalg.norm(np.cross(b-a,c-a),axis=1)/2
    owner=np.repeat(np.arange(len(counts)),triangles)
    weights=np.bincount(owner,weights=area,minlength=len(counts))
    if not np.isfinite(weights).all() or np.any(weights<=0):raise ValueError('保存的出口面面积无效。')
    centres=np.column_stack([np.bincount(owner,weights=area*(a[:,j]+b[:,j]+c[:,j])/3)/weights for j in range(3)])
    if not np.isfinite(centres).all():raise ValueError('保存的出口面形心无效。')
    points=outlet_xy(domain);triangulation=Delaunay(centres[:,:2])
    if np.any(triangulation.find_simplex(points)<0):raise ValueError('保存的出口共同采样点位于插值范围外。')
    sector=domain['config'].get('boundary',{}).get('kind')=='sector'
    return dict(first_face=first,end_face=end,weights=weights,points=points,triangulation=triangulation,
        sampling=('sector-interior lattice and nozzle-centred probes; linear interpolation of saved outlet face values'
                  if sector else '17x17 lattice within 0.8 chamber radius; linear interpolation of saved outlet face values'))


def sample(path,sampler):
    import h5py
    first,end=sampler['first_face'],sampler['end_face']
    with h5py.File(path,'r') as data:
        def field(name):
            dataset=data['results/1/phase-1/faces/'+name+'/1']
            start=int(dataset.attrs['minId'][0])-1
            if first<start or end-start>len(dataset):raise ValueError('保存的出口场数据范围不完整。')
            return dataset[first-start:end-start]
        fractions=field('SV_Y')
        if fractions.ndim!=2 or fractions.shape[1]!=4:raise ValueError('保存的出口组分数组与冷流模板不符。')
        values=np.column_stack([field('SV_U'),field('SV_V'),field('SV_W'),fractions[:,0]])
    samples=LinearNDInterpolator(sampler['triangulation'],values)(sampler['points'])
    if not np.isfinite(samples).all() or not np.isfinite(values).all():raise ValueError('保存的出口场数据无效。')
    weights=sampler['weights'];mean=np.average(values[:,3],weights=weights)
    cv=float(np.sqrt(np.average((values[:,3]-mean)**2,weights=weights))/max(abs(mean),1e-12))
    return dict(points_m=sampler['points'].tolist(),values=samples.tolist(),
        columns=['u_m_s','v_m_s','w_m_s','h2_mass_fraction'],h2_area_mean=float(mean),h2_coefficient_of_variation=cv,
        velocity_area_rms=float(np.sqrt(np.average(np.sum(values[:,:3]**2,axis=1),weights=weights))),sampling=sampler['sampling'])
