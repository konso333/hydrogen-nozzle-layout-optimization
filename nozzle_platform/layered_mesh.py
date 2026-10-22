"""Bounded Fluent Meshing workflow for an axial-wall layered mesh family."""

import math
import os
from pathlib import Path
import re
import subprocess

import numpy as np
from scipy.optimize import brentq
from scipy.spatial import cKDTree

from nozzle_platform.domain_cad import classify,ROLES
from nozzle_platform.mesh_quality import parse_quality,acceptable
from nozzle_platform.poly_mesh import read_native,audit,write_fluent,require
from nozzle_platform.volume_mesh import nozzle_metal_end_zmax
from nozzle_platform.workflows import save,sha256


def controls(config):
    """Systematic tangential/core refinement and a fixed physical layer extent."""
    factor=config.get('size_factor',1.)
    first=config.get('layer_first_height_mm',.025)*factor
    count=math.ceil(config.get('layer_count',5)/factor)
    total=config.get('layer_thickness_mm',.25)
    require(first*count<=total,'壁面层首层尺寸与层数之和超过总厚度，请调整壁面层参数。')
    rate=1. if math.isclose(first*count,total,rel_tol=1e-12) else brentq(
        lambda value:first*sum(value**i for i in range(count))-total,1.,3.)
    require(rate<=1.8,'壁面层增长过快，请减小首层尺寸或增加参考层数。')
    return dict(min_size_mm=config['min_size_mm']*factor,max_size_mm=config['max_size_mm']*factor,
        inlet_size_mm=config['inlet_size_mm']*factor,passage_size_mm=config['passage_size_mm']*factor,
        nozzle_wall_size_mm=max(config['min_size_mm'],config['passage_size_mm']/2)*factor,
        curvature_normal_angle_deg=360/config['curvature_samples']*factor,
        first_height_mm=first,number_of_layers=count,growth_rate=rate,total_thickness_mm=total,
        source_geometry_curvature_samples=64,source_geometry_size_factor=1.,
        layer_scope='axial nozzle wall triangles; |normal_z|/|normal| < 0.05; native truncation allowed at junctions')


def verify_volume_controls(task,settings,local_refinement,*,generated=False):
    """Reject ignored native options; retain any bounded octree size adjustment."""
    actual=task.Arguments.get_state()
    require(actual.get('VolumeFill')=='poly-hexcore','原生体网格填充方式与请求不一致。')
    maximum=actual.get('VolumeFillControls',{}).get('HexMaxSize')
    require(type(maximum) in (float,int) and math.isfinite(maximum),'原生体网格未返回有效的最大尺寸。')
    requested=settings['max_size_mm']
    if generated:
        require(.5*requested<=maximum<=requested*(1+1e-12),
            '原生网格器调整后的最大尺寸超出请求范围，请检查尺寸场。')
    else:
        require(math.isclose(maximum,requested,rel_tol=1e-12),'原生体网格最大尺寸设置未生效。')
    if local_refinement:
        require(actual.get('VolumeMeshPreferences',{}).get('UseSizeField')=='yes',
            '局部加密需要显式启用体积尺寸场；原生设置未生效，已停止。')
    return actual


def configure_volume_task(task,settings,local_refinement):
    arguments=dict(VolumeFill='poly-hexcore',VolumeFillControls=dict(HexMaxSize=settings['max_size_mm']))
    if local_refinement:arguments['VolumeMeshPreferences']=dict(UseSizeField='yes')
    task.Arguments.set_state(arguments)
    return verify_volume_controls(task,settings,local_refinement)


def refinement_regions(job):
    """Fixed physical boxes with the same size factor as the mesh family."""
    domain,config=job['domain'],job['config']
    if not config.get('local_volume_refinement',False):return []
    settings=domain['config'];metal=nozzle_metal_end_zmax(domain)
    end=settings['outlet_z_mm'];radius=settings['passage_radius_mm']
    jet_radius=1.15*radius+.05*(end-metal)
    require(end>metal,'喷嘴下游空间不足，无法设置喷流加密区域。')
    core_end=min(end,metal+min(.1,.02*radius))
    near_end=metal+.15*(end-metal)
    regions=[]
    for index,(x,y) in enumerate(domain['coordinates_mm'],1):
        for kind,half,start,stop,size in [
                ('passage',radius,settings['upstream_z_mm'],core_end,config['passage_size_mm']/3),
                ('near-jet',radius,metal-min(.5,.1*radius),near_end,config['passage_size_mm']/2),
                ('jet',jet_radius,metal-min(.5,.1*radius),end,config['passage_size_mm'])]:
            regions.append(dict(name=f'{kind}-core-{index:03d}',lower_mm=[x-half,y-half,start],
                upper_mm=[x+half,y+half,stop],target_size_mm=max(config['min_size_mm'],size)*config['size_factor']))
    return regions


def wall_groups(points,wall,domain,refine_nozzle=False):
    """Keep layer growth axial; also size nearby nonaxial solid faces."""
    centres=points[wall].mean(axis=1)
    distance=cKDTree(domain['coordinates_mm']).query(centres[:,:2])[0]
    normal=np.cross(points[wall[:,1]]-points[wall[:,0]],points[wall[:,2]]-points[wall[:,0]])
    near=(distance<domain['config']['passage_radius_mm']-.001)&(centres[:,2]<nozzle_metal_end_zmax(domain)+.001)
    axial=near&(abs(normal[:,2])<.05*np.linalg.norm(normal,axis=1))
    groups={'passage_wall':wall[axial]}
    extra=near&~axial if refine_nozzle else np.zeros(len(wall),dtype=bool)
    if extra.any():groups['nozzle_wall']=wall[extra]
    groups['chamber_wall']=wall[~(axial|extra)]
    return groups


def add_refinement_regions(session,workflow,regions):
    """Use the verified 25.2 compound task after fluid regions are updated."""
    if not regions:return []
    objects=list(session.meshing_utilities.get_all_objects())
    require(len(objects)==1,'局部体积加密需要唯一的公共流体对象。')
    workflow.TaskObject['Update Regions'].InsertNextTask(CommandName='CreateLocalRefinementRegions')
    task=workflow.TaskObject['Create Local Refinement Regions'];records=[]
    for region in regions:
        low,high=region['lower_mm'],region['upper_mm']
        box=dict(SizeRelativeLength='Directly specify coordinates',
            Xmin=low[0],Ymin=low[1],Zmin=low[2],Xmax=high[0],Ymax=high[1],Zmax=high[2])
        task.Arguments.set_state(dict(RefinementRegionsName=region['name'],CreationMethod='Box',
            BOIMaxSize=region['target_size_mm'],BOISizeName=region['name'],SelectionType='object',
            ObjectSelectionList=objects,ObjectSelectionSingle=objects,BoundingBoxObject=box))
        task.AddChildAndUpdate(DeferUpdate=False)
        child=workflow.TaskObject[region['name']];effective=child.Arguments.get_state()
        require(child.State.get_state()=='Up-to-date','局部体积加密任务未完成。')
        require(effective.get('RefinementRegionsName')==region['name'] and
            effective.get('BOIMaxSize')==region['target_size_mm'] and
            all(effective.get('BoundingBoxObject',{}).get(k)==v for k,v in box.items()),
            '原生局部体积加密的范围或尺寸与请求不一致。')
        records.append(dict(**region,effective_arguments=effective))
    return records


def create_native_mapping(directory):
    """An owned temporary ASCII working path for native absolute checkpoints."""
    require(os.name=='nt','当前壁面层网格仅验证过 Windows Fluent 25.2。')
    for letter in 'WXYZRSTUV':
        drive=letter+':'
        if Path(drive+'/').exists():continue
        result=subprocess.run(['subst',drive,str(directory)],capture_output=True,check=False,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:continue
        try:save(directory/'native_drive.json',dict(drive=drive,target=str(directory)))
        except BaseException:
            subprocess.run(['subst',drive,'/D'],capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
            raise
        return drive+'\\'
    raise ValueError('找不到空闲的临时盘符，无法提供 Fluent 检查点工作路径。')


def cleanup_native_mapping(directory):
    record=directory/'native_drive.json'
    if not record.exists():return
    import json
    mapping=json.loads(record.read_text(encoding='utf-8'))
    require(re.fullmatch('[R-Z]:',mapping.get('drive','')) is not None,'临时工作路径记录无效。')
    require(Path(mapping['target']).resolve()==directory.resolve(),'临时工作路径不属于本次任务。')
    drive=mapping['drive']
    if not Path(drive+'/').exists():return
    require(os.path.samefile(drive+'/',directory),'临时盘符已指向其他目录，保留该映射并停止清理。')
    subprocess.run(['subst',drive,'/D'],capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)


def inward_skin(points,boundaries,expected_volume):
    """Validate closed oriented triangles, then use Fluent's inward winding."""
    triangles=np.concatenate(list(boundaries.values()))
    edges=triangles[:,[[0,1],[1,2],[2,0]]].reshape(-1,2)
    canonical=np.sort(edges,axis=1)
    order=np.lexsort((canonical[:,1],canonical[:,0]))
    ordered=canonical[order]
    require(len(ordered)%2==0 and np.array_equal(ordered[::2],ordered[1::2]) and
            (len(ordered)<3 or np.all(np.any(ordered[1:-1:2]!=ordered[2::2],axis=1))),
            '固定几何表面不封闭或存在非流形边。')
    require(np.array_equal(edges[order][::2],edges[order][1::2,::-1]),
            '固定几何表面的相邻三角形方向不一致。')
    a,b,c=points[triangles[:,0]],points[triangles[:,1]],points[triangles[:,2]]
    signed_volume=float(np.einsum('ij,ij->i',a,np.cross(b,c)).sum()/6)
    require(np.isclose(abs(signed_volume),expected_volume,rtol=.01),'固定几何表面的方向或体积检查失败。')
    # Imported OCC volume triangles already carry their mesh orientation.
    # Reapplying signed CAD boundary tags would flip some surfaces twice.
    if signed_volume>0:boundaries={role:faces[:,[0,2,1]] for role,faces in boundaries.items()}
    return boundaries,abs(signed_volume)


def write_surface(gmsh,job,directory):
    domain,config=job['domain'],job['config']
    gmsh.option.setString('Geometry.OCCTargetUnit','MM')
    shapes=gmsh.model.occ.importShapes(str(directory/'fluid.brep'));gmsh.model.occ.synchronize()
    require(len(shapes)==1 and shapes[0][0]==3,'流体 CAD 已失效。')
    mapping=classify(gmsh,shapes[0],domain['config'],domain['coordinates_mm'])
    require(mapping['boundary_patch_counts']==domain['boundary_patch_counts'],'流体边界来源检查失败。')
    require(np.isclose(gmsh.model.occ.getMass(*shapes[0]),domain['volume_mm3'],rtol=1e-8),'流体 CAD 体积来源检查失败。')
    # A fixed, finer faceting of the same geometry is shared by all levels.
    # Solver surface/core sizes are applied separately by the native mesher.
    for option,value in {'Mesh.MeshSizeMin':min(config['min_size_mm'],.04),
            'Mesh.MeshSizeMax':min(config['max_size_mm'],1.),'Mesh.MeshSizeFactor':1.,
            'Mesh.MeshSizeFromCurvature':64,'Mesh.MeshSizeExtendFromBoundary':0,'Mesh.ElementOrder':1,'Mesh.RandomSeed':1}.items():
        gmsh.option.setNumber(option,value)
    distance=gmsh.model.mesh.field.add('Distance')
    gmsh.model.mesh.field.setNumbers(distance,'FacesList',mapping['physical_surfaces']['fuel_inlet']+mapping['physical_surfaces']['oxidizer_inlet'])
    threshold=gmsh.model.mesh.field.add('Threshold')
    for key,value in {'InField':distance,'SizeMin':config['inlet_size_mm'],'SizeMax':min(config['max_size_mm'],1.),
            'DistMin':config['inlet_size_mm'],'DistMax':config['refine_distance_mm']}.items():
        gmsh.model.mesh.field.setNumber(threshold,key,value)
    gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
    gmsh.model.mesh.generate(2)
    tags,values,_=gmsh.model.mesh.getNodes();order=np.argsort(tags)
    tags=tags[order];points=np.asarray(values).reshape(-1,3)[order]
    boundaries={role:[] for role in ROLES}
    for role,surfaces in mapping['physical_surfaces'].items():
        for surface in surfaces:
            types,_,groups=gmsh.model.mesh.getElements(2,surface)
            require(all(t==2 for t in types),'几何表面需要一阶三角形。')
            for group in groups:
                triangles=np.searchsorted(tags,np.asarray(group).reshape(-1,3))
                boundaries[role].append(triangles)
    boundaries={role:np.concatenate(groups) for role,groups in boundaries.items()}
    face_count=sum(len(group) for group in boundaries.values())
    require(face_count<=3000000,'固定几何表面超过 300 万三角形预算，请减少喷嘴数量。')
    wall=boundaries.pop('wall')
    boundaries.update(wall_groups(points,wall,domain,config.get('local_volume_refinement',False)))
    require(all(len(faces)>0 for faces in boundaries.values()),'未能识别完整的入口、出口、轴向喷嘴壁面及公共腔壁面。')
    boundaries,volume=inward_skin(points,boundaries,domain['volume_mm3'])
    with (directory/'surface.msh').open('w',encoding='ascii',newline='\n') as out:
        out.write(f'(0 "Closed fluid skin; millimetres")\n(2 3)\n(10 (0 1 {len(points):x} 1 3))\n(13 (0 1 {face_count:x} 0))\n')
        out.write(f'(10 (1 1 {len(points):x} 1 3)(\n');np.savetxt(out,points,fmt='%.17e');out.write('))\n');cursor=1
        for zone,(role,faces) in enumerate(boundaries.items(),3):
            bc=10 if 'inlet' in role else 5 if role=='outlet' else 3
            kind='velocity-inlet' if bc==10 else 'pressure-outlet' if bc==5 else 'wall'
            out.write(f'(13 ({zone:x} {cursor:x} {cursor+len(faces)-1:x} {bc:x} 3)(\n')
            for row in faces+1:out.write(' '.join(f'{int(v):x}' for v in row)+' 0 0\n')
            out.write(f'))\n(39 ({zone} {kind} {role} 1)())\n');cursor+=len(faces)
    return dict(source_geometry_triangles=face_count,source_geometry_volume_mm3=volume,
        axial_layer_wall_triangles=len(boundaries['passage_wall']),
        local_nozzle_wall_triangles=len(boundaries.get('nozzle_wall',[])),source_geometry_curvature_samples=64)


def generate(gmsh,job,directory):
    from coldflow_trial.processes import OwnedProcesses,cleanup_records
    import ansys.fluent.core as pyfluent
    config=job['config'];settings=controls(config);regions=refinement_regions(job)
    progress=lambda message:save(directory/'progress.json',dict(message=message))
    progress('正在生成固定几何表面并识别轴向喷嘴壁面')
    surface_report=write_surface(gmsh,job,directory)
    surface_report['source_geometry_sha256']=sha256(directory/'surface.msh')
    gmsh.clear()
    save(directory/'mesh_controls.json',settings|surface_report)
    save(directory/'volume_refinement.json',[])
    tracker=OwnedProcesses(job['fluent']['executable'],directory);session=None
    native_path=create_native_mapping(directory)
    def execute(task):
        task.Execute()
        require(task.State.get_state()=='Up-to-date','Fluent Meshing 任务未完成：'+str(task.Errors.get_state()))
    try:
        fluent=job['fluent'];pyfluent.config.fluent_server_info_dir=str(directory)
        pyfluent.config.launch_fluent_ip='127.0.0.1'
        appdata=os.environ.get('APPDATA');os.environ['APPDATA']=str(directory/'controller-cache')
        try:
            session=pyfluent.launch_fluent(fluent_path=fluent['executable'],product_version=fluent['product_version'],
                dimension=3,precision='double',processor_count=fluent['processes'],mode='meshing',ui_mode='no_gui',
                cwd=native_path,start_timeout=fluent['start_timeout_seconds'],cleanup_on_exit=True,start_watchdog=False,
                start_transcript=False,py=False,additional_arguments='-license='+fluent['license_product'],
                env=dict(({'ANSYSLMD_LICENSE_FILE':fluent['license_server']} if fluent.get('license_server') else {}),
                         **({'APPDATA':appdata} if appdata else {})))
        finally:
            if appdata is None:os.environ.pop('APPDATA',None)
            else:os.environ['APPDATA']=appdata
        tracker.capture(session);session.tui.file.start_transcript('meshing.trn')
        workflow=session.workflow;workflow.InitializeWorkflow(WorkflowType='Watertight Geometry')
        task=workflow.TaskObject['Import Geometry']
        task.Arguments.set_state(dict(FileFormat='Mesh',MeshFileName='surface.msh',MeshUnit='mm',LengthUnit='mm'));execute(task)
        progress('正在设置入口和喷嘴流道尺寸并改善表面质量')
        local=workflow.TaskObject['Add Local Sizing']
        local_rules=[('ports',['fuel_inlet','oxidizer_inlet'],settings['inlet_size_mm']),
                     ('passages',['passage_wall'],settings['passage_size_mm'])]
        if surface_report['local_nozzle_wall_triangles']:
            local_rules.append(('nozzle_faces',['nozzle_wall'],settings['nozzle_wall_size_mm']))
        for name,zones,size in local_rules:
            local.Arguments.set_state(dict(AddChild='yes',BOIControlName=name,BOIExecution='Face Size',
                BOIZoneorLabel='zone',BOIFaceZoneList=zones,BOISize=size,BOIGrowthRate=1.2))
            local.AddChildAndUpdate(DeferUpdate=False)
            effective=workflow.TaskObject[name].Arguments.get_state()
            require(set(effective.get('BOIFaceZoneList',[]))==set(zones),'局部网格尺寸作用范围不正确。')
        surface=workflow.TaskObject['Generate the Surface Mesh']
        surface.Arguments.set_state({'CFDSurfaceMeshControls':dict(RemeshImportedMesh='All',
            MinSize=settings['min_size_mm'],MaxSize=settings['max_size_mm'],
            CurvatureNormalAngle=settings['curvature_normal_angle_deg'],GrowthRate=1.2)})
        execute(surface);surface.InsertNextTask(CommandName='ImproveSurfaceMesh')
        task=workflow.TaskObject['Improve Surface Mesh']
        task.Arguments.set_state({'FaceQualityLimit':.7,'SMImprovePreferences':{'AllowDefeaturing':'no'}});execute(task)
        task=workflow.TaskObject['Describe Geometry']
        task.Arguments.set_state({'SetupType':'The geometry consists of only fluid regions with no voids'})
        task.UpdateChildTasks(SetupTypeChanged=False);execute(task)
        execute(workflow.TaskObject['Update Boundaries']);execute(workflow.TaskObject['Update Regions'])
        volume=workflow.TaskObject['Generate the Volume Mesh']
        configure_volume_task(volume,settings,bool(regions))
        if regions:progress('正在设置喷嘴内部和下游喷流的局部体积加密')
        save(directory/'volume_refinement.json',add_refinement_regions(session,workflow,regions))
        layers=workflow.TaskObject['Add Boundary Layers']
        layers.Arguments.set_state(dict(AddChild='yes',BLControlName='axial_layers',OffsetMethodType='uniform',
            FirstHeight=settings['first_height_mm'],NumberOfLayers=settings['number_of_layers'],Rate=settings['growth_rate'],
            FaceScope={'GrowOn':'selected-zones'},ZoneSelectionList=['passage_wall']))
        layers.AddChildAndUpdate(DeferUpdate=False)
        effective=workflow.TaskObject['axial_layers'].Arguments.get_state()
        require(effective.get('BLZoneList')==['passage_wall'],'实际壁面层作用范围与喷嘴轴向壁面不一致。')
        progress('正在生成壁面层与多面体／六面体核心网格')
        requested_volume_controls=configure_volume_task(volume,settings,bool(regions))
        save(directory/'volume_controls.json',dict(requested=requested_volume_controls))
        execute(volume);tracker.capture(session)
        effective_volume_controls=verify_volume_controls(volume,settings,bool(regions),generated=True)
        native_volume_controls=dict(requested=requested_volume_controls,effective=effective_volume_controls)
        save(directory/'volume_controls.json',native_volume_controls)
        volume.InsertNextTask(CommandName='ImproveVolumeMesh');task=workflow.TaskObject['Improve Volume Mesh']
        task.Arguments.set_state({'CellQualityLimit':.15,'VMImprovePreferences':{'VIgnoreFeature':'no'}});execute(task)
        save(directory/'native_workflow.json',workflow.get_state())
        solver=session.switch_to_solver();session=solver;tracker.capture(session)
        solver.settings.mesh.check();solver.settings.mesh.quality()
        quality=parse_quality((directory/'meshing.trn').read_text(encoding='utf-8',errors='replace'))
        save(directory/'native_quality.json',dict(**quality,mesh_quality_accepted=acceptable(quality)))
        save(directory/'mesh_quality.json',dict(mesh_quality_accepted=acceptable(quality),
            minimum_fluent_orthogonal_quality=quality['minimum_orthogonal_quality'],maximum_fluent_aspect_ratio=quality['maximum_aspect_ratio'],
            mesh_quality_limits=dict(minimum_orthogonal_quality=.1,maximum_aspect_ratio=100.)))
        require(acceptable(quality),'壁面层体网格未通过最低正交质量 0.1／最大长宽比 100 检查，已停止。')
        solver.settings.file.write_case(file_name='native_mesh.cas.h5')
    finally:
        try:
            tracker.capture(session)
            if session is not None:session.exit(timeout=10,wait=10)
        finally:
            cleanup=cleanup_records(list(tracker.owned.values()));save(directory/'cleanup.json',cleanup)
            if cleanup['all_tracked_processes_exited']:cleanup_native_mapping(directory)
    require(cleanup['all_tracked_processes_exited'],'壁面层网格进程退出尚未核实。')
    progress('正在独立检查体单元连通、面闭合、几何和开口数量')
    arrays=read_native(directory/'native_mesh.cas.h5')
    require(len(arrays['cell_types'])<=config['cell_budget'],'原生体网格超过设定的单元预算，请调整网格或减少喷嘴数量。')
    report=audit(arrays,job['domain'],regions=regions)
    save(directory/'geometry_quality.json',{k:v for k,v in report.items() if k!='preview'})
    progress('正在导出保持原生拓扑的 SI 网格')
    write_fluent(directory/'flow.msh',arrays);np.savez_compressed(directory/'mesh_arrays.npz',**arrays)
    report.update(**surface_report,layer_controls=settings,native_volume_controls=native_volume_controls,
        minimum_fluent_orthogonal_quality=quality['minimum_orthogonal_quality'],maximum_fluent_aspect_ratio=quality['maximum_aspect_ratio'],
        volume_mesh_generated=True,fluent_executed=True,fluent_meshing_executed=True,solver_executed=False,scientific_eligible=False,
        boundary_layers='axial-wall layers; requested count '+str(settings['number_of_layers'])+'; native truncation allowed at junctions',
        mesh_method='layered-polyhex',size_factor=config['size_factor'],
        local_volume_refinement=bool(regions),volume_refinement_regions=regions,
        local_volume_refinement_strategy='nozzle-core-near-jet-downstream-v2' if regions else 'none')
    return report
