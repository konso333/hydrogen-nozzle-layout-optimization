"""Independent checks against saved Fluent case/data, not controller responses."""

import math


def polygon_patches(polygons):
    parents = list(range(len(polygons)))
    def find(index):
        while parents[index]!=index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    edges = {}
    for index,nodes in enumerate(polygons):
        nodes = list(map(int,nodes))
        for a,b in zip(nodes,nodes[1:]+nodes[:1]):
            edge = (min(a,b),max(a,b))
            if edge in edges:
                parents[find(index)] = find(edges[edge])
            else:
                edges[edge] = index
    return len({find(index) for index in range(len(polygons))})


def case_openings(path):
    import h5py
    import numpy as np
    with h5py.File(path,'r') as case:
        mesh = case['meshes/1']
        top = mesh['faces/zoneTopology']
        names = top['name'][()][0].decode().split(';')
        counts = mesh['faces/nodes/1/nnodes'][()].astype(int)
        nodes = mesh['faces/nodes/1/nodes'][()].astype(int)-1
        offsets = np.r_[0,np.cumsum(counts)]
        result = {}
        for name,start,end in zip(names,top['minId'][()].astype(int)-1,top['maxId'][()].astype(int)):
            if name in {'fuel_inlet','oxidizer_inlet','outlet'}:
                result[name] = polygon_patches([nodes[offsets[i]:offsets[i+1]] for i in range(start,end)])
        return result


def case_volume(path):
    """Divergence integral of the saved oriented exterior polygon faces, in m3."""
    import h5py
    import numpy as np
    with h5py.File(path,'r') as case:
        mesh = case['meshes/1']
        top = mesh['faces/zoneTopology']
        names = top['name'][()][0].decode().split(';')
        xyz = mesh['nodes/coords/1'][()]
        counts = mesh['faces/nodes/1/nnodes'][()].astype(int)
        nodes = mesh['faces/nodes/1/nodes'][()].astype(int)-1
        offsets = np.r_[0,np.cumsum(counts)]
        selected = np.concatenate([np.arange(start,end) for name,start,end in zip(names,
            top['minId'][()].astype(int)-1,top['maxId'][()].astype(int))
            if name in {'fuel_inlet','oxidizer_inlet','outlet','wall'}])
        triangles = counts[selected]-2
        if np.any(triangles<1):
            raise ValueError('保存的外边界面退化。')
        anchor = np.repeat(offsets[selected],triangles)
        local = np.arange(triangles.sum())-np.repeat(np.r_[0,np.cumsum(triangles)[:-1]],triangles)
        second = anchor+1+local
        a,b,c = xyz[nodes[anchor]],xyz[nodes[second]],xyz[nodes[second+1]]
        volume = abs(float(np.einsum('ij,ij->i',a,np.cross(b,c)).sum()/6))
        if not math.isfinite(volume) or volume<=0:
            raise ValueError('保存的 Fluent 网格体积无效。')
        return volume


def verify_saved_fluxes(directory, point):
    """Cross-check live boundary mass and convective species reports from HDF5."""
    import h5py
    import numpy as np
    result = {}
    with h5py.File(directory/'final.cas.h5','r') as case, h5py.File(directory/'final.dat.h5','r') as data:
        top = case['meshes/1/faces/zoneTopology']
        names = top['name'][()][0].decode().split(';')
        flux = data['results/1/phase-1/faces/SV_FLUX/1'][()]
        species = data['results/1/phase-1/faces/SV_Y/1']
        first = int(species.attrs['minId'][0])-1
        fractions = species[()]
        if fractions.ndim!=2 or fractions.shape[1]!=4:
            raise ValueError('保存的 Fluent 组分数组与冷流模板不符。')
        for name,start,end in zip(names,top['minId'][()].astype(int)-1,top['maxId'][()].astype(int)):
            if name not in point['mass_flow_kg_s']:
                continue
            rate = -float(flux[start:end].sum())
            if not math.isclose(rate,point['mass_flow_kg_s'][name],rel_tol=1e-8,abs_tol=1e-13):
                raise ValueError('Fluent 保存文件与在线质量流量报告不一致。')
            result[name] = {'mass_flow_kg_s':rate}
            if name!='wall':
                rates = -(flux[start:end,None]*fractions[start-first:end-first]).sum(axis=0)
                result[name]['convective_species_kg_s'] = dict(zip(('h2','o2','h2o','n2'),map(float,rates)))
                for species_name,value in result[name]['convective_species_kg_s'].items():
                    if not math.isclose(value,point['species_convective_flow_kg_s'][species_name][name],rel_tol=1e-8,abs_tol=1e-13):
                        raise ValueError('Fluent 保存文件与在线组分对流通量不一致。')
        fields = data['results/1/phase-1/cells/SV_Y/1'][()]
        if not np.isfinite(fields).all() or fields.min() < -1e-10 or fields.max()>1+1e-10:
            raise ValueError('保存的组分质量分数越界或包含非有限数。')
        if np.max(abs(fields.sum(axis=1)-1))>1e-8:
            raise ValueError('保存的组分质量分数之和不为 1。')
    return {'checked':True,'boundary_reports':result,'species_fraction_range_checked':True}
