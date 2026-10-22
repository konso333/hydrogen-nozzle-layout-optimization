"""Mixed-cell topology, independent geometry checks and controlled wall layers."""
import json
import re
import copy
import time
import zipfile
import threading

import numpy as np
import pytest

from nozzle_platform.layered_mesh import (controls,cleanup_native_mapping,inward_skin,refinement_regions,
    wall_groups,configure_volume_task,verify_volume_controls)
from nozzle_platform.poly_mesh import read_native,audit,write_fluent
from nozzle_platform.workflows import normalize_mesh,normalize_flow
from nozzle_platform.workflows import WorkflowStore
from nozzle_platform.mesh_study import layered_mesh_preflight,normalize as normalize_study,run as run_study
from nozzle_platform.mesh_quality import condition_mesh
from tests.test_platform_workflows import mesh_request,flow_request
from tests.test_platform_mesh_quality import FakeSession
from tests.test_platform_domains import stores,config as domain_config
from tests.test_platform_mesh_study import request as study_request
from tests.test_platform_grid_convergence import uniform_rows


@pytest.mark.parametrize('reverse',[False,True])
def test_surface_winding_points_into_fluid_without_reapplying_cad_signs(reverse):
    points=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
    faces=np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3]])
    if reverse:faces=faces[:,::-1]
    original=faces.copy()
    groups,volume=inward_skin(points,{'ports':faces[:1],'wall':faces[1:]},1/6)
    assert volume==pytest.approx(1/6)
    np.testing.assert_array_equal(faces,original)
    triangles=np.concatenate(list(groups.values()))
    a,b,c=(points[triangles[:,i]] for i in range(3))
    assert np.all(np.einsum('ij,ij->i',np.cross(b-a,c-a),points.mean(axis=0)-(a+b+c)/3)>0)


@pytest.mark.parametrize('problem',['open','nonmanifold','local_flip','wrong_volume'])
def test_surface_winding_rejects_invalid_closed_skin(problem):
    points=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
    faces=np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3]])
    if problem=='open':faces=faces[:-1]
    if problem=='nonmanifold':faces=np.tile(faces,(2,1))
    if problem=='local_flip':faces[0]=faces[0,::-1]
    with pytest.raises(ValueError):inward_skin(points,{'all':faces},2 if problem=='wrong_volume' else 1/6)


def two_cubes(tmp_path,nozzle_wall=False):
    import h5py
    points=np.array([(x,y,z) for x in range(3) for y in range(2) for z in range(2)],float)*.001
    index=lambda x,y,z:4*x+2*y+z
    faces=[];owner=[]
    def add(xyz,cell):faces.append([index(*v) for v in xyz][::-1]);owner.append(cell)
    add([(1,0,0),(1,1,0),(1,1,1),(1,0,1)],0)  # inward to c0; internal
    add([(0,0,0),(0,0,1),(0,1,1),(0,1,0)],0)
    add([(2,0,0),(2,1,0),(2,1,1),(2,0,1)],1)
    for x in range(2):
        add([(x,0,0),(x+1,0,0),(x+1,0,1),(x,0,1)],x)
        add([(x,1,0),(x,1,1),(x+1,1,1),(x+1,1,0)],x)
        add([(x,0,0),(x,1,0),(x+1,1,0),(x+1,0,0)],x)
        add([(x,0,1),(x+1,0,1),(x+1,1,1),(x,1,1)],x)
    path=tmp_path/'native.cas.h5'
    with h5py.File(path,'w') as f:
        m=f.create_group('meshes/1')
        ds=m.create_dataset('nodes/coords/99',data=points);ds.attrs['minId']=[1]
        m['faces/nodes/1/nnodes']=np.full(11,4)
        m['faces/nodes/1/nodes']=np.array(faces).flatten()+1
        m['faces/c0/1']=np.array(owner)+1
        ds=m.create_dataset('faces/c1/1',data=[2]);ds.attrs['minId']=[1]
        m['cells/ctype/1/cell-types']=[3,7]
        t=m.create_group('faces/zoneTopology')
        if nozzle_wall:
            t['name']=np.array([b'interior--fluid;fuel_inlet;outlet;oxidizer_inlet;chamber_wall;nozzle_wall;passage_wall'])
            t['minId']=[1,2,3,4,5,7,8];t['maxId']=[1,2,3,4,6,7,11]
        else:
            t['name']=np.array([b'interior--fluid;fuel_inlet;outlet;oxidizer_inlet;chamber_wall;passage_wall'])
            t['minId']=[1,2,3,4,5,8];t['maxId']=[1,2,3,4,7,11]
    domain=dict(volume_mm3=2.,boundary_area_mm2=dict(fuel_inlet=1.,oxidizer_inlet=1.,outlet=1.,wall=7.),
                boundary_patch_counts=dict(fuel_inlet=1,oxidizer_inlet=1,outlet=1))
    return path,domain


@pytest.mark.parametrize('extra_zone',[False,True])
def test_native_mixed_cells_keep_exact_volume_closure_and_openings(tmp_path,extra_zone):
    path,domain=two_cubes(tmp_path,nozzle_wall=extra_zone);arrays=read_native(path)
    r=audit(arrays,domain)
    assert r['cells']==2 and r['connected_fluid_components']==1
    assert r['volume_m3']==pytest.approx(2e-9)
    assert r['minimum_signed_volume_m3']==pytest.approx(1e-9)
    assert r['max_cell_area_closure_error_m2']<1e-20
    assert r['opening_counts']==domain['boundary_patch_counts']
    assert r['relative_CAD_volume_error']<1e-12
    assert r['cell_type_counts']=={'3':1,'7':1}
    assert r['preview']['triangles']


def test_native_preview_intersects_a_fluid_domain_entirely_above_y_zero(tmp_path):
    path,domain=two_cubes(tmp_path);arrays=read_native(path)
    arrays['points_m']+=np.array([.01,.02,0])
    report=audit(arrays,domain)
    assert report['preview']['triangles']
    assert min(v[1] for v in report['preview']['vertices'])>=20
    assert report['volume_m3']==pytest.approx(2e-9)


def test_local_refinement_is_checked_against_actual_cells_and_volume(tmp_path):
    path,domain=two_cubes(tmp_path);arrays=read_native(path)
    region=dict(name='first-cube',lower_mm=[0,0,0],upper_mm=[1,1,1],target_size_mm=.6)
    summary=audit(arrays,domain,regions=[region])['volume_refinement_summary'][0]
    assert summary['cells']==1 and summary['fluid_volume_m3']==pytest.approx(1e-9)
    assert summary['volume_weighted_equivalent_h_mm']==pytest.approx(1.)
    with pytest.raises(ValueError,match='目标尺寸'):audit(arrays,domain,regions=[dict(region,target_size_mm=.4)])
    with pytest.raises(ValueError,match='没有真实体单元'):
        audit(arrays,domain,regions=[dict(region,lower_mm=[10,10,10],upper_mm=[11,11,11])])


@pytest.mark.parametrize('passage_size',[.5,.04])
def test_refinement_boxes_follow_each_nozzle_and_preserve_physical_extent(passage_size):
    domain=dict(config=dict(upstream_z_mm=-28,outlet_z_mm=58.5,chamber_start_z_mm=-1.5,passage_radius_mm=7),
        coordinates_mm=[[10,20],[-10,-20]],faces=[dict(role='wall',bounds_mm=[0,0,-28,1,1,4]),
                                               dict(role='wall',bounds_mm=[-42,-42,-1.5,42,42,58.5])])
    request=mesh_request(mesh_method='layered-polyhex',local_volume_refinement=True,passage_size_mm=passage_size)
    request.update(min_size_mm=.04,inlet_size_mm=min(.2,passage_size))
    config=normalize_mesh(request)
    coarse=refinement_regions(dict(domain=domain,config=dict(config,size_factor=.9)))
    fine=refinement_regions(dict(domain=domain,config=dict(config,size_factor=.6)))
    assert len(coarse)==len(fine)==6
    assert coarse[0]['lower_mm']==[3,13,-28] and coarse[0]['upper_mm']==[17,27,4.1]
    assert coarse[1]['lower_mm']==[3,13,3.5]
    assert coarse[1]['upper_mm']==pytest.approx([17,27,12.175])
    assert coarse[2]['lower_mm'][2]==3.5 and coarse[2]['upper_mm'][2]==58.5
    assert coarse[3]['lower_mm']==[-17,-27,-28]
    assert [{k:v for k,v in r.items() if k!='target_size_mm'} for r in coarse]==[{k:v for k,v in r.items() if k!='target_size_mm'} for r in fine]
    assert coarse[0]['target_size_mm']/fine[0]['target_size_mm']==pytest.approx(1.5)
    assert [r['target_size_mm'] for r in fine[:3]]==pytest.approx([max(config['min_size_mm'],passage_size/f)*.6 for f in [3,2,1]])
    assert refinement_regions(dict(domain=domain,config=dict(config,local_volume_refinement=False)))==[]


@pytest.mark.parametrize('reverse',[False,True])
def test_wall_sizing_includes_bluff_faces_without_growing_layers_there(reverse):
    points=np.array([[1,0,1],[1,1,1],[1,0,2],[-1,-1,4],[1,-1,4],[0,1,4],
                     [10,0,1],[10,1,1],[10,0,2],[1,0,6],[1,1,6],[1,0,7],
                     [19,-1,4],[21,-1,4],[20,1,4]],float)
    wall=np.arange(15).reshape(-1,3)
    if reverse:wall=wall[:,::-1]
    domain=dict(config=dict(chamber_start_z_mm=0,outlet_z_mm=10,passage_radius_mm=3),
        coordinates_mm=[[0,0],[20,0]],faces=[dict(role='wall',bounds_mm=[-3,-3,0,23,3,4]),
                                         dict(role='wall',bounds_mm=[-42,-42,0,42,42,10])])
    groups=wall_groups(points,wall,domain,True)
    np.testing.assert_array_equal(groups['passage_wall'],wall[[0]])
    np.testing.assert_array_equal(groups['nozzle_wall'],wall[[1,4]])
    np.testing.assert_array_equal(groups['chamber_wall'],wall[[2,3]])
    assert sum(len(faces) for faces in groups.values())==len(wall)
    original=wall_groups(points,wall,domain,False)
    assert 'nozzle_wall' not in original
    np.testing.assert_array_equal(original['passage_wall'],wall[[0]])
    np.testing.assert_array_equal(original['chamber_wall'],wall[[1,2,3,4]])


@pytest.mark.parametrize('value',[1,'true',None])
def test_volume_refinement_option_requires_a_native_boolean(value):
    with pytest.raises(ValueError):normalize_mesh(mesh_request(mesh_method='layered-polyhex',local_volume_refinement=value))
    with pytest.raises(ValueError):normalize_mesh(mesh_request(local_volume_refinement=True))


def test_poly_export_preserves_every_face_owner_coordinate_and_cell_type(tmp_path):
    path,_=two_cubes(tmp_path);arrays=read_native(path);target=tmp_path/'flow.msh'
    write_fluent(target,arrays);text=target.read_text(encoding='ascii')
    assert '(10 (1 1 c 1 3)' in text
    coordinates=re.search(r'\(10 \(1 1 c 1 3\)\(\n(.*?)\)\)',text,re.S)[1]
    np.testing.assert_allclose(np.array([[float(v) for v in row.split()] for row in coordinates.splitlines()]),arrays['points_m'],rtol=0,atol=0)
    assert '(12 (2 1 2 1 0)(\n3 7\n))' in text
    actual=[]
    for block in re.findall(r'\(13 \([^)]+\)\(\n(.*?)\)\)',text,re.S):
        for line in block.splitlines():
            values=[int(v,16) for v in line.split()]
            assert values[0]==4
            actual.append(tuple(values[1:]))
    expected=[]
    for i in range(len(arrays['face_counts'])):
        polygon=arrays['face_nodes'][arrays['face_offsets'][i]:arrays['face_offsets'][i+1]]+1
        expected.append(tuple(polygon)+(int(arrays['c0'][i]+1),int(arrays['c1'][i]+1)))
    assert sorted(actual)==sorted(expected)


@pytest.mark.parametrize('kind',['unknown-boundary','overlap','first-node','invalid-owner','invalid-node','interior-owner'])
def test_malformed_native_case_is_rejected(tmp_path,kind):
    import h5py
    path,_=two_cubes(tmp_path)
    with h5py.File(path,'r+') as f:
        m=f['meshes/1']
        if kind=='unknown-boundary':m['faces/zoneTopology/name'][0]=b'other;fuel_inlet;outlet;oxidizer_inlet;chamber_wall;passage_wall'
        elif kind=='overlap':m['faces/zoneTopology/minId'][1]=1
        elif kind=='first-node':m['nodes/coords/99'].attrs['minId']=[2]
        elif kind=='invalid-owner':m['faces/c0/1'][0]=3
        elif kind=='invalid-node':m['faces/nodes/1/nodes'][0]=200
        elif kind=='interior-owner':m['faces/c1/1'][0]=0
    with pytest.raises(ValueError):read_native(path)


@pytest.mark.parametrize('kind',['closure','zero-volume','disconnected','wrong-area','wrong-opening'])
def test_native_independent_checks_reject_unusable_topology(tmp_path,kind):
    path,domain=two_cubes(tmp_path);a=read_native(path)
    if kind=='closure':a['face_nodes'][4:8]=a['face_nodes'][4:8][::-1]
    elif kind=='zero-volume':a['points_m'][:,2]=0
    elif kind=='disconnected':a['c1'][0]=-1;a['face_labels'][0]=4
    elif kind=='wrong-area':domain['boundary_area_mm2']['fuel_inlet']=2.
    elif kind=='wrong-opening':domain['boundary_patch_counts']['fuel_inlet']=2
    with pytest.raises(ValueError):audit(a,domain)


def test_wall_family_refines_first_height_layers_and_core_keeps_physical_thickness():
    request=mesh_request(mesh_method='layered-polyhex',size_factor=.64)
    config=normalize_mesh(request);family=[controls(dict(config,size_factor=factor)) for factor in [1.,.8,.64]]
    assert [v['number_of_layers'] for v in family]==[5,7,8]
    assert [v['first_height_mm'] for v in family]==pytest.approx([.025,.020,.016])
    assert [v['max_size_mm'] for v in family]==pytest.approx([4.,3.2,2.56])
    for v in family:
        thickness=v['first_height_mm']*sum(v['growth_rate']**i for i in range(v['number_of_layers']))
        assert thickness==pytest.approx(.25)
        assert v['source_geometry_curvature_samples']==64


@pytest.mark.parametrize('changes',[{'mesh_method':{}},{'mesh_method':'command'},
    {'layer_count':True},{'layer_count':13},{'layer_first_height_mm':float('nan')},
    {'layer_thickness_mm':.04},{'layer_first_height_mm':.1,'layer_count':12},
    {'mesh_method':'tetra','layer_count':5}])
def test_invalid_layer_parameters_fail_before_native_launch(changes):
    request=mesh_request(mesh_method='layered-polyhex');request.update(changes)
    with pytest.raises(ValueError):normalize_mesh(request)


def test_accepted_layers_are_not_converted_or_repaired(tmp_path):
    session=FakeSession(tmp_path,[(.2,60)])
    r=condition_mesh(session,tmp_path,lambda _:None,preserve_layers=True)
    assert r['mesh_quality_history'][0]['stage']=='imported_mixed'
    assert not session.calls and not r['polyhedral_conversion_performed']
    assert 'imported_tetra_minimum_orthogonal_quality' not in r


def test_rejected_layers_cannot_be_hidden_by_whole_domain_conversion(tmp_path):
    session=FakeSession(tmp_path,[(.09,60)])
    with pytest.raises(ValueError,match='壁面层网格导入'):
        condition_mesh(session,tmp_path,lambda _:None,preserve_layers=True)
    assert not session.calls
    assert not json.loads((tmp_path/'mesh_quality.json').read_text())['mesh_quality_accepted']


@pytest.mark.parametrize('drive,target',[('C:','same'),('W:','outside')])
def test_mapping_cleanup_rejects_unowned_targets(tmp_path,drive,target):
    (tmp_path/'native_drive.json').write_text(json.dumps(dict(drive=drive,target=str(tmp_path if target=='same' else tmp_path.parent))))
    with pytest.raises(ValueError):cleanup_native_mapping(tmp_path)


@pytest.mark.parametrize('budget',[True,1799,7201,1.5])
def test_strict_time_budget_is_bounded(budget):
    with pytest.raises(ValueError):normalize_flow(flow_request(convergence_mode='grid-verification',time_budget_seconds=budget))


def test_only_strict_verification_can_use_extended_time_budget():
    assert normalize_flow(flow_request())['time_budget_seconds']==1800
    assert normalize_flow(flow_request(convergence_mode='grid-verification',time_budget_seconds=3600))['time_budget_seconds']==3600
    assert normalize_flow(flow_request(convergence_mode='grid-verification',time_budget_seconds=7200))['time_budget_seconds']==7200
    with pytest.raises(ValueError):normalize_flow(flow_request(time_budget_seconds=3600))
    with pytest.raises(ValueError):normalize_flow(flow_request(time_budget_seconds=7200))


def test_extended_iteration_budget_is_only_available_to_verification():
    assert normalize_flow(flow_request(convergence_mode='grid-verification',iterations=2000))['iterations']==2000
    with pytest.raises(ValueError):normalize_flow(flow_request(convergence_mode='grid-verification',iterations=2001))
    with pytest.raises(ValueError):normalize_flow(flow_request(iterations=501))


class NativeVolumeTask:
    def __init__(self,ignore=None):
        self.Arguments=self;self.state={};self.ignore=ignore
    def set_state(self,arguments):
        self.state=copy.deepcopy(arguments)
        if self.ignore=='size-field':self.state.pop('VolumeMeshPreferences',None)
        if self.ignore=='maximum':self.state['VolumeFillControls']['HexMaxSize']=4.
    def get_state(self):return copy.deepcopy(self.state)


@pytest.mark.parametrize('ignored',['size-field','maximum'])
def test_native_silently_ignored_size_controls_cannot_reach_meshing(ignored):
    with pytest.raises(ValueError):configure_volume_task(NativeVolumeTask(ignored),dict(max_size_mm=2.5),True)


def test_local_volume_size_field_is_explicit_and_native_adjustment_is_recorded_separately():
    task=NativeVolumeTask();settings=dict(max_size_mm=2.5)
    requested=configure_volume_task(task,settings,True)
    task.state['VolumeFillControls']['HexMaxSize']=2.4
    effective=verify_volume_controls(task,settings,True,generated=True)
    assert requested['VolumeMeshPreferences']['UseSizeField']=='yes'
    assert requested['VolumeFillControls']['HexMaxSize']==2.5
    assert effective['VolumeFillControls']['HexMaxSize']==2.4
    task.state['VolumeMeshPreferences']['UseSizeField']='no'
    with pytest.raises(ValueError):verify_volume_controls(task,settings,True,generated=True)


@pytest.mark.parametrize('maximum',[None,True,float('nan'),2.6,1.2])
def test_invalid_or_excessive_native_size_adjustment_is_rejected(maximum):
    task=NativeVolumeTask();settings=dict(max_size_mm=2.5)
    configure_volume_task(task,settings,True);task.state['VolumeFillControls']['HexMaxSize']=maximum
    with pytest.raises(ValueError):verify_volume_controls(task,settings,True,generated=True)


def test_nonlocal_mesh_keeps_native_default_size_field_preference():
    task=NativeVolumeTask()
    assert 'VolumeMeshPreferences' not in configure_volume_task(task,dict(max_size_mm=2.5),False)


def native_family():
    return [dict(cells=1000*2**i,volume_m3=1.,mesh_method='layered-polyhex',cell_type='mixed-polyhex',
        minimum_fluent_orthogonal_quality=.2,maximum_fluent_aspect_ratio=60.,
        implementation_sha256={'source':'same'},domain_artifact_sha256={'domain':'same'},source_geometry_sha256='same') for i in range(3)]


def test_native_family_preflight_requires_measured_separation_before_solving():
    r=layered_mesh_preflight(native_family())
    assert r['passed'] and r['effective_refinement_ratios']==pytest.approx([2**(1/3)]*2)
    value=native_family();value[1]['cells']=value[0]['cells']+1
    assert not layered_mesh_preflight(value)['passed']


def test_native_preflight_checks_physical_boxes_and_measured_local_resolution():
    family=native_family()
    for i,mesh in enumerate(family):
        target=.5/(i+1)
        mesh.update(local_volume_refinement=True,
            volume_refinement_regions=[dict(name='jet',lower_mm=[-1,-1,0],upper_mm=[1,1,10],target_size_mm=target)],
            volume_refinement_summary=[dict(name='jet',cells=100,volume_weighted_equivalent_h_mm=target)])
    assert layered_mesh_preflight(family)['passed']
    wrong=copy.deepcopy(family);wrong[1]['volume_refinement_regions'][0]['upper_mm'][2]=11
    assert not layered_mesh_preflight(wrong)['passed']
    wrong=copy.deepcopy(family);wrong[1]['volume_refinement_summary'][0]['volume_weighted_equivalent_h_mm']=1.
    assert not layered_mesh_preflight(wrong)['passed']
    wrong=copy.deepcopy(family);wrong[1]['volume_refinement_summary']=[]
    assert not layered_mesh_preflight(wrong)['passed']


@pytest.mark.parametrize('problem',['missing','geometry','implementation','volume','cells','nan-quality','nan-aspect','bad-quality','bad-aspect','method'])
def test_invalid_native_families_cannot_trigger_solvers(problem):
    meshes=native_family()
    if problem=='missing':meshes[0]=None
    elif problem=='geometry':meshes[0]['source_geometry_sha256']='different'
    elif problem=='implementation':meshes[0]['implementation_sha256']['source']='different'
    elif problem=='volume':meshes[0]['volume_m3']=float('nan')
    elif problem=='cells':meshes[0]['cells']=True
    elif problem=='nan-quality':meshes[0]['minimum_fluent_orthogonal_quality']=float('nan')
    elif problem=='nan-aspect':meshes[0]['maximum_fluent_aspect_ratio']=float('nan')
    elif problem=='bad-quality':meshes[0]['minimum_fluent_orthogonal_quality']=.09
    elif problem=='bad-aspect':meshes[0]['maximum_fluent_aspect_ratio']=101
    elif problem=='method':meshes[0]['mesh_method']='tetra'
    assert not layered_mesh_preflight(meshes)['passed']


@pytest.mark.parametrize('separated',[True,False])
def test_study_generates_all_native_meshes_before_any_solver_and_skips_bad_family(stores,tmp_path,monkeypatch,separated):
    _,_,scheme,domains=stores;domain=domains.create(domain_config(scheme))
    store=WorkflowStore(tmp_path/'jobs',domains);store.reservation='study';store.jobs['study']={}
    request=study_request(refinement_mode='uniform',refinement_ratio=1.25)
    request.update(domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'])
    request['mesh'].update(mesh_method='layered-polyhex',size_factor=.64)
    config=normalize_study(request);family=native_family();evidence=uniform_rows()
    if not separated:family[1]['cells']=family[0]['cells']+1
    calls=[];results={}
    def start(kind,parameters,current,source,mesh=None,**kwargs):
        calls.append(kind)
        if kind=='mesh':
            index=len(results);result=dict(family[index],result_id=f'mesh{index}',elapsed_seconds=.1)
            results[result['result_id']]=result
        else:
            index=int(mesh['result_id'][-1]);row=evidence[index]
            point=dict(area_weighted_total_pressure_drop_Pa=row['pressure_drop_Pa'],relative_mass_imbalance=1e-8,h2_relative_convective_imbalance=1e-8)
            result=dict(result_id=f'flow{index}',history=[copy.deepcopy(point) for _ in range(3)],
                solver_cell_count=mesh['cells'],solver_volume_m3=mesh['volume_m3'],solver_cell_type='mixed-polyhex',
                polyhedral_conversion_performed=False,elapsed_seconds=.2,iterations_completed=200,
                minimum_fluent_orthogonal_quality=.2,maximum_fluent_aspect_ratio=60.,numerical_criteria_met=True,
                saved_data_crosscheck={'checked':True},implementation_sha256=mesh['implementation_sha256'],
                domain_artifact_sha256=mesh['domain_artifact_sha256'],outlet_profile=row['outlet_profile'],interior_profile=row['interior_profile'])
        ident=result['result_id'];store.jobs[ident]=dict(job_id=ident,status='completed',message='synthetic orchestration test only',result=result)
        return {'job_id':ident}
    monkeypatch.setattr(store,'_start',start)
    monkeypatch.setattr(store,'get_result',lambda ident,*args:(results[ident],tmp_path))
    folder=tmp_path/'study';folder.mkdir();(folder/'plan.json').write_text(json.dumps(config))
    run_study(store,'study',folder,config,domain,threading.Event())
    task=store.status('study');assert task['status']=='completed'
    assert calls==(['mesh']*3+['flow']*3 if separated else ['mesh']*3)
    assert task['result']['comparison']['mesh_preflight']['passed']==separated
    assert task['result']['comparison']['scoped_grid_convergence_passed']==separated
    assert all(row['status']==('completed' if separated else 'mesh_ready') for row in task['result']['rows'])
    assert not task['result']['grid_independence_verified']
    assert store.reservation is None and store.active is None


def test_layered_worker_uses_solver_runtime_and_exports_native_evidence(stores,tmp_path,monkeypatch):
    models,designs,scheme,domains=stores;models.gmsh_path=None
    domain=domains.create(domain_config(scheme))
    native_python=tmp_path/'test-python.exe';native_python.write_bytes(b'SYNTHETIC RUNTIME MARKER')
    native_exe=tmp_path/'test-fluent.exe';native_exe.write_bytes(b'SYNTHETIC RUNTIME MARKER')
    store=WorkflowStore(tmp_path/'jobs',domains,fluent={'python':str(native_python),'executable':str(native_exe)})
    calls=[];cleaned=[]
    class Process:
        _handle=0;returncode=0
        def poll(self):return 0
        def wait(self,**kwargs):return 0
    class Job:
        def assign(self,p):pass
        def close(self):pass
    def launch(argv,**kwargs):
        from pathlib import Path
        folder=Path(argv[-1]);calls.append((argv,kwargs))
        for name in ['flow.msh','mesh_arrays.npz','native_mesh.cas.h5','meshing.trn','mesh_controls.json',
                'native_workflow.json','native_quality.json','geometry_quality.json','surface.msh','volume_refinement.json','volume_controls.json']:
            (folder/name).write_bytes(b'SYNTHETIC EXPORT CONTRACT TEST ONLY')
        (folder/'native_drive.json').write_text('{}')
        (folder/'result.json').write_text(json.dumps({'ok':True,'result':{'cells':2,'mesh_method':'layered-polyhex'}}))
        return Process()
    monkeypatch.setattr('nozzle_platform.workflows.WindowsJob',Job)
    monkeypatch.setattr('nozzle_platform.workflows.subprocess.Popen',launch)
    monkeypatch.setattr('nozzle_platform.layered_mesh.cleanup_native_mapping',lambda folder:cleaned.append(folder))
    request=mesh_request(mesh_method='layered-polyhex');request.update(domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'])
    task=store.start_mesh(request)
    end=time.monotonic()+5
    while store.active is not None and time.monotonic()<end:time.sleep(.01)
    final=store.status(task['job_id']);assert final['status']=='completed',final['message']
    assert calls[0][0][0]==str(native_python) and calls[0][1]['shell'] is False
    assert cleaned==[store.folders[task['job_id']]]
    result=store.export(task['job_id'],domain['domain_id'],scheme['scheme_id'])
    with zipfile.ZipFile(designs.downloads[result['download_url'].rsplit('/',1)[-1]]) as bundle:
        assert 'native_mesh.cas.h5' in bundle.namelist()
        assert 'mesh_controls.json' in bundle.namelist()
        assert 'volume_controls.json' in bundle.namelist()
        assert 'flow.gmsh.msh' not in bundle.namelist()
        assert 'native_drive.json' not in bundle.namelist() and 'job.json' not in bundle.namelist()
