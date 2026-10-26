import json
from tests.test_platform import server,call
from tests.test_presets import RAW,models
from tests.test_pipelines import request


def post(server,path,document):
    return call(server,'POST',path,json.dumps(document).encode(),{'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json'})


def test_real_http_preset_save_load_download_and_locked_runtime(server,tmp_path,monkeypatch):
    fake=models(tmp_path/'synthetic_runtime');monkeypatch.setattr(server.models,'cad',fake.cad)
    source=server.models.add_source('fixture.step',RAW);model=server.models.load(source['source_id'],axis='y',origin=[1,2,3])
    config=request();config['design']['model_id']=model['model_id']
    code,raw,_=post(server,'/api/preset-save',dict(name='HTTP SYNTHETIC TEST',request=config));assert code==200
    saved=json.loads(raw);key=saved['preset_id'];assert call(server,'GET','/api/presets')[0]==200
    code,raw,_=call(server,'GET','/preset-files/'+key+'.json');assert code==200
    doc=json.loads(raw);assert not doc['request']['domain']['template_confirmed']
    assert post(server,'/api/preset-import',doc)[0]==200
    code,raw,_=post(server,'/api/preset-load',dict(preset_id=key));assert code==200
    assert json.loads(raw)['model']['model_id']==model['model_id']
    assert post(server,'/api/pipeline-check',config)[0]==200
    assert not server.pipelines.folders and not server.workflows.active
    server.workflows.reservation='synthetic-held-task'
    assert post(server,'/api/preset-load',dict(preset_id=key))[0]==400
    assert post(server,'/api/preset-save',dict(name='blocked',request=config))[0]==400


def test_preset_routes_preserve_local_origin_and_download_guards(server):
    for name in ['/presets.js','/presets.css']:assert call(server,'GET',name)[0]==200
    assert call(server,'GET','/preset-files/../../secret.json')[0]==404
    assert post(server,'/api/preset-load',dict(preset_id='../secret'))[0]==400
    foreign={'Origin':'https://elsewhere.example','Content-Type':'application/json'}
    assert call(server,'POST','/api/preset-import',b'{}',foreign)[0]==403
    assert not server.presets.catalog()
