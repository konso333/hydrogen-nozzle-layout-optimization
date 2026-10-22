"""Deterministic small 3-D slab and analytic checks for GPU material experiments.

Independent of production nozzle geometry, physics selection and convergence flags.
All boundary face normals follow the existing Fluent exporter: toward cell c0.
"""
from pathlib import Path
import numpy as np


def slab_mesh(path, divisions=(16, 4, 4), lengths=(.01, .01, .01)):
    if len(divisions)!=3 or any(type(n) is not int or not 2<=n<=32 for n in divisions):
        raise ValueError('Three integer divisions in 2..32 are required.')
    lengths=np.asarray(lengths,dtype=float)
    if lengths.shape!=(3,) or not np.isfinite(lengths).all() or np.any(lengths<=0):
        raise ValueError('Three finite positive lengths are required.')
    nx,ny,nz=divisions
    def node(i,j,k):return (i*(ny+1)+j)*(nz+1)+k
    points=np.array([(i*lengths[0]/nx,j*lengths[1]/ny,k*lengths[2]/nz)
                     for i in range(nx+1) for j in range(ny+1) for k in range(nz+1)])
    centres=np.array([((i+.5)*lengths[0]/nx,(j+.5)*lengths[1]/ny,(k+.5)*lengths[2]/nz)
                      for i in range(nx) for j in range(ny) for k in range(nz)])
    faces={}
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                cell=(i*ny+j)*nz+k
                v=[node(i,j,k),node(i+1,j,k),node(i+1,j+1,k),node(i,j+1,k),
                   node(i,j,k+1),node(i+1,j,k+1),node(i+1,j+1,k+1),node(i,j+1,k+1)]
                for local in [(0,3,7,4),(1,5,6,2),(0,4,5,1),(3,2,6,7),(0,1,2,3),(4,7,6,5)]:
                    face=[v[n] for n in local];key=tuple(sorted(face))
                    if key in faces:faces[key]['c1']=cell
                    else:faces[key]=dict(nodes=face,c0=cell,c1=-1)
    groups={name:[] for name in ('interior','cold','hot','insulated')}
    closure=np.zeros_like(centres);volumes=np.zeros(len(centres));areas={k:0. for k in groups}
    for face in faces.values():
        ids=face['nodes'];p=points[ids];centre=p.mean(axis=0)
        normal=np.cross(p[1]-p[0],p[2]-p[0])
        if np.dot(normal,centres[face['c0']]-centre)<0:
            ids.reverse();p=points[ids];normal=-normal
        normal=normal/np.linalg.norm(normal)*np.linalg.norm(np.cross(p[1]-p[0],p[3]-p[0]))
        if face['c1']>=0:
            assert np.dot(normal,centres[face['c1']]-centre)<0
            role='interior'
        elif np.all(p[:,0]==0):role='cold'
        elif np.all(p[:,0]==lengths[0]):role='hot'
        else:role='insulated'
        groups[role].append(face);areas[role]+=float(np.linalg.norm(normal))
        closure[face['c0']]-=normal
        volumes[face['c0']]-=np.dot(normal,centre)/3
        if face['c1']>=0:
            closure[face['c1']]+=normal
            volumes[face['c1']]+=np.dot(normal,centre)/3
    assert np.abs(closure).max()<1e-16 and np.all(volumes>0)
    assert np.allclose(volumes,np.prod(lengths)/len(centres),rtol=1e-12,atol=0)
    with Path(path).open('w',encoding='ascii',newline='\n') as stream:
        stream.write('(0 "Independent transport slab; SI metres")\n(2 3)\n')
        stream.write(f'(10 (0 1 {len(points):x} 1 3))\n(12 (0 1 {len(centres):x} 0))\n(13 (0 1 {len(faces):x} 0))\n')
        stream.write(f'(10 (1 1 {len(points):x} 1 3)(\n')
        np.savetxt(stream,points,fmt='%.17e');stream.write('))\n')
        stream.write(f'(12 (2 1 {len(centres):x} 1 4))\n(39 (2 fluid fluid 1)())\n')
        cursor=1
        for zone,(name,rows) in enumerate(groups.items(),3):
            bc=2 if name=='interior' else 3
            stream.write(f'(13 ({zone:x} {cursor:x} {cursor+len(rows)-1:x} {bc:x} 4)(\n')
            for row in rows:
                stream.write(' '.join(f'{n+1:x}' for n in row['nodes'])+f" {row['c0']+1:x} {row['c1']+1:x}\n")
            stream.write(f'))\n(39 ({zone} {"interior" if bc==2 else "wall"} {name} 1)())\n')
            cursor+=len(rows)
    return dict(cells=len(centres),nodes=len(points),faces=len(faces),lengths_m=lengths.tolist(),
                divisions=list(divisions),heated_area_m2=float(lengths[1]*lengths[2]),
                volume_m3=float(volumes.sum()),max_area_closure_error_m2=float(np.abs(closure).max()),
                boundary_area_m2=areas,cell_centres_m=centres.tolist())


def assess_heat(heat_W, conductivity, mesh, temperatures=(300.,310.), relative_limit=1e-5):
    """Heat-rate and conservation checks for a uniform constant-k stationary slab."""
    if set(heat_W)!={'cold','hot','insulated'} or not all(np.isfinite(v) for v in heat_W.values()):
        raise ValueError('All three finite boundary heat rates are required.')
    if not np.isfinite(conductivity) or conductivity<=0 or not 0<relative_limit<1:
        raise ValueError('Positive conductivity and a meaningful tolerance are required.')
    delta=float(temperatures[1]-temperatures[0])
    if not np.isfinite(delta) or delta<=0:raise ValueError('Hot temperature must exceed cold temperature.')
    expected=conductivity*mesh['heated_area_m2']*delta/mesh['lengths_m'][0]
    error=max(abs(abs(heat_W[k])-expected)/expected for k in ('cold','hot'))
    imbalance=abs(sum(heat_W.values()))/expected
    insulated=abs(heat_W['insulated'])/expected
    return dict(expected_heat_W=expected,boundary_heat_W=heat_W,relative_heat_error=error,
                relative_heat_imbalance=imbalance,relative_insulated_heat=insulated,relative_limit=relative_limit,
                opposite_heat_signs=heat_W['cold']*heat_W['hot']<0,
                passed=bool(error<=relative_limit and imbalance<=relative_limit and insulated<=relative_limit
                            and heat_W['cold']*heat_W['hot']<0))


def saved_fields(case_path, data_path):
    """Read complete slab fields and integrated wall heat (W/face, not W/m2).

    SV_HEAT_FLUX sums are independently calibrated against the baseline Fluent
    get_heat_transfer report. Unsupported/chunked layouts are rejected explicitly.
    """
    import h5py
    with h5py.File(case_path,'r') as case,h5py.File(data_path,'r') as data:
        mesh=case['meshes/1'];top=mesh['faces/zoneTopology']
        names=top['name'][()][0].decode().split(';')
        if set(names)!={'interior','cold','hot','insulated'}:raise ValueError('Unknown slab boundary zones.')
        ct=mesh['cells/zoneTopology'];n=int(np.sum(ct['maxId'][()]-ct['minId'][()]+1))
        fields={}
        for key in ['SV_Y','SV_MU_LAM','SV_T','SV_U','SV_V','SV_W']:
            group=data['results/1/phase-1/cells/'+key]
            if set(group)!={'1'}:raise ValueError('Unsupported cell field chunks.')
            ds=group['1'];value=ds[()]
            if int(ds.attrs['minId'][0])!=1 or int(ds.attrs['maxId'][0])!=n or len(value)!=n:
                raise ValueError('Incomplete saved cell field.')
            if not np.isfinite(value).all():raise ValueError('Non-finite saved field.')
            fields[key]=value
        heat_group=data['results/1/phase-1/faces/SV_HEAT_FLUX']
        if set(heat_group)!={'1'}:raise ValueError('Unsupported wall heat chunks.')
        ds=heat_group['1'];begin=int(ds.attrs['minId'][0]);end=int(ds.attrs['maxId'][0])
        heat=ds[()].reshape(-1)
        if len(heat)!=end-begin+1 or not np.isfinite(heat).all():raise ValueError('Invalid saved wall heat.')
        rates={}
        for name,first,last in zip(names,top['minId'][()],top['maxId'][()]):
            if name=='interior':continue
            if int(first)<begin or int(last)>end:raise ValueError('Wall heat does not cover all boundary faces.')
            rates[name]=float(heat[int(first)-begin:int(last)-begin+1].sum())
        points=mesh['nodes/coords/1'][()]
        face_counts=mesh['faces/nodes/1/nnodes'][()].astype(int)
        face_nodes=mesh['faces/nodes/1/nodes'][()].astype(int)-1
        offsets=np.r_[0,np.cumsum(face_counts)]
        c0=mesh['faces/c0/1'][()].astype(int)-1
        c1=np.full(len(c0),-1,dtype=int)
        ds=mesh['faces/c1/1'];first=int(ds.attrs['minId'][0])-1
        c1[first:first+len(ds)]=ds[()].astype(int)-1
        vertex_ids=[set() for _ in range(n)]
        for i,(a,b) in enumerate(zip(c0,c1)):
            nodes=face_nodes[offsets[i]:offsets[i+1]]
            if not 0<=a<n or not -1<=b<n or nodes.min()<0 or nodes.max()>=len(points):
                raise ValueError('Invalid saved mesh connectivity.')
            vertex_ids[a].update(nodes)
            if b>=0:vertex_ids[b].update(nodes)
        if any(len(v)!=8 for v in vertex_ids):raise ValueError('Benchmark requires hexahedral cells.')
        centres=np.array([points[sorted(v)].mean(axis=0) for v in vertex_ids])
    return dict(fields=fields,heat_W=rates,cell_centres_m=centres,
                bounds_m=np.array([points.min(axis=0),points.max(axis=0)]),cells=n)
