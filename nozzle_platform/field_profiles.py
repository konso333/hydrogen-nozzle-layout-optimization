"""Common interior probes reconstructed from saved Fluent cell-centred fields."""

import numpy as np
from scipy.spatial import cKDTree
from nozzle_platform.probe_points import interior_xy


def cell_centres(path):
    """Oriented tetrahedral volume moments of each closed polyhedral cell.

    Face triangles are processed in chunks to bound temporary memory. Fluent
    c0/c1 indices are 1-based; the two owners have opposite face orientation.
    """
    import h5py
    with h5py.File(path,'r') as f:
        mesh=f['meshes/1']
        count=int(np.max(mesh['cells/zoneTopology/maxId'][()]))
        xyz=mesh['nodes/coords/1'][()]
        counts=mesh['faces/nodes/1/nnodes'][()].astype(np.int64)
        nodes=mesh['faces/nodes/1/nodes'][()].astype(np.int64)-1
        offsets=np.r_[0,np.cumsum(counts)]
        owner0=mesh['faces/c0/1'][()].astype(np.int64)-1
        second=mesh['faces/c1/1'];start=int(second.attrs['minId'][0])-1
        owner1=np.full(len(counts),-1,dtype=np.int64)
        owner1[start:start+len(second)]=second[()].astype(np.int64)-1
    if len(owner0)!=len(counts) or np.any(counts<3) or np.any(owner0<0) or np.any(owner0>=count):
        raise ValueError('保存的单元面拓扑无效。')
    volume=np.zeros(count);moment=np.zeros((count,3))
    for first in range(0,len(counts),20000):
        selected=np.arange(first,min(first+20000,len(counts)))
        triangles=counts[selected]-2
        face=np.repeat(selected,triangles)
        anchor=np.repeat(offsets[selected],triangles)
        local=np.arange(triangles.sum())-np.repeat(np.r_[0,np.cumsum(triangles)[:-1]],triangles)
        a,b,c=xyz[nodes[anchor]],xyz[nodes[anchor+1+local]],xyz[nodes[anchor+2+local]]
        v=np.einsum('ij,ij->i',a,np.cross(b,c))/6
        m=v[:,None]*(a+b+c)/4
        np.add.at(volume,owner0[face],v);np.add.at(moment,owner0[face],m)
        internal=owner1[face]>=0
        np.add.at(volume,owner1[face[internal]],-v[internal]);np.add.at(moment,owner1[face[internal]],-m[internal])
    if np.any(abs(volume)<1e-30) or not np.isfinite(moment).all():
        raise ValueError('保存网格的单元体积矩无效。')
    centres=moment/volume[:,None]
    if not np.isfinite(centres).all() or np.any(centres<xyz.min(axis=0)-1e-8) or np.any(centres>xyz.max(axis=0)+1e-8):
        raise ValueError('保存网格的单元形心越界。')
    return centres,float(abs(volume).sum())


def prepare(path,domain):
    centres,volume=cell_centres(path)
    radius=domain['config']['chamber_radius_mm']*.001
    z0=domain['config']['chamber_start_z_mm']*.001
    length=domain['config']['chamber_length_mm']*.001
    outlet=z0+length
    solid_end=max([z0]+[face['bounds_mm'][5]*.001 for face in domain['faces']
        if face['role']=='wall' and face['bounds_mm'][5]*.001<outlet-1e-6])
    xy=interior_xy(domain)
    # Exclude enclosure faces reaching the outlet, then place both sections
    # downstream of all metal. This also handles short user-defined chambers.
    points=np.array([(x,y,solid_end+fraction*(outlet-solid_end)) for fraction in (.1,.4) for x,y in sorted(xy)])
    distance,index=cKDTree(centres).query(points,k=16)
    if np.any(distance[:,0]>radius/4):
        raise ValueError('内部共同采样点离保存流体单元过远。')
    scale=np.maximum(distance[:,-1],1e-12)
    offsets=(centres[index]-points[:,None,:])/scale[:,None,None]
    design=np.concatenate([np.ones((*offsets.shape[:2],1)),offsets],axis=2)
    weight=1/np.maximum(distance/scale[:,None],.05)
    matrix=design*weight[:,:,None]
    if np.any(np.linalg.cond(matrix)>1e8):
        raise ValueError('内部共同采样的局部插值模板退化。')
    coefficients=np.linalg.pinv(matrix)[:,0,:]*weight
    return dict(points=points,index=index,coefficients=coefficients,cell_count=len(centres),
                volume_m3=volume,max_nearest_distance_m=float(distance[:,0].max()))


def sample(path,sampler):
    import h5py
    with h5py.File(path,'r') as f:
        group=f['results/1/phase-1/cells']
        def values(name):
            dataset=group[name+'/1']
            if int(dataset.attrs['minId'][0])!=1 or len(dataset)!=sampler['cell_count']:
                raise ValueError('内部场数据与保存网格单元编号不一致。')
            return dataset[()]
        fields=np.column_stack([values('SV_U'),values('SV_V'),values('SV_W'),values('SV_Y')[:,0]])
    data=np.einsum('ij,ijk->ik',sampler['coefficients'],fields[sampler['index']])
    if not np.isfinite(data).all():raise ValueError('内部场共同采样出现非有限数据。')
    return dict(points_m=sampler['points'].tolist(),values=data.tolist(),
                columns=['u_m_s','v_m_s','w_m_s','h2_mass_fraction'],
                sampling='common sections at 0.1 and 0.4 of clear chamber length downstream of metal; weighted local linear reconstruction using 16 saved cell centroids',
                max_nearest_distance_m=sampler['max_nearest_distance_m'])
