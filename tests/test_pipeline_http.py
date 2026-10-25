import json
from tests.test_platform import server,call


def test_pipeline_static_routes_and_history_are_local(server):
    for path in ['/pipeline.js','/pipeline.css','/api/pipelines']:
        code,raw,_=call(server,'GET',path)
        assert code==200
        if path=='/api/pipelines':assert json.loads(raw)=={'tasks':[]}
    assert call(server,'GET','/api/pipelines/missing')[0]==404
    assert call(server,'GET','/pipeline-files/missing/../../secret')[0]==404


def test_pipeline_invalid_requests_and_foreign_origin_never_launch_jobs(server):
    headers={'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json'}
    assert call(server,'POST','/api/pipeline',b'{}',headers)[0]==400
    headers['Origin']='https://elsewhere.example'
    assert call(server,'POST','/api/pipeline',b'{}',headers)[0]==403
    assert not server.workflows.active and not server.workflows.reservation and not server.pipelines.folders
