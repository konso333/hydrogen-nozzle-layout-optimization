"""Linear tetrahedra, explicit face topology and SI Fluent mesh export.

Gmsh minSICN is reported separately from Fluent orthogonal quality.
"""

from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from nozzle_platform.domain_cad import ROLES, classify
from nozzle_platform.workflows import save


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nozzle_metal_end_zmax(domain):
    """Exclude the enclosure walls reaching the outlet from the metal end."""
    config=domain['config']
    outlet=config['outlet_z_mm']
    end=max([config['chamber_start_z_mm']]+[face['bounds_mm'][5]
        for face in domain['faces'] if face['role']=='wall'
        and face['bounds_mm'][5]<outlet-.001])
    return end


def passage_refinement_zmax(domain):
    """Cover protruding nozzle metal with a fixed physical refinement extent."""
    return min(nozzle_metal_end_zmax(domain)+2.,domain['config']['outlet_z_mm'])


def face_topology(points, cells, boundaries):
    """Reject invalid topology; orient legacy file normals toward cell c0."""
    require(points.ndim == 2 and points.shape[1] == 3 and np.isfinite(points).all(), "网格坐标无效。")
    require(cells.ndim == 2 and cells.shape[1] == 4 and len(cells) > 0, "需要一阶四面体体网格。")
    require(cells.min() >= 0 and cells.max() < len(points), "体网格节点索引越界。")
    require(len(np.unique(np.sort(cells, axis=1), axis=0)) == len(cells), "体网格有重复单元。")
    p = points[cells]
    volumes = np.einsum('ij,ij->i', p[:,1]-p[:,0], np.cross(p[:,2]-p[:,0], p[:,3]-p[:,0]))/6
    require(np.all(volumes > 0), "体网格有零体积或负体积单元。")
    local = np.array([[1,2,3],[0,3,2],[0,1,3],[0,2,1]])
    all_faces = cells[:,local].reshape(-1,3)
    owners = np.repeat(np.arange(len(cells)),4)
    keys, first, inverse, counts = np.unique(np.sort(all_faces,axis=1),axis=0,
        return_index=True,return_inverse=True,return_counts=True)
    require(counts.max() <= 2, "体网格有非流形面。")
    faces, c0 = all_faces[first].copy(), owners[first]
    c1 = np.full(len(faces),-1,dtype=np.int64)
    interior = counts == 2
    order = np.argsort(inverse,kind='stable')
    starts = np.r_[0,np.cumsum(counts)[:-1]]
    c1[interior] = owners[order[starts[interior]+1]]
    centres, fc = p.mean(axis=1), points[faces].mean(axis=1)
    normals = np.cross(points[faces[:,1]]-points[faces[:,0]],points[faces[:,2]]-points[faces[:,0]])
    flip = np.einsum('ij,ij->i',normals,centres[c0]-fc) < 0
    faces[flip,1], faces[flip,2] = faces[flip,2].copy(),faces[flip,1].copy()
    normals[flip] *= -1
    require(np.all(np.einsum('ij,ij->i',normals,centres[c0]-fc) > 0), "网格面方向检查失败。")
    require(np.all(np.einsum('ij,ij->i',normals[interior],centres[c1[interior]]-fc[interior]) < 0), "相邻体单元位于面的同侧。")
    def rows(a):
        return np.ascontiguousarray(a).view(np.dtype([('a',a.dtype),('b',a.dtype),('c',a.dtype)])).reshape(-1)
    labels = np.full(len(faces),-1,dtype=np.int8)
    labels[interior] = 0
    key_rows = rows(keys)
    areas, boundary_counts = {},{}
    require(set(boundaries) == set(ROLES), "网格需要完整四类边界。")
    for label,role in enumerate(ROLES,1):
        triangles = np.asarray(boundaries[role],dtype=keys.dtype)
        require(triangles.ndim == 2 and triangles.shape[1] == 3 and len(triangles)>0, "边界三角形缺失。")
        boundary_keys = np.sort(triangles,axis=1)
        index = np.searchsorted(key_rows,rows(boundary_keys))
        require(np.all(index<len(keys)), "边界包含不属于体网格的三角形。")
        require(np.array_equal(keys[index],boundary_keys), "边界与体单元不匹配。")
        require(len(np.unique(index)) == len(index) and np.all(counts[index]==1) and np.all(labels[index]==-1), "边界重复标记或包含内部面。")
        labels[index] = label
        boundary_counts[role] = len(index)
        areas[role] = float(np.linalg.norm(normals[index],axis=1).sum()/2)
    require(np.all(labels>=0), "存在未分类的外部边界面。")
    graph = coo_matrix((np.ones(interior.sum(),dtype=np.int8),(c0[interior],c1[interior])),shape=(len(cells),len(cells)))
    components = connected_components(graph,directed=False,return_labels=False)
    require(components == 1, "体网格含有分离的流体空间。")
    closure = np.zeros((len(cells),3))
    np.add.at(closure,c0,-normals/2)
    np.add.at(closure,c1[interior],normals[interior]/2)
    error = float(np.abs(closure).max())
    require(error < max(float(np.linalg.norm(normals,axis=1).max())*1e-10,1e-24), "体单元面不闭合。")
    report = {"nodes":len(points),"cells":len(cells),"faces":len(faces),
        "interior_faces":int(interior.sum()),"connected_fluid_components":int(components),
        "minimum_signed_volume_m3":float(volumes.min()),"volume_m3":float(volumes.sum()),
        "boundary_faces":boundary_counts,"boundary_mesh_area_m2":areas,
        "max_cell_area_closure_error_m2":error,"unmapped_boundary_faces":0,"nonmanifold_faces":0}
    return faces,c0,c1,labels,report


def write_fluent(path, points, cells, boundaries):
    faces,c0,c1,labels,report = face_topology(points,cells,boundaries)
    zones = [(3,'interior',2,0)] + [(i+4,role,10 if 'inlet' in role else 5 if role=='outlet' else 3,i+1) for i,role in enumerate(ROLES)]
    with Path(path).open('w',encoding='ascii',newline='\n') as out:
        out.write('(0 "Nozzle platform volume mesh; SI metres")\n(2 3)\n')
        out.write(f'(10 (0 1 {len(points):x} 1 3))\n(12 (0 1 {len(cells):x} 0))\n(13 (0 1 {len(faces):x} 0))\n')
        out.write(f'(10 (1 1 {len(points):x} 1 3)(\n')
        np.savetxt(out,points,fmt='%.17e')
        out.write('))\n')
        out.write(f'(12 (2 1 {len(cells):x} 1 2))\n')
        cursor = 1
        for zone,name,bc,label in zones:
            ix = np.flatnonzero(labels==label)
            if not len(ix):
                continue
            out.write(f'(13 ({zone:x} {cursor:x} {cursor+len(ix)-1:x} {bc:x} 3)(\n')
            for f,a,b in zip(faces[ix]+1,c0[ix]+1,c1[ix]+1):
                out.write(f'{f[0]:x} {f[1]:x} {f[2]:x} {a:x} {b:x}\n')
            out.write('))\n')
            cursor += len(ix)
        out.write('(39 (2 fluid fluid 1)())\n')
        for zone,name,bc,label in zones:
            if np.any(labels==label):
                kind = 'velocity-inlet' if bc==10 else 'pressure-outlet' if bc==5 else name
                out.write(f'(39 ({zone} {kind} {name} 1)())\n')
    return report


def generate(gmsh, job, directory):
    def progress(message):
        save(directory/'progress.json',{'message':message})
    progress('正在读取流体实体并重新核对四类边界')
    domain, config = job['domain'],job['config']
    gmsh.option.setString('Geometry.OCCTargetUnit','MM')
    shapes = gmsh.model.occ.importShapes(str(directory/'fluid.brep'))
    gmsh.model.occ.synchronize()
    require(len(shapes)==1 and shapes[0][0]==3, "流体 CAD 已失效。")
    mapping = classify(gmsh,shapes[0],domain['config'],domain['coordinates_mm'])
    require(mapping['boundary_patch_counts']==domain['boundary_patch_counts'], "流体边界来源检查失败。")
    require(np.isclose(gmsh.model.occ.getMass(*shapes[0]),domain['volume_mm3'],rtol=1e-8), "流体 CAD 体积来源检查失败。")
    for i,role in enumerate(ROLES,11):
        gmsh.model.addPhysicalGroup(2,mapping['physical_surfaces'][role],i,role)
    gmsh.model.addPhysicalGroup(3,[shapes[0][1]],1,'fluid')
    for option,value in {'Mesh.MeshSizeMin':config['min_size_mm'],'Mesh.MeshSizeMax':config['max_size_mm'],
            'Mesh.MeshSizeFactor':config.get('size_factor',1.),
            'Mesh.MeshSizeFromCurvature':config.get('curvature_samples',20),'Mesh.MeshSizeExtendFromBoundary':0,'Mesh.ElementOrder':1,
            'Mesh.Algorithm3D':10 if config.get('algorithm','delaunay')=='hxt' else 1,'Mesh.Optimize':1,'Mesh.OptimizeNetgen':0}.items():
        gmsh.option.setNumber(option,value)
    distance = gmsh.model.mesh.field.add('Distance')
    gmsh.model.mesh.field.setNumbers(distance,'FacesList',mapping['physical_surfaces']['fuel_inlet']+mapping['physical_surfaces']['oxidizer_inlet'])
    threshold = gmsh.model.mesh.field.add('Threshold')
    for key,value in {'InField':distance,'SizeMin':config['inlet_size_mm'],'SizeMax':config['max_size_mm'],
            'DistMin':config['inlet_size_mm'],'DistMax':config['refine_distance_mm']}.items():
        gmsh.model.mesh.field.setNumber(threshold,key,value)
    fields = [threshold]
    # The distant chamber may be coarse, but inlet-only refinement does not
    # resolve the narrow passages upstream of the chamber. Cap the full length.
    radius = domain['config']['passage_radius_mm']+.5
    passage_end=passage_refinement_zmax(domain)
    for x,y in domain['coordinates_mm']:
        box = gmsh.model.mesh.field.add('Box')
        for key,value in {'VIn':config.get('passage_size_mm',.5),'VOut':config['max_size_mm'],
                'XMin':x-radius,'XMax':x+radius,'YMin':y-radius,'YMax':y+radius,
                'ZMin':domain['config']['upstream_z_mm']-1,'ZMax':passage_end,
                'Thickness':2}.items():
            gmsh.model.mesh.field.setNumber(box,key,value)
        fields.append(box)
    background = gmsh.model.mesh.field.add('Min')
    gmsh.model.mesh.field.setNumbers(background,'FieldsList',fields)
    gmsh.model.mesh.field.setAsBackgroundMesh(background)
    progress('正在生成一阶四面体体网格并优化单元')
    gmsh.model.mesh.generate(3)
    # Run Netgen exactly once; enabling the automatic pass as well duplicated
    # the expensive optimisation and consumed the bounded mesh time budget.
    gmsh.model.mesh.optimize('Netgen')
    progress('正在检查体单元连通性、边界、体积和导出格式')
    types,tags,groups = gmsh.model.mesh.getElements(3)
    require(len(types)==1 and types[0]==4, "仅支持一阶四面体体网格。")
    require(len(tags[0])<=config.get('cell_budget',1500000), "体网格超过设定的单元预算，请提高预算、调整网格或减少喷嘴。")
    node_tags,values,_ = gmsh.model.mesh.getNodes()
    order = np.argsort(node_tags)
    node_tags, points_mm = node_tags[order],np.asarray(values).reshape(-1,3)[order]
    cells = np.searchsorted(node_tags,np.asarray(groups[0]).reshape(-1,4))
    boundaries = {}
    for role,faces in mapping['physical_surfaces'].items():
        triangles = []
        for face in faces:
            stypes,_,nodes = gmsh.model.mesh.getElements(2,face)
            require(all(t==2 for t in stypes), "边界不是一阶三角形。")
            for group in nodes:
                triangles.extend(np.searchsorted(node_tags,np.asarray(group).reshape(-1,3)))
        boundaries[role] = np.asarray(triangles,dtype=np.int64)
    quality = gmsh.model.mesh.getElementQualities(tags[0],'minSICN')
    require(np.isfinite(quality).all() and min(quality)>0, "Gmsh 体单元质量无效。")
    report = write_fluent(directory/'flow.msh',points_mm*.001,cells,boundaries)
    report['relative_CAD_volume_error'] = abs(report['volume_m3']/(domain['volume_mm3']*1e-9)-1)
    require(report['relative_CAD_volume_error']<.01, "体网格与 CAD 体积差超过 1%。")
    errors = {role:abs(report['boundary_mesh_area_m2'][role]/(domain['boundary_area_mm2'][role]*1e-6)-1) for role in ROLES}
    require(max(errors.values())<.02, "边界网格面积与 CAD 面积差超过 2%，请加密网格。")
    report.update(relative_boundary_area_errors=errors,minimum_gmsh_minSICN=float(min(quality)),
        quality_percentiles=np.percentile(quality,[1,5,50,95]).tolist(),gmsh_version=gmsh.__version__,
        curvature_samples=config.get('curvature_samples',20),algorithm=config.get('algorithm','delaunay'),
        passage_size_mm=config.get('passage_size_mm',.5),
        size_factor=config.get('size_factor',1.),
        passage_refinement_zmax_mm=passage_end,
        units='m',bounds_m=[(points_mm.min(axis=0)*.001).tolist(),(points_mm.max(axis=0)*.001).tolist()],
        volume_mesh_generated=True,fluent_executed=False,scientific_eligible=False,
        boundary_layers='none',mesh_method='tetra',cell_type='tetrahedral',fluent_orthogonal_quality=None)
    np.savez_compressed(directory/'mesh_arrays.npz',points_m=points_mm*.001,tetra=cells,**boundaries)
    gmsh.write(str(directory/'flow.gmsh.msh'))
    # A genuine section of the tetrahedra for the browser, not the CAD skin.
    plane_y=float((points_mm[:,1].min()+points_mm[:,1].max())/2)
    selected = np.flatnonzero((points_mm[cells][:,:,1].min(axis=1)<=plane_y)&(points_mm[cells][:,:,1].max(axis=1)>=plane_y))
    if len(selected)>20000:
        selected = selected[np.linspace(0,len(selected)-1,20000,dtype=int)]
    skin = cells[selected][:,np.array([[1,2,3],[0,3,2],[0,1,3],[0,2,1]])].reshape(-1,3)
    used,inverse = np.unique(skin,return_inverse=True)
    report['preview'] = {'vertices':points_mm[used].round(6).tolist(),'triangles':inverse.reshape(-1,3).tolist(),
        'section':f'tetrahedra intersecting Y={plane_y:g} mm; up to 20000 cells','sampled':len(selected)==20000}
    return report
