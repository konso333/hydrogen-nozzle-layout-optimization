"""Actual saved-cell section maps, clipped by mesh faces rather than CAD colours.

The section is piecewise constant on actual cut-cell polygons. No interpolation
across walls/holes or reconstruction of a missing field is performed.
"""
import os
for _key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[_key]='1'
import json
from pathlib import Path
import sys
import numpy as np

FIELDS={'speed':('Velocity magnitude','m/s','viridis'),
        'pressure':('Static gauge pressure','Pa','coolwarm'),
        'temperature':('Temperature','K','inferno'),
        'h2':('Hydrogen mass fraction','kg/kg','viridis')}


def face_triangles(vertices,eps):
    """Ear-clip the dominant projection; retain actual 3-D vertices on warped faces."""
    if len(vertices)==3:return [vertices]
    if len(vertices)>128:raise ValueError('截面网格面超过 128 个顶点，暂不能可靠三角化。')
    normal=np.cross(vertices,np.roll(vertices,-1,axis=0)).sum(axis=0)
    projected=np.delete(vertices,int(np.argmax(abs(normal))),axis=1)
    cross=lambda a,b:a[0]*b[1]-a[1]*b[0]
    signed=sum(cross(a,b) for a,b in zip(projected,np.roll(projected,-1,axis=0)))
    if abs(signed)<=eps**2:raise ValueError('截面遇到退化的网格面。')
    orientation=1 if signed>0 else -1;remaining=list(range(len(vertices)));triangles=[]
    while len(remaining)>3:
        for i,current in enumerate(remaining):
            prev=remaining[i-1];following=remaining[(i+1)%len(remaining)]
            a,b,c=projected[[prev,current,following]]
            if cross(b-a,c-b)*orientation<=eps**2:continue
            blocked=False
            for other in remaining:
                if other in (prev,current,following):continue
                p=projected[other]
                turns=[orientation*cross(b-a,p-a),orientation*cross(c-b,p-b),orientation*cross(a-c,p-c)]
                if min(turns)>=-eps**2:
                    blocked=True;break
            if blocked:continue
            triangles.append(vertices[[prev,current,following]]);remaining.pop(i);break
        else:raise ValueError('网格面投影不能可靠三角化，已停止场图生成。')
    triangles.append(vertices[remaining]);return triangles


def section(case_path,data_path,*,axis='y',position_mm=0.):
    import h5py
    from nozzle_platform.nozzle_gpu_probe import _field
    if axis not in ('x','y') or not np.isfinite(position_mm):raise ValueError('截面只能选择 X 或 Y 方向的有限坐标。')
    normal='xyz'.index(axis);plane=float(position_mm)/1000;project=[i for i in range(3) if i!=normal]
    with h5py.File(case_path) as f,h5py.File(data_path) as d:
        mesh=f['meshes/1'];top=mesh['cells/zoneTopology']
        if list(top['minId'][()])!=[1] or len(top['maxId'])!=1:raise ValueError('云图当前要求一个连续流体单元区。')
        count=int(top['maxId'][0]);group=d['results/1/phase-1/cells']
        values={n:_field(group,n,1,count) for n in ['SV_U','SV_V','SV_W','SV_P','SV_T']}
        y=_field(group,'SV_Y',1,count,width=4)
        if y.min()<0 or y.max()>1 or np.max(abs(y.sum(axis=1)-1))>1e-8:raise ValueError('保存组分范围或闭合检查未通过，不能生成云图。')
        if np.any(values['SV_T']<=0):raise ValueError('保存温度无效。')
        fields=dict(speed=np.sqrt(sum(values[n]**2 for n in ['SV_U','SV_V','SV_W'])),
                    pressure=values['SV_P'],temperature=values['SV_T'],h2=y[:,0])
        points=mesh['nodes/coords/1'][()];counts=mesh['faces/nodes/1/nnodes'][()].astype(np.int64)
        nodes=mesh['faces/nodes/1/nodes'][()].astype(np.int64)-1
        owner=mesh['faces/c0/1'][()].astype(np.int64)-1
        neighbour=np.full(len(owner),-1,dtype=np.int64);ds=mesh['faces/c1/1']
        first=int(ds.attrs['minId'][0])-1;neighbour[first:first+len(ds)]=ds[()].astype(np.int64)-1
    if (points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all() or len(owner)!=len(counts)
            or np.any(counts<3) or int(counts.sum())!=len(nodes) or nodes.min()<0 or nodes.max()>=len(points)
            or owner.min()<0 or owner.max()>=count or neighbour.max()>=count):raise ValueError('保存网格拓扑不完整。')
    eps=max(float(np.ptp(points,axis=0).max()),1.)*1e-10
    if plane<points[:,normal].min()-eps or plane>points[:,normal].max()+eps:raise ValueError('所选截面位于流体网格之外。')
    requested_plane=plane
    # A deterministic one-sided limit avoids an entire face lying in the cut
    # plane. The effective location is recorded; no solution field is changed.
    offset=eps*8
    plane+=offset if plane-offset<points[:,normal].min() else -offset
    offsets=np.r_[0,np.cumsum(counts)];segments={};coords={}
    def key(p):
        k=tuple(np.rint(p[project]/eps).astype(np.int64));coords.setdefault(k,p[project]*1000);return k
    def add(cell,a,b):
        if cell>=0 and a!=b:
            edges=segments.setdefault(int(cell),set());edge=tuple(sorted((a,b)))
            # Coincident pieces of adjacent triangulated warped faces cancel
            # from the boundary of the same cell, rather than create a flap.
            if edge in edges:edges.remove(edge)
            else:edges.add(edge)
    for lo in range(0,len(counts),20000):
        hi=min(lo+20000,len(counts));ys=points[nodes[offsets[lo]:offsets[hi]],normal]-plane
        local=offsets[lo:hi]-offsets[lo]
        low=np.minimum.reduceat(ys,local);high=np.maximum.reduceat(ys,local)
        selected=np.flatnonzero((low<=eps)&(high>=-eps))+lo
        for face in selected:
            verts=points[nodes[offsets[face]:offsets[face+1]]];dist=verts[:,normal]-plane
            if np.all(abs(dist)<=eps):pairs=[(key(a),key(b)) for a,b in zip(verts,np.roll(verts,-1,axis=0))]
            else:
                pairs=[]
                for triangle in face_triangles(verts,eps):
                    dist=triangle[:,normal]-plane;intersections=[]
                    if np.all(abs(dist)<=eps):
                        pairs.extend((key(a),key(b)) for a,b in zip(triangle,np.roll(triangle,-1,axis=0)));continue
                    for i,a in enumerate(triangle):
                        j=(i+1)%3;b=triangle[j]
                        if abs(dist[i])<=eps:intersections.append(key(a))
                        if (dist[i]<-eps and dist[j]>eps) or (dist[i]>eps and dist[j]<-eps):
                            if tuple(a)>tuple(b):a,b=b,a
                            da,db=a[normal]-plane,b[normal]-plane
                            intersections.append(key(a+(b-a)*(-da)/(db-da)))
                    intersections=list(dict.fromkeys(intersections))
                    if len(intersections)==2:pairs.append(tuple(intersections))
                    elif len(intersections)>2:raise ValueError('三角片切面不确定，原始计算文件仍保留。')
            for a,b in set(tuple(sorted(p)) for p in pairs):add(owner[face],a,b);add(neighbour[face],a,b)
    polygons=[];ids=[];seen=set();tangential=0
    for cell,edges in sorted(segments.items()):
        graph={}
        for a,b in edges:graph.setdefault(a,set()).add(b);graph.setdefault(b,set()).add(a)
        if len(graph)<3:tangential+=1;continue
        if any(len(v)!=2 for v in graph.values()):raise ValueError('截面单元边界未形成闭合多边形，已停止云图导出。')
        unused=set(graph)
        while unused:
            start=min(unused);loop=[start];previous=None;current=start
            for _ in range(len(graph)+1):
                following=next(v for v in sorted(graph[current]) if v!=previous)
                if following==start:break
                if following in loop:raise ValueError('截面多边形自交或未闭合。')
                loop.append(following);previous,current=current,following
            else:raise ValueError('截面多边形追踪超出预算。')
            unused.difference_update(loop);polygon=np.array([coords[k] for k in loop])
            area=abs(np.dot(polygon[:,0],np.roll(polygon[:,1],-1))-np.dot(polygon[:,1],np.roll(polygon[:,0],-1)))/2
            if area<=eps**2*1e6:tangential+=1;continue
            signature=tuple(sorted(loop))
            if signature in seen:continue # Face-coincident cuts use the smaller cell ID.
            seen.add(signature);polygons.append(polygon);ids.append(cell)
    if not polygons:raise ValueError('所选截面没有非零面积的流体单元，请调整截面坐标。')
    ids=np.asarray(ids,dtype=np.int64)
    return dict(polygons=polygons,cell_ids=ids+1,values={n:v[ids] for n,v in fields.items()},
                axis=axis,position_mm=float(position_mm),effective_position_mm=plane*1000,
                geometric_offset_mm=(plane-requested_plane)*1000,project_axes=['xyz'[i] for i in project],
                source_cells=count,tangential_cells=tangential,
                scope='真实网格切面，多边形面耳切三角化后裁切，保留翘曲面三维顶点；采用已记录几何容差内的单侧截面避免面重合；单元内分片常数，不跨壁面插值。')


def extract(case_path,data_path,output,*,axis='y',position_mm=0.):
    from nozzle_platform.workflows import sha256,save
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    result=section(case_path,data_path,axis=axis,position_mm=position_mm)
    lengths=[len(p) for p in result['polygons']]
    np.savez_compressed(output/'section.npz',vertices=np.concatenate(result['polygons']),offsets=np.r_[0,np.cumsum(lengths)],
                        cell_ids=result['cell_ids'],**result['values'])
    import csv
    with (output/'section_values.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['cell_id',*FIELDS])
        writer.writerows(zip(result['cell_ids'],*(result['values'][n] for n in FIELDS)))
    report={k:v for k,v in result.items() if k not in ('polygons','values','cell_ids')}
    report.update(section_polygons=len(lengths),case_sha256=sha256(case_path),data_sha256=sha256(data_path),
                  section_sha256=sha256(output/'section.npz'),csv_sha256=sha256(output/'section_values.csv'),
                  bounds={n:[float(v.min()),float(v.max())] for n,v in result['values'].items()},
                  scientific_eligible=False,convergence_implied=False)
    save(output/'section.json',report);return report


def render(folders,*,labels=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from nozzle_platform.workflows import sha256,save
    folders=[Path(p) for p in folders];reports=[json.loads((p/'section.json').read_text(encoding='utf-8')) for p in folders]
    if not folders or len(folders)>3:raise ValueError('云图每次支持 1～3 个方案。')
    if any(sha256(p/'section.npz')!=r['section_sha256'] for p,r in zip(folders,reports)):raise ValueError('截面采样文件已改变。')
    if len({(r['axis'],r['position_mm']) for r in reports})!=1:raise ValueError('多方案云图必须使用同一物理截面。')
    limits={n:[min(r['bounds'][n][0] for r in reports),max(r['bounds'][n][1] for r in reports)] for n in FIELDS}
    for index,(folder,report) in enumerate(zip(folders,reports)):
        images={}
        with np.load(folder/'section.npz') as data:
            vertices=data['vertices'];offset=data['offsets'];polygons=[vertices[a:b] for a,b in zip(offset,offset[1:])]
            for name,(label,unit,cmap) in FIELDS.items():
                fig,ax=plt.subplots(figsize=(7,7),layout='constrained')
                low,high=limits[name]
                if low==high:
                    pad=max(abs(low)*1e-6,1e-12);low-=pad;high+=pad
                collection=PolyCollection(polygons,array=data[name],cmap=cmap,edgecolors='none',antialiaseds=False,clim=(low,high))
                ax.add_collection(collection);ax.autoscale_view();ax.set_aspect('equal')
                ax.set_xlabel(report['project_axes'][0].upper()+' [mm]');ax.set_ylabel(report['project_axes'][1].upper()+' [mm]')
                title=(labels[index]+'\n' if labels else '')+label+' | '+report['axis'].upper()+'='+str(report['position_mm'])+' mm (one-sided limit)'
                ax.set_title(title);fig.colorbar(collection,ax=ax,label=label+' ['+unit+']')
                fig.text(.5,.005,'Saved cold-flow iterate; convergence/physics not certified',ha='center',fontsize=8)
                path=folder/(name+'.png');fig.savefig(path,dpi=140);plt.close(fig);images[path.name]=sha256(path)
        report.update(common_colour_limits=limits,images_sha256=images)
        save(folder/'maps.json',report)
    return limits


def assembly_image(preview,coordinates,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from nozzle_platform.workflows import sha256,save
    output=Path(output)
    with np.load(preview) as data:vertices=data['vertices'];triangles=data['triangles']
    shifts=np.array([[x,y,0.] for x,y in coordinates],float)
    if (vertices.ndim!=2 or vertices.shape[1]!=3 or triangles.ndim!=2 or triangles.shape[1]!=3
            or not np.isfinite(vertices).all() or not len(shifts) or triangles.min()<0 or triangles.max()>=len(vertices)):
        raise ValueError('装配图源曲面不完整。')
    stride=max(1,int(np.ceil(len(triangles)*len(shifts)/24000)))
    faces=vertices[triangles[::stride]]
    placed=np.concatenate([faces+shift for shift in shifts])
    fig=plt.figure(figsize=(8,7),layout='constrained');ax=fig.add_subplot(projection='3d')
    ax.add_collection3d(Poly3DCollection(placed,facecolors='#72a997',edgecolors='none',alpha=1))
    lower=vertices.min(axis=0)+shifts.min(axis=0);upper=vertices.max(axis=0)+shifts.max(axis=0)
    span=np.maximum(upper-lower,1e-6);centre=(lower+upper)/2
    for i,setlim in enumerate([ax.set_xlim,ax.set_ylim,ax.set_zlim]):setlim(centre[i]-span[i]*.55,centre[i]+span[i]*.55)
    ax.set_box_aspect(span);ax.view_init(elev=22,azim=-55);ax.set(xlabel='X [mm]',ylabel='Y [mm]',zlabel='Z [mm]',title=f'Nozzle assembly | N={len(shifts)}')
    fig.text(.5,.005,'Actual CAD surface preview; sampled triangles. STEP contains full assembly.',ha='center',fontsize=8)
    fig.savefig(output,dpi=140);plt.close(fig)
    report=dict(preview_sha256=sha256(preview),coordinates_mm=coordinates,triangle_stride=stride,
                drawn_triangles=len(placed),full_preview_triangles=len(triangles)*len(shifts),image_sha256=sha256(output))
    save(output.with_suffix('.json'),report);return report


def main():
    from nozzle_platform.workflows import save
    folder=Path(sys.argv[1]);spec=json.loads((folder/'postprocess.json').read_text(encoding='utf-8'))
    try:
        if spec['operation']=='extract':result=extract(spec['case'],spec['data'],spec['output'],axis=spec['axis'],position_mm=spec['position_mm'])
        elif spec['operation']=='render':result=render(spec['folders'],labels=spec['labels'])
        elif spec['operation']=='assembly':result=assembly_image(spec['preview'],spec['coordinates'],spec['output'])
        else:raise ValueError('Unknown field-map operation')
        save(folder/'postprocess_result.json',dict(ok=True,result=result))
    except Exception as error:
        save(folder/'postprocess_result.json',dict(ok=False,error=str(error)));raise


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));main()
