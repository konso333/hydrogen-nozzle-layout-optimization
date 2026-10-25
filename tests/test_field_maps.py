import json
import numpy as np
import pytest
from nozzle_platform.field_maps import section,extract,render


def cubes(tmp_path,origins=((0,0,0),)):
    import h5py
    vertices=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]],float)*.001
    faces=np.array([[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]])
    points=np.concatenate([vertices+np.array(o)*.001 for o in origins]);n=len(origins)
    case=tmp_path/'case.h5';data=tmp_path/'data.h5'
    with h5py.File(case,'w') as f:
        m=f.create_group('meshes/1');m['cells/zoneTopology/minId']=[1];m['cells/zoneTopology/maxId']=[n]
        m['nodes/coords/1']=points;m['faces/nodes/1/nnodes']=np.full(n*6,4)
        m['faces/nodes/1/nodes']=np.concatenate([faces.flatten()+i*8+1 for i in range(n)])
        m['faces/c0/1']=np.repeat(np.arange(n)+1,6);d=m.create_dataset('faces/c1/1',data=np.array([],dtype=np.int64));d.attrs['minId']=[1]
    with h5py.File(data,'w') as f:
        g=f.create_group('results/1/phase-1/cells')
        for name,value in [('SV_U',1.),('SV_V',2.),('SV_W',2.),('SV_P',50.),('SV_T',320.)]:
            d=g.create_dataset(name+'/1',data=np.full(n,value));d.attrs['minId']=[1];d.attrs['maxId']=[n]
        d=g.create_dataset('SV_Y/1',data=np.tile([.1,.2,0,.7],(n,1)));d.attrs['minId']=[1];d.attrs['maxId']=[n]
    return case,data


def area(poly):return abs(np.dot(poly[:,0],np.roll(poly[:,1],-1))-np.dot(poly[:,1],np.roll(poly[:,0],-1)))/2


def test_real_cube_cut_has_exact_area_units_and_saved_values(tmp_path):
    case,data=cubes(tmp_path);r=section(case,data,position_mm=.5)
    assert len(r['polygons'])==1 and area(r['polygons'][0])==pytest.approx(1.)
    assert r['cell_ids'].tolist()==[1]
    assert r['values']['speed'].tolist()==[3.]
    assert r['values']['h2'].tolist()==[.1] and r['values']['pressure'].tolist()==[50.]


def test_disconnected_fluid_sections_do_not_fill_the_solid_gap(tmp_path):
    case,data=cubes(tmp_path,((0,0,0),(3,0,0)));r=section(case,data,position_mm=.5)
    assert len(r['polygons'])==2 and sum(area(p) for p in r['polygons'])==pytest.approx(2.)
    assert all(np.ptp(p[:,0])<=1.00001 for p in r['polygons'])


def test_face_coincident_section_is_not_double_painted(tmp_path):
    case,data=cubes(tmp_path,((0,0,0),(0,1,0)));r=section(case,data,position_mm=1.)
    assert len(r['polygons'])==1 and r['cell_ids'].tolist()==[1]


def test_outside_section_or_invalid_species_is_a_reported_failure(tmp_path):
    import h5py
    case,data=cubes(tmp_path)
    with pytest.raises(ValueError,match='之外'):section(case,data,position_mm=2.)
    with h5py.File(data,'a') as f:f['results/1/phase-1/cells/SV_Y/1'][0,0]=.9
    with pytest.raises(ValueError,match='组分'):section(case,data,position_mm=.5)


def test_maps_export_png_csv_and_provenance_and_verify_tampering(tmp_path):
    case,data=cubes(tmp_path);folder=tmp_path/'maps'
    r=extract(case,data,folder,position_mm=.5);render([folder],labels=['SYNTHETIC TEST'])
    output=json.loads((folder/'maps.json').read_text(encoding='utf8'))
    assert set(output['images_sha256'])=={'speed.png','pressure.png','temperature.png','h2.png'}
    assert r['bounds']['speed']==[3.,3.] and r['scientific_eligible'] is False
    assert 'cell_id' in (folder/'section_values.csv').read_text(encoding='utf-8-sig')
    (folder/'section.npz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='改变'):render([folder])


def test_concave_face_is_triangulated_without_filling_its_missing_corner():
    from nozzle_platform.field_maps import face_triangles
    polygon=np.array([[0,0,0],[2,0,0],[2,0,1],[1,0,1],[1,0,2],[0,0,2]],float)
    triangles=face_triangles(polygon,1e-10)
    assert len(triangles)==4
    assert sum(area(t[:,[0,2]]) for t in triangles)==pytest.approx(3.)
    assert all(t[:,0].mean()<=1 or t[:,2].mean()<=1 for t in triangles)
    reverse=face_triangles(polygon[::-1],1e-10)
    assert sum(area(t[:,[0,2]]) for t in reverse)==pytest.approx(3.)


def test_warped_quad_retains_its_3d_vertices_when_cutting():
    from nozzle_platform.field_maps import face_triangles
    vertices=np.array([[0,-.1,0],[1,.1,0],[1,-.1,1],[0,.1,1]])
    triangles=face_triangles(vertices,1e-10)
    assert len(triangles)==2
    assert all(any(np.array_equal(p,v) for v in vertices) for t in triangles for p in t)


def test_internal_coincident_triangulation_flaps_cancel_from_cell_boundary(tmp_path):
    import h5py
    case,data=cubes(tmp_path)
    with h5py.File(case,'a') as f:
        m=f['meshes/1'];original=m['nodes/coords/1'][()]
        flap=np.array([[.0002,.0002,.0002],[.0008,.0008,.0002],[.0002,.0008,.0008]])
        del m['nodes/coords/1'];m['nodes/coords/1']=np.vstack([original,flap])
        counts=m['faces/nodes/1/nnodes'][()];nodes=m['faces/nodes/1/nodes'][()]
        del m['faces/nodes/1/nnodes'];del m['faces/nodes/1/nodes'];del m['faces/c0/1']
        m['faces/nodes/1/nnodes']=np.r_[counts,3,3];m['faces/nodes/1/nodes']=np.r_[nodes,9,10,11,9,11,10]
        m['faces/c0/1']=np.ones(8,dtype=int)
    r=section(case,data,position_mm=.5)
    assert len(r['polygons'])==1 and area(r['polygons'][0])==pytest.approx(1.)
    assert r['geometric_offset_mm']!=0 and abs(r['geometric_offset_mm'])<1e-5


def test_assembly_preview_records_sampling_and_preserves_all_nozzles(tmp_path):
    from nozzle_platform.field_maps import assembly_image
    preview=tmp_path/'preview.npz'
    np.savez(preview,vertices=[[0,0,0],[1,0,0],[0,1,0],[0,0,2]],triangles=[[0,1,2],[0,1,3],[0,2,3],[1,2,3]])
    report=assembly_image(preview,[[0,0],[3,0]],tmp_path/'assembly.png')
    assert report['drawn_triangles']==8 and report['triangle_stride']==1
    assert len(report['coordinates_mm'])==2 and (tmp_path/'assembly.png').is_file()
