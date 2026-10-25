"""Validate native mixed-cell meshes and export unchanged topology in SI units."""

from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from nozzle_platform.domain_cad import ROLES


def require(condition,message):
    if not condition:raise ValueError(message)


def read_native(path):
    import h5py
    with h5py.File(path,'r') as f:
        mesh=f['meshes/1'];coords=mesh['nodes/coords']
        require(len(coords)==1,'原生网格需要一个连续节点坐标块。')
        dataset=next(iter(coords.values()))
        require(int(dataset.attrs['minId'][0])==1,'原生节点编号必须从 1 开始。')
        points=dataset[()]
        counts=mesh['faces/nodes/1/nnodes'][()].astype(np.int64)
        nodes=mesh['faces/nodes/1/nodes'][()].astype(np.int64)-1
        c0=mesh['faces/c0/1'][()].astype(np.int64)-1
        second=mesh['faces/c1/1'];start=int(second.attrs['minId'][0])-1
        c1=np.full(len(counts),-1,dtype=np.int64)
        c1[start:start+len(second)]=second[()].astype(np.int64)-1
        types=mesh['cells/ctype/1/cell-types'][()].astype(np.int8)
        top=mesh['faces/zoneTopology'];labels=np.full(len(counts),-1,dtype=np.int8)
        for name,first,end in zip(top['name'][()][0].decode().split(';'),
                top['minId'][()].astype(int)-1,top['maxId'][()].astype(int)):
            role='interior' if name.startswith('interior') else 'wall' if name in {'wall','passage_wall','nozzle_wall','chamber_wall'} else name
            require(role in ('interior',)+ROLES,'原生网格包含未知边界。')
            require(np.all(labels[first:end]==-1),'原生面区编号重叠。')
            labels[first:end]=0 if role=='interior' else ROLES.index(role)+1
    require(points.ndim==2 and points.shape[1]==3 and np.isfinite(points).all(),'原生节点坐标无效。')
    require(len(counts)>0 and counts.min()>=3 and counts.sum()==len(nodes),'原生面节点拓扑无效。')
    require(nodes.min()>=0 and nodes.max()<len(points),'原生面节点越界。')
    require(len(c0)==len(counts) and np.all(c0>=0) and np.all(c0<len(types)),'原生面第一单元编号无效。')
    require(np.all(c1<len(types)) and np.all(c1>=-1) and np.all(c0!=c1),'原生面第二单元编号无效。')
    require(np.array_equal(c1>=0,labels==0) and np.all(labels>=0),'原生面区与内外边界不一致。')
    require(len(types)>0 and np.all(np.isin(types,[2,3,4,5,6,7])),'原生单元类型无效。')
    return dict(points_m=points,face_counts=counts,face_nodes=nodes,
        face_offsets=np.r_[0,np.cumsum(counts)],c0=c0,c1=c1,cell_types=types,face_labels=labels)


def audit(arrays,domain,regions=None):
    """Check connectivity, cell closure and exterior geometry independently."""
    from nozzle_platform.flow_validation import polygon_patches
    points,counts,nodes,offsets=(arrays[k] for k in ['points_m','face_counts','face_nodes','face_offsets'])
    c0,c1,labels=(arrays[k] for k in ['c0','c1','face_labels']);cells=len(arrays['cell_types'])
    internal=c1>=0
    graph=coo_matrix((np.ones(internal.sum(),dtype=np.int8),(c0[internal],c1[internal])),shape=(cells,cells))
    components=connected_components(graph,directed=False,return_labels=False)
    require(components==1,'原生体网格包含分离流体空间。')
    areas=np.zeros(5);boundary_volume=0.;closure=np.zeros((cells,3));volumes=np.zeros(cells)
    max_area=0.;section=[];moment=np.zeros((cells,3)) if regions else None
    plane_y=float((points[:,1].min()+points[:,1].max())/2)
    for first in range(0,len(counts),20000):
        selected=np.arange(first,min(first+20000,len(counts)));triangles=counts[selected]-2
        face=np.repeat(selected,triangles);anchor=np.repeat(offsets[selected],triangles)
        local=np.arange(triangles.sum())-np.repeat(np.r_[0,np.cumsum(triangles)[:-1]],triangles)
        a,b,c=points[nodes[anchor]],points[nodes[anchor+1+local]],points[nodes[anchor+2+local]]
        normal=np.cross(b-a,c-a)/2;volume=np.einsum('ij,ij->i',a,np.cross(b,c))/6
        area=np.linalg.norm(normal,axis=1);max_area=max(max_area,float(area.max()))
        areas+=np.bincount(labels[face],weights=area,minlength=5)
        boundary_volume+=float(volume[labels[face]>0].sum())
        np.add.at(closure,c0[face],normal);np.add.at(volumes,c0[face],volume)
        interior=c1[face]>=0
        np.add.at(closure,c1[face[interior]],-normal[interior]);np.add.at(volumes,c1[face[interior]],-volume[interior])
        if moment is not None:
            m=volume[:,None]*(a+b+c)/4
            np.add.at(moment,c0[face],m);np.add.at(moment,c1[face[interior]],-m[interior])
        # A middle plane also intersects sectors lying entirely above Y=0.
        y=points[nodes[offsets[first]:offsets[selected[-1]+1]],1]
        starts=offsets[selected]-offsets[first]
        crosses=(np.minimum.reduceat(y,starts)<=plane_y)&(np.maximum.reduceat(y,starts)>=plane_y)
        section.extend(selected[crosses].tolist())
    error=float(np.abs(closure).max())
    require(error<max(max_area*1e-7,1e-20),'原生体单元面不闭合。')
    require(np.isfinite(volumes).all() and (np.all(volumes>1e-30) or np.all(volumes<-1e-30)),
            '原生网格存在退化或方向不一致的单元。')
    volume=abs(boundary_volume)
    require(np.isclose(abs(volumes).sum(),volume,rtol=.001,atol=0.),'原生单元体积与外边界积分不一致。')
    volume_error=abs(volume/(domain['volume_mm3']*1e-9)-1)
    errors={role:abs(areas[i+1]/(domain['boundary_area_mm2'][role]*1e-6)-1) for i,role in enumerate(ROLES)}
    require(volume_error<.01,'原生体网格与 CAD 体积差超过 1%。')
    worst=max(errors,key=errors.get)
    require(errors[worst]<.02,f'原生体网格边界面积与 CAD 差超过 2%：{worst} 偏差 {errors[worst]*100:.4f}%；请加密该入口或表面。')
    patches={}
    for i,role in enumerate(ROLES[:3],1):
        faces=np.flatnonzero(labels==i)
        patches[role]=polygon_patches([nodes[offsets[j]:offsets[j+1]] for j in faces])
        require(patches[role]==domain['boundary_patch_counts'][role],'原生体网格开口数量改变。')
    selected=np.asarray(section,dtype=int)
    sampled=len(selected)>20000
    if sampled:selected=selected[np.linspace(0,len(selected)-1,20000,dtype=int)]
    triangles=[]
    for face in selected:
        polygon=nodes[offsets[face]:offsets[face+1]]
        triangles.extend((polygon[0],polygon[j],polygon[j+1]) for j in range(1,len(polygon)-1))
    require(len(triangles)>0,'原生体网格截面预览为空。')
    triangles=np.array(triangles,dtype=np.int64);used,inverse=np.unique(triangles,return_inverse=True)
    report=dict(nodes=len(points),cells=cells,faces=len(counts),interior_faces=int(internal.sum()),
        connected_fluid_components=int(components),volume_m3=volume,minimum_signed_volume_m3=float(abs(volumes).min()),
        max_cell_area_closure_error_m2=error,relative_CAD_volume_error=volume_error,
        relative_boundary_area_errors=errors,boundary_mesh_area_m2={role:float(areas[i+1]) for i,role in enumerate(ROLES)},
        boundary_faces={role:int((labels==i+1).sum()) for i,role in enumerate(ROLES)},
        opening_counts=patches,unmapped_boundary_faces=0,nonmanifold_faces=0,units='m',
        bounds_m=[points.min(axis=0).tolist(),points.max(axis=0).tolist()],
        cell_type='mixed-polyhex',cell_type_counts={str(k):int(v) for k,v in zip(*np.unique(arrays['cell_types'],return_counts=True))},
        preview=dict(vertices=(points[used]*1000).round(6).tolist(),triangles=inverse.reshape(-1,3).tolist(),
            section=f'native mixed-cell faces crossing Y={plane_y*1000:g} mm; up to 20000 faces',sampled=sampled))
    if regions:
        centres=moment/volumes[:,None];require(np.isfinite(centres).all(),'局部加密检查的体单元形心无效。')
        summaries=[];sizes=np.cbrt(abs(volumes))*1000
        for region in regions:
            selected=np.all(centres>=np.array(region['lower_mm'])*.001,axis=1)&np.all(centres<=np.array(region['upper_mm'])*.001,axis=1)
            require(selected.any(),'局部加密区域内没有真实体单元：'+region['name'])
            mean=float(np.average(sizes[selected],weights=abs(volumes[selected])))
            require(mean<=2*region['target_size_mm']*(1+1e-10),
                f"局部体网格未达到目标尺寸作用检查：{region['name']}，实际平均等效尺度 {mean:.4g} mm，需不超过 {2*region['target_size_mm']:.4g} mm。")
            summaries.append(dict(name=region['name'],cells=int(selected.sum()),fluid_volume_m3=float(abs(volumes[selected]).sum()),
                target_size_mm=region['target_size_mm'],volume_weighted_equivalent_h_mm=mean,
                maximum_equivalent_h_mm=float(sizes[selected].max()),mean_to_target_limit=2.))
        report['volume_refinement_summary']=summaries
    return report


def write_fluent(path,arrays):
    """Change only face ordering and zone IDs; retain node and cell topology."""
    points,counts,nodes,offsets,c0,c1,types,labels=(arrays[k] for k in
        ['points_m','face_counts','face_nodes','face_offsets','c0','c1','cell_types','face_labels'])
    with Path(path).open('w',encoding='ascii',newline='\n') as out:
        out.write('(0 "Native mixed-cell mesh; unchanged topology; SI metres")\n(2 3)\n')
        out.write(f'(10 (0 1 {len(points):x} 1 3))\n(12 (0 1 {len(types):x} 0))\n(13 (0 1 {len(counts):x} 0))\n')
        out.write(f'(10 (1 1 {len(points):x} 1 3)(\n');np.savetxt(out,points,fmt='%.17e');out.write('))\n')
        out.write(f'(12 (2 1 {len(types):x} 1 0)(\n')
        for first in range(0,len(types),500):out.write(' '.join(f'{v:x}' for v in types[first:first+500])+'\n')
        out.write('))\n(39 (2 fluid fluid 1)())\n');cursor=1
        for label,role in enumerate(('interior',)+ROLES):
            selected=np.flatnonzero(labels==label);require(len(selected)>0,'导出的原生面区缺失。')
            bc=2 if label==0 else 10 if 'inlet' in role else 5 if role=='outlet' else 3
            kind='interior' if label==0 else 'velocity-inlet' if bc==10 else 'pressure-outlet' if bc==5 else 'wall'
            zone=label+3
            out.write(f'(13 ({zone:x} {cursor:x} {cursor+len(selected)-1:x} {bc:x} 0)(\n')
            for i in selected:
                row=nodes[offsets[i]:offsets[i+1]]+1
                out.write(f'{len(row):x} '+' '.join(f'{v:x}' for v in row)+f' {c0[i]+1:x} {c1[i]+1:x}\n')
            out.write(f'))\n(39 ({zone} {kind} {role} 1)())\n');cursor+=len(selected)
