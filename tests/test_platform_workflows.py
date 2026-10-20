"""Physical mesh topology, lineage, task failures and parameter rejection."""

import copy
import json
from pathlib import Path
import threading
import time
import zipfile

import numpy as np
import pytest

from nozzle_platform.volume_mesh import face_topology, write_fluent
from nozzle_platform.domain_cad import ROLES
from nozzle_platform.flow_validation import polygon_patches, verify_saved_fluxes, case_openings, case_volume
from nozzle_platform.workflows import WorkflowStore, normalize_mesh, normalize_flow, sha256
from tests.test_platform_domains import stores,config


def mesh_request(**changes):
    return dict(domain_id='domain',scheme_id='scheme',min_size_mm=.08,max_size_mm=4,
                inlet_size_mm=.2,refine_distance_mm=2,**changes)


def flow_request(**changes):
    result=dict(mesh_id='mesh',domain_id='domain',scheme_id='scheme',fuel_velocity_m_s=1.,
        oxidizer_velocity_m_s=1.,temperature_K=300.,oxidizer_o2_mass_fraction=.233,iterations=100)
    result.update(changes)
    return result


@pytest.mark.parametrize('field,value',[
    ('min_size_mm',0),('min_size_mm',True),('max_size_mm',float('nan')),
    ('inlet_size_mm',.01),('max_size_mm',.1),('refine_distance_mm',.2),
    ('max_size_mm',21),('refine_distance_mm',101),('algorithm','bad'),('algorithm',{}),
    ('curvature_samples',15),('curvature_samples',65),('passage_size_mm',.01),('passage_size_mm',10)])
def test_mesh_rejects_invalid_sizes(field,value):
    request=mesh_request();request[field]=value
    with pytest.raises(ValueError):
        normalize_mesh(request)


@pytest.mark.parametrize('changes',[
    {'iterations':501},{'iterations':True},{'iterations':1.5},{'iterations':0},
    {'fuel_velocity_m_s':0},{'oxidizer_velocity_m_s':101},
    {'temperature_K':100},{'temperature_K':float('inf')},
    {'oxidizer_o2_mass_fraction':1},{'oxidizer_o2_mass_fraction':0}, {'shell':'anything'}])
def test_flow_rejects_invalid_conditions_and_commands(changes):
    with pytest.raises(ValueError):
        normalize_flow(flow_request(**changes))


def tetra():
    # One unit tetrahedron: exactly known volume 1/6, every face exterior.
    points=np.array([[0.,0.,0.],[1,0,0],[0,1,0],[0,0,1]])
    cells=np.array([[0,1,2,3]])
    boundaries=dict(zip(ROLES,[np.array([f]) for f in [[1,2,3],[0,3,2],[0,1,3],[0,2,1]]]))
    return points,cells,boundaries


def test_polygon_openings_merge_shared_edges_but_not_point_contacts():
    assert polygon_patches([[0,1,2],[2,1,3],[4,5,6]])==2
    assert polygon_patches([[0,1,2],[2,3,4]])==2
    assert polygon_patches([[0,1,2,3],[2,1,4],[4,1,5],[6,7,8]])==2


def saved_hdf_pair(directory):
    h5py=pytest.importorskip('h5py')
    names=['fuel_inlet','oxidizer_inlet','outlet','wall']
    with h5py.File(directory/'final.cas.h5','w') as case:
        top=case.create_group('meshes/1/faces/zoneTopology')
        top['name']=np.array([';'.join(names).encode()])
        top['minId']=np.arange(1,5);top['maxId']=np.arange(1,5)
        case['meshes/1/faces/nodes/1/nnodes']=np.full(4,3)
        case['meshes/1/faces/nodes/1/nodes']=np.arange(1,13)
    composition=np.array([[1.,0,0,0],[0,.233,0,.767],[.4,.1398,0,.4602],[0,.233,0,.767]])
    flux=np.array([-2.,-3.,5.,0.])
    with h5py.File(directory/'final.dat.h5','w') as data:
        data['results/1/phase-1/faces/SV_FLUX/1']=flux
        species=data.create_dataset('results/1/phase-1/faces/SV_Y/1',data=composition)
        species.attrs['minId']=[1]
        data['results/1/phase-1/cells/SV_Y/1']=composition[:2]
    return {'mass_flow_kg_s':dict(zip(names,map(float,-flux))),
        'species_convective_flow_kg_s':{species:dict(zip(names[:3],map(float,-flux[:3]*composition[:3,index])))
            for index,species in enumerate(['h2','o2','h2o','n2'])}}


def test_saved_fluent_mass_species_and_openings_are_independently_checked(tmp_path):
    point=saved_hdf_pair(tmp_path)
    checked=verify_saved_fluxes(tmp_path,point)
    assert checked['checked'] and checked['species_fraction_range_checked']
    assert checked['boundary_reports']['outlet']['mass_flow_kg_s']==-5
    assert case_openings(tmp_path/'final.cas.h5')=={'fuel_inlet':1,'oxidizer_inlet':1,'outlet':1}


@pytest.mark.parametrize('kind',['mass','species','sum','range'])
def test_inconsistent_saved_fluent_data_rejects_report(tmp_path,kind):
    point=saved_hdf_pair(tmp_path)
    if kind=='mass':point['mass_flow_kg_s']['outlet']=-4
    elif kind=='species':point['species_convective_flow_kg_s']['h2']['outlet']=0
    else:
        import h5py
        with h5py.File(tmp_path/'final.dat.h5','r+') as data:
            data['results/1/phase-1/cells/SV_Y/1'][0]=[.9,0,0,0] if kind=='sum' else [1.1,-.1,0,0]
    with pytest.raises(ValueError):
        verify_saved_fluxes(tmp_path,point)


def test_exact_tetra_volume_handedness_and_complete_export(tmp_path):
    points,cells,boundaries=tetra()
    faces,c0,c1,labels,report=face_topology(points,cells,boundaries)
    assert report['volume_m3']==pytest.approx(1/6)
    assert report['max_cell_area_closure_error_m2']<1e-15
    assert report['faces']==4 and report['connected_fluid_components']==1
    assert set(labels)=={1,2,3,4} and np.all(c1==-1)
    centre=points[cells].mean(axis=1)
    normals=np.cross(points[faces[:,1]]-points[faces[:,0]],points[faces[:,2]]-points[faces[:,0]])
    assert np.all(np.einsum('ij,ij->i',normals,centre[c0]-points[faces].mean(axis=1))>0)
    output=tmp_path/'flow.msh'
    write_fluent(output,points,cells,boundaries)
    text=output.read_text()
    assert '(12 (2 1 1 1 2))' in text and 'SI metres' in text
    for name in ROLES:
        assert name in text


def test_saved_polygon_mesh_volume_has_independent_known_value(tmp_path):
    h5py=pytest.importorskip('h5py')
    points,cells,boundaries=tetra()
    faces,*_=face_topology(points,cells,boundaries)
    path=tmp_path/'tet.cas.h5'
    with h5py.File(path,'w') as case:
        case['meshes/1/nodes/coords/1']=points
        top=case.create_group('meshes/1/faces/zoneTopology')
        top['name']=np.array([';'.join(ROLES).encode()])
        top['minId']=np.arange(1,5);top['maxId']=np.arange(1,5)
        case['meshes/1/faces/nodes/1/nnodes']=np.full(4,3)
        case['meshes/1/faces/nodes/1/nodes']=faces.ravel()+1
    assert case_volume(path)==pytest.approx(1/6,rel=1e-12)


@pytest.mark.parametrize('kind',['inverted','duplicate-cell','missing-face','wrong-face','duplicated-boundary','disconnected'])
def test_unusable_mesh_cannot_be_exported(tmp_path,kind):
    points,cells,boundaries=tetra()
    if kind=='inverted': cells=cells[:,[0,2,1,3]]
    elif kind=='duplicate-cell': cells=np.repeat(cells,2,axis=0)
    elif kind=='missing-face': boundaries['wall']=np.empty((0,3),dtype=int)
    elif kind=='wrong-face': boundaries['wall']=np.array([[1,1,3]])
    elif kind=='duplicated-boundary': boundaries['wall']=boundaries['outlet'].copy()
    elif kind=='disconnected':
        points=np.r_[points,points+[2,0,0]];cells=np.r_[cells,cells+4]
        boundaries={k:np.r_[v,v+4] for k,v in boundaries.items()}
    with pytest.raises(ValueError):
        write_fluent(tmp_path/'bad.msh',points,cells,boundaries)
    assert not (tmp_path/'bad.msh').exists()


def test_parent_domain_integrity_checked_before_launch(stores,tmp_path):
    models,_,scheme,domains=stores
    domain=domains.create(config(scheme))
    workflows=WorkflowStore(tmp_path/'jobs',domains)
    request=mesh_request();request.update(domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'])
    (domains.directories[domain['domain_id']]/'fluid.brep').write_text('changed')
    with pytest.raises(ValueError,match='文件已改变'):
        workflows.start_mesh(request)
    assert not workflows.jobs and workflows.active is None


def test_export_reconstructs_report_and_rejects_modified_mesh(stores,tmp_path):
    _,designs,scheme,domains=stores
    domain=domains.create(config(scheme))
    workflows=WorkflowStore(tmp_path/'jobs',domains)
    folder=tmp_path/'result';folder.mkdir()
    (folder/'flow.msh').write_text('synthetic mesh, export test only')
    record={'kind':'mesh','result_id':'mesh','domain_id':domain['domain_id'],'scheme_id':scheme['scheme_id'],
        'artifact_sha256':{'flow.msh':sha256(folder/'flow.msh')}}
    workflows.results['mesh'],workflows.folders['mesh']=record,folder
    (folder/'report.json').write_text('{"forged":true}')
    exported=workflows.export('mesh',domain['domain_id'],scheme['scheme_id'])
    with zipfile.ZipFile(designs.downloads[exported['download_url'].rsplit('/',1)[-1]]) as bundle:
        assert json.loads(bundle.read('report.json'))==record
        assert 'job.json' not in bundle.namelist()
    with pytest.raises(ValueError):
        workflows.export('mesh','other',scheme['scheme_id'])
    (folder/'flow.msh').write_text('changed')
    with pytest.raises(ValueError,match='文件已改变'):
        workflows.export('mesh',domain['domain_id'],scheme['scheme_id'])


def test_only_one_job_and_cancellation_before_launch(stores,tmp_path):
    _,_,scheme,domains=stores
    domain=domains.create(config(scheme))
    workflows=WorkflowStore(tmp_path/'jobs',domains)
    workflows.active='existing'
    request=mesh_request();request.update(domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'])
    with pytest.raises(ValueError,match='已有'):
        workflows.start_mesh(request)
    folder=tmp_path/'cancel';folder.mkdir()
    event=threading.Event();event.set()
    workflows.jobs['existing']={'status':'queued','result':None}
    workflows._run('existing',folder,{'kind':'mesh'},event)
    assert workflows.status('existing')['status']=='cancelled'
    assert workflows.active is None and not workflows.results


def test_missing_worker_result_fails_and_releases_slot(stores,tmp_path,monkeypatch):
    _,_,scheme,domains=stores
    domain=domains.create(config(scheme))
    workflows=WorkflowStore(tmp_path/'jobs',domains)
    folder=tmp_path/'failed';folder.mkdir()
    workflows.jobs['job']={'status':'queued','result':None}
    workflows.active='job'
    class Process:
        _handle=0
        returncode=1
        def poll(self):return 1
        def wait(self,**kwargs):return 1
    class Job:
        def assign(self,p):pass
        def close(self):pass
    monkeypatch.setattr('nozzle_platform.workflows.WindowsJob',Job)
    monkeypatch.setattr('nozzle_platform.workflows.subprocess.Popen',lambda *a,**kw:Process())
    workflows._run('job',folder,{'kind':'mesh'},threading.Event())
    assert workflows.status('job')['status']=='failed'
    assert workflows.active is None and not workflows.results
