"""Geometry comparison, strict deduplication and immutable library contracts."""
import copy,csv,hashlib,io,json,math,threading,zipfile
from pathlib import Path
import pytest
from nozzle_platform.design import DesignStore,generate
from nozzle_platform.geometry_compare import compare
from nozzle_platform.geometry_records import geometry_key,verify_scheme
from nozzle_platform.geometry_library import GeometryLibrary
from nozzle_platform.models import MODEL_ENGINE,digest
from tests.test_platform import request,model,server,call

class ReferenceModels:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.sources={};self.loads=0
        self.raw=b'isolated synthetic source only; no CAD or CFD evidence';source=digest(self.raw);origin=[0,0,0]
        identity=digest(json.dumps([MODEL_ENGINE,source,'y',origin],sort_keys=True).encode())
        self.value=dict(model(),model_id=identity,source_id=source,name='synthetic.step',label='SYNTHETIC',axis='y',origin_input_mm=origin,origin_mm=origin,model_engine=MODEL_ENGINE,source_unit='mm',volume_mm3=1.)
        (self.root/(source+'.step')).write_bytes(self.raw)
    def get(self,key):
        if key!=self.value['model_id']:raise ValueError('模型编号不匹配。')
        return copy.deepcopy(self.value)
    def add_source(self,name,raw,**kwargs):
        assert raw==self.raw;entry=dict(source_id=self.value['source_id'],default_axis='y');self.sources[entry['source_id']]=entry;return entry
    def load(self,source_id,**kwargs):self.loads+=1;return self.get(self.value['model_id'])
    def catalog(self):return list(self.sources.values())

@pytest.fixture
def library(tmp_path):
    models=ReferenceModels(tmp_path/'models');designs=DesignStore(tmp_path/'designs',models);lib=GeometryLibrary(tmp_path/'library',designs)
    return lib,designs,models

def scheme(designs,models,**changes):return designs.create(request(model_id=models.value['model_id'],**changes))

@pytest.mark.parametrize('N,R,region,sweep',[(1,40,'circle',360),(11,60,'circle',360),(20,100,'sector',120),(50,100,'circle',360),(100,150,'circle',360)])
def test_comparison_preserves_conditions_and_never_claims_physical_rank(N,R,region,sweep):
    config=request(N=N,radius_mm=R,region=region,sweep_angle_deg=sweep);original=copy.deepcopy(config)
    result=compare(dict(design=config),model());assert config==original and result['recommended_family'] is None and result['solver_started'] is False
    assert len(result['rows'])==4
    for row in result['rows']:
        if not row['feasible']:continue
        candidate=row['scheme'];verify_scheme(candidate)
        assert all(candidate['request'][k]==config[k] for k in config if k!='family')

def test_deduplication_is_exact_coordinate_set_only():
    result=compare(dict(design=request(N=1)),model());assert result['unique_coordinate_sets']==1
    base=generate(request(N=3),model());permuted=copy.deepcopy(base);permuted['coordinates_mm'].reverse()
    assert geometry_key(base)==geometry_key(permuted)
    moved=copy.deepcopy(base);moved['coordinates_mm']=[[x+1,y] for x,y in base['coordinates_mm']];assert geometry_key(base)!=geometry_key(moved)
    rotated=copy.deepcopy(base);rotated['coordinates_mm']=[[-y,x] for x,y in base['coordinates_mm']];assert geometry_key(base)!=geometry_key(rotated)
    other=copy.deepcopy(base);other['request']['wall_clearance_mm']+=.1;assert geometry_key(base)!=geometry_key(other)

def test_manual_comparison_requires_explicit_automatic_mode():
    with pytest.raises(ValueError,match='自动定位'):compare(dict(design=request(spacing_mode='manual')),model())

def test_comparison_proposals_are_not_exportable_until_adopted(library):
    _,designs,models=library;original=request(model_id=models.value['model_id'],N=11,radius_mm=60)
    result=designs.compare(dict(design=original));assert not designs.schemes
    chosen=result['suggestions'][0]
    with pytest.raises(ValueError):designs.export(chosen['scheme_id'])
    with pytest.raises(ValueError):designs.adopt(dict(advice_id=result['advice_id'],suggestion_id=chosen['suggestion_id'],current_design=dict(original,N=12)))
    adopted=designs.adopt(dict(advice_id=result['advice_id'],suggestion_id=chosen['suggestion_id'],current_design=original));verify_scheme(adopted)

def test_save_is_idempotent_and_survives_store_restart(library):
    lib,designs,models=library;original=scheme(designs,models,N=11,radius_mm=60)
    first=lib.save(dict(scheme_id=original['scheme_id'],name='十一喷嘴'))
    assert lib.save(dict(scheme_id=original['scheme_id'],name='十一喷嘴'))['already_saved']
    restarted=GeometryLibrary(lib.root,DesignStore(designs.root,models));assert restarted.catalog()['entries'][0]['available']
    loaded=restarted.load(first['entry']['entry_id']);assert loaded['coordinates_preserved'] and loaded['scheme']==original and models.loads==1
    assert restarted.designs.get(original['scheme_id'])==original and loaded['solver_started'] is False

def test_library_export_matches_saved_coordinates_and_all_hashes(library):
    lib,designs,models=library;original=scheme(designs,models);entry=lib.save(dict(scheme_id=original['scheme_id'],name='记录包'))['entry'];response=lib.export(entry['entry_id'])
    path=designs.downloads[response['download_url'].rsplit('/',1)[-1]]
    with zipfile.ZipFile(path) as z:
        manifest=json.loads(z.read('manifest.json'));assert set(z.namelist())==set(manifest)|{'manifest.json'}
        for name,h in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==h
        assert digest(z.read('single_nozzle.step'))==original['model']['source_id']
        rows=list(csv.DictReader(io.StringIO(z.read('coordinates.csv').decode('utf-8-sig'))));assert [[float(r['x_mm']),float(r['y_mm'])] for r in rows]==original['coordinates_mm']
        assert not any('.msh' in n or '.cas' in n or '.dat' in n for n in z.namelist())

@pytest.mark.parametrize('tamper',['coordinates','coordinates_and_header','source','origin','summary'])
def test_tampered_records_are_unavailable_and_never_load(library,tamper):
    lib,designs,models=library;original=scheme(designs,models);key=lib.save(dict(scheme_id=original['scheme_id'],name='损坏检查'))['entry']['entry_id'];folder=lib.folder(key)
    if tamper.startswith('coordinates'):
        (folder/'coordinates.csv').write_text('wrong coordinates',encoding='utf-8')
        if tamper.endswith('header'):
            header=json.loads((folder/'entry.json').read_text(encoding='utf-8'));header['files_sha256']['coordinates.csv']=hashlib.sha256((folder/'coordinates.csv').read_bytes()).hexdigest();(folder/'entry.json').write_text(json.dumps(header),encoding='utf-8')
    elif tamper=='source':(models.root/(models.value['source_id']+'.step')).write_bytes(b'changed')
    else:
        doc=json.loads((folder/'scheme.json').read_text(encoding='utf-8'))
        if tamper=='origin':doc['model']['origin_input_mm'][0]=1
        else:doc['summary']['nozzle_count']=99
        (folder/'scheme.json').write_text(json.dumps(doc),encoding='utf-8')
    assert lib.catalog()['entries'][0]['available'] is False
    with pytest.raises(ValueError):lib.load(key)
    assert models.loads==0

def test_different_names_preserve_independent_adjustment_records(library):
    lib,designs,models=library;original=request(model_id=models.value['model_id'],N=50)
    report=designs.advise(dict(design=original,explore_families=True));option=next(x for x in report['suggestions'] if x['kind']=='quantity')
    adopted=designs.adopt(dict(advice_id=report['advice_id'],suggestion_id=option['suggestion_id'],current_design=original))
    keys=[lib.save(dict(scheme_id=adopted['scheme_id'],name=name))['entry']['entry_id'] for name in ('原目标50','供组员回放')]
    assert keys[0]!=keys[1] and len(lib.catalog()['entries'])==2
    for key in keys:assert lib.read(key)[1]['design_assistance']['requested_design']['N']==50

def test_library_rejects_path_traversal_and_bad_save_names(library):
    lib,designs,models=library;original=scheme(designs,models)
    for key in ('../models','x'*64,None):
        with pytest.raises(ValueError):lib.folder(key)
    for name in ('','x'*61,'bad\nname'):
        with pytest.raises(ValueError):lib.save(dict(scheme_id=original['scheme_id'],name=name))

@pytest.mark.parametrize('N,R,feasible',[(11,60,True),(50,40,False)])
def test_comparison_export_keeps_all_rows_hashes_and_failed_statuses(library,N,R,feasible):
    _,designs,models=library;original=request(model_id=models.value['model_id'],N=N,radius_mm=R);result=designs.compare(dict(design=original))
    payload=dict(advice_id=result['advice_id'],current_design=original)
    with pytest.raises(ValueError):designs.export_comparison(dict(payload,current_design=dict(original,N=12)))
    response=designs.export_comparison(payload);path=designs.downloads[response['download_url'].rsplit('/',1)[-1]]
    with zipfile.ZipFile(path) as z:
        manifest=json.loads(z.read('manifest.json'));assert set(z.namelist())==set(manifest)|{'manifest.json'}
        for name,h in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==h
        rows=list(csv.DictReader(io.StringIO(z.read('comparison.csv').decode('utf-8-sig'))));assert len(rows)==4 and all(int(r['N'])==N and float(r['radius_mm'])==R for r in rows)
        assert all(r['feasible']==str(feasible) for r in rows)
        assert len([n for n in z.namelist() if n.endswith('/coordinates.csv')])==(4 if feasible else 0)
        assert not any('pressure' in key or 'heat' in key for key in rows[0])

def test_comparison_and_library_http_routes(server,tmp_path):
    models=ReferenceModels(tmp_path/'references');designs=DesignStore(tmp_path/'design_records',models);server.designs=designs;server.geometry_library=GeometryLibrary(tmp_path/'records',designs)
    headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'}
    original=request(model_id=models.value['model_id'],N=3)
    def post(path,data):
        status,raw,_=call(server,'POST',path,json.dumps(data).encode(),headers);return status,json.loads(raw)
    status,comparison=post('/api/design-compare',dict(design=original));assert status==200
    assert post('/api/design-compare-export',dict(advice_id=comparison['advice_id'],current_design=original))[0]==200
    option=comparison['suggestions'][0];status,chosen=post('/api/design-assist-adopt',dict(advice_id=comparison['advice_id'],suggestion_id=option['suggestion_id'],current_design=original));assert status==200
    status,saved=post('/api/geometry-library-save',dict(scheme_id=chosen['scheme_id'],name='HTTP记录'));assert status==200
    key=saved['entry']['entry_id'];assert call(server,'GET','/geometry-library-files/'+key+'/layout.svg')[0]==200
    assert post('/api/geometry-library-load',dict(entry_id=key))[0]==200
    assert post('/api/geometry-library-export',dict(entry_id='../models'))[0]==400
    assert call(server,'GET','/geometry-library-files/'+key+'/../../secret')[0]==404
    assert call(server,'GET','/geometry_workspace.js')[0]==200
    assert call(server,'GET','/geometry_workspace.css')[0]==200
