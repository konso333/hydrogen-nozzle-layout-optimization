"""Portable geometry records with synthetic CAD seams and independent stores."""
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from nozzle_platform.design import DesignStore
from nozzle_platform.geometry_library import GeometryLibrary
from nozzle_platform.geometry_records import canonical,digest,geometry_key
from nozzle_platform.models import ModelStore
from tests.test_platform import call,request,server
from tests.test_presets import RAW


@pytest.fixture
def stores(tmp_path,monkeypatch):
    def cad(self,job,**kwargs):
        return dict(axis=job['axis'],origin_mm=job['origin'] if job['origin'] is not None else [1.,2.,3.],
                    bounds_mm=[[-10,-10,-28],[10,10,4]],envelope_diameter_mm=20,volume_mm3=1.,
                    solid_count=1,triangle_count=12,vertices=[],triangles=[])
    monkeypatch.setattr(ModelStore,'cad',cad)
    def make(name):
        models=ModelStore(tmp_path/name/'models')
        designs=DesignStore(tmp_path/name/'designs',models)
        return GeometryLibrary(tmp_path/name/'library',designs)
    return make('source'),make('target')


def exported(library,axis='y',origin=None):
    models=library.designs.models
    source=models.add_source('fixture.step',RAW)
    model=models.load(source['source_id'],axis=axis,origin=origin)
    scheme=library.designs.create(request(model_id=model['model_id'],N=11,radius_mm=60))
    entry=library.save(dict(scheme_id=scheme['scheme_id'],name='可迁移排布'))['entry']
    response=library.export(entry['entry_id'])
    path=library.designs.downloads[response['download_url'].rsplit('/',1)[-1]]
    return path.read_bytes(),entry,copy.deepcopy(scheme)


def package_files(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name:archive.read(name) for name in archive.namelist() if name!='manifest.json'}


def repack(files,*,rebuild_manifest=True,manifest=None):
    if rebuild_manifest:manifest={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in files.items():archive.writestr(name,raw)
        archive.writestr('manifest.json',canonical(manifest))
    return stream.getvalue()


@pytest.mark.parametrize('axis',['x','y','z'])
@pytest.mark.parametrize('origin',[None,[0,0,0],[0.,0.,0.]])
def test_import_into_empty_store_preserves_coordinates_reference_and_original_metadata(stores,axis,origin):
    source,target=stores
    raw,entry,scheme=exported(source,axis,origin)
    assert not target.designs.models.catalog()
    imported=target.import_package(raw)
    assert imported['entry']['entry_id']==entry['entry_id'] and imported['coordinates_preserved']
    assert not imported['already_saved'] and imported['solver_started'] is False
    saved,actual=target.read(entry['entry_id'])
    assert saved==source.read(entry['entry_id'])[0] and actual==scheme
    restored=target.load(entry['entry_id'])
    assert restored['model']['axis']==axis and restored['model']['origin_input_mm']==origin
    assert type(restored['model']['origin_input_mm'])==type(origin)
    assert restored['scheme']['coordinates_mm']==scheme['coordinates_mm']
    assert target.import_package(raw)['already_saved']
    assert len(target.catalog()['entries'])==1
    assert not list(target.root.glob('.import_*'))


@pytest.mark.parametrize('fault',['hash','csv_inner_hash','source','volume','unit','missing','path','oversize','nonfinite','not_zip'])
def test_invalid_packages_leave_live_models_and_records_untouched(stores,fault):
    source,target=stores
    raw,entry,scheme=exported(source)
    files=package_files(raw)
    if fault=='hash':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:manifest=json.loads(archive.read('manifest.json'))
        files['coordinates.csv']=b'changed'
        raw=repack(files,rebuild_manifest=False,manifest=manifest)
    elif fault=='csv_inner_hash':
        files['coordinates.csv']=b'changed'
        header=json.loads(files['entry.json'])
        header['files_sha256']['coordinates.csv']=hashlib.sha256(files['coordinates.csv']).hexdigest()
        files['entry.json']=canonical(header).encode()
        raw=repack(files)
    elif fault=='source':
        files['single_nozzle.step']+=b'\n'
        raw=repack(files)
    elif fault in ('volume','unit'):
        header=json.loads(files['entry.json'])
        modified=copy.deepcopy(scheme)
        if fault=='volume':modified['model']['volume_mm3']=2.
        else:modified['model']['source_unit']='m'
        files['scheme.json']=canonical(modified).encode()
        header['entry_id']=digest(dict(name=header['name'],scheme=modified))
        header['geometry_key']=geometry_key(modified)
        header['files_sha256']['scheme.json']=hashlib.sha256(files['scheme.json']).hexdigest()
        files['entry.json']=canonical(header).encode()
        raw=repack(files)
    elif fault=='missing':
        del files['layout.svg'];raw=repack(files)
    elif fault=='path':
        files['../README.txt']=files.pop('README.txt');raw=repack(files)
    elif fault=='oversize':
        files['README.txt']=b'a'*(1024*1024+1);raw=repack(files)
    elif fault=='nonfinite':
        header=json.loads(files['entry.json']);header['created_epoch']=float('nan')
        files['entry.json']=json.dumps(header).encode();raw=repack(files)
    else:raw=b'not a zip archive'
    with pytest.raises(ValueError):target.import_package(raw)
    assert not target.designs.models.catalog() and not list(target.designs.models.root.glob('*.step'))
    assert not target.catalog()['entries'] and not list(target.root.glob('.import_*'))


def test_import_rejects_duplicate_zip_members(stores):
    source,target=stores
    raw,entry,scheme=exported(source)
    output=io.BytesIO(raw)
    with pytest.warns(UserWarning,match='Duplicate'):
        with zipfile.ZipFile(output,'a') as archive:archive.writestr('entry.json',b'{}')
    with pytest.raises(ValueError,match='清单'):target.import_package(output.getvalue())
    assert not target.designs.models.catalog()


def test_import_preserves_existing_valid_record_and_can_repair_matching_source(stores):
    source,target=stores
    raw,entry,scheme=exported(source)
    target.import_package(raw)
    folder=target.folder(entry['entry_id'])
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    models=target.designs.models
    path=models.root/(scheme['model']['source_id']+'.step')
    path.write_bytes(b'interrupted earlier source upload')
    imported=target.import_package(raw)
    assert imported['already_saved'] and path.read_bytes()==RAW
    assert {p.name:p.read_bytes() for p in folder.iterdir()}==before


def test_import_endpoint_enforces_origin_content_type_and_idle_state(stores,server):
    source,target=stores
    raw,entry,scheme=exported(source)
    server.geometry_library=target
    headers={'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/zip'}
    assert call(server,'POST','/api/geometry-library-import',raw,dict(headers,Origin='https://elsewhere.example'))[0]==403
    assert call(server,'POST','/api/geometry-library-import',raw,dict(headers,**{'Content-Type':'application/json'}))[0]==400
    server.workflows.reservation='synthetic-active-job'
    assert call(server,'POST','/api/geometry-library-import',raw,headers)[0]==400
    assert not target.catalog()['entries']
    server.workflows.reservation=None
    status,body,_=call(server,'POST','/api/geometry-library-import',raw,headers)
    assert status==200 and json.loads(body)['coordinates_preserved']
