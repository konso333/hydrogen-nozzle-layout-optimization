import hashlib,json,threading,http.client
import pytest
from nozzle_platform.review_reports import ReviewStore
from nozzle_platform.server import PlatformServer

def pack(root,key='m16_test'):
    folder=root/key;folder.mkdir(parents=True)
    (folder/'audit.json').write_text('{"scope":"synthetic test"}',encoding='utf-8')
    report=dict(report_id=key,title='SYNTHETIC TEST',scientific_eligible=False,
        statuses={n:{'state':s} for n,s in [('saved_fields','passed'),('convergence','not_met'),('physical_scope','pending')]},
        missing_evidence=['total flux'],metrics=[{'value':None}],provenance={'audit_sha256':hashlib.sha256((folder/'audit.json').read_bytes()).hexdigest()})
    (folder/'report.json').write_text(json.dumps(report),encoding='utf-8');(folder/'raw.txt').write_text('raw record',encoding='utf-8')
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    (folder/'manifest.json').write_text(json.dumps(dict(schema_version=1,report_id=key,files_sha256=files)),encoding='utf-8')
    return folder

def test_missing_evidence_stays_null_and_statuses_are_independent(tmp_path):
    pack(tmp_path);s=ReviewStore(tmp_path);r=s.get('m16_test')
    assert r['metrics'][0]['value'] is None
    assert r['statuses']['saved_fields']['state']=='passed'
    assert r['statuses']['convergence']['state']=='not_met'
    assert r['scientific_eligible'] is False
    assert s.file('m16_test','raw.txt').read_text()=='raw record'

def test_tampering_revokes_catalog_availability_and_download(tmp_path):
    p=pack(tmp_path);(p/'raw.txt').write_text('modified')
    s=ReviewStore(tmp_path)
    assert not s.catalog()[0]['available']
    with pytest.raises(ValueError,match='完整性'):s.get('m16_test')
    with pytest.raises(ValueError):s.file('m16_test','raw.txt')

@pytest.mark.parametrize('key',['../m16_test','%2e%2e','/tmp','m16_test/../../x','x<script>'])
def test_report_ids_cannot_escape_fixed_root(tmp_path,key):
    pack(tmp_path)
    with pytest.raises(ValueError):ReviewStore(tmp_path).get(key)

def test_empty_store_has_no_generated_or_placeholder_results(tmp_path):
    assert ReviewStore(tmp_path/'missing').catalog()==[]

def test_summary_cannot_point_to_another_audit_even_with_valid_file_hashes(tmp_path):
    folder=pack(tmp_path);r=json.loads((folder/'report.json').read_text());r['provenance']['audit_sha256']='0'*64
    (folder/'report.json').write_text(json.dumps(r))
    m=json.loads((folder/'manifest.json').read_text());m['files_sha256']['report.json']=hashlib.sha256((folder/'report.json').read_bytes()).hexdigest()
    (folder/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='来源'):ReviewStore(tmp_path).get('m16_test')

def test_installed_bundle_preserves_its_source_and_refuses_overwrite(tmp_path):
    import zipfile
    from nozzle_platform.review_reports import install_review
    source=pack(tmp_path/'source');report=json.loads((source/'report.json').read_text());report['report_id']='installed'
    folder=install_review(tmp_path/'reports',report,{'audit.json':source/'audit.json','raw.txt':source/'raw.txt'})
    assert ReviewStore(tmp_path/'reports').get('installed')['metrics'][0]['value'] is None
    with zipfile.ZipFile(folder/'review_bundle.zip') as z:
        m=json.loads(z.read('manifest.json'))
        for n,d in m['files_sha256'].items():assert hashlib.sha256(z.read(n)).hexdigest()==d
    with pytest.raises(ValueError,match='Preserve'):install_review(tmp_path/'reports',report,{'audit.json':source/'audit.json'})

def test_http_report_page_routes_use_checked_bundles(tmp_path):
    pack(tmp_path/'verification_reports')
    server=PlatformServer(('127.0.0.1',0),tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def get(path,host=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        c.request('GET',path,headers={'Host':host or '127.0.0.1:'+str(server.server_port)})
        r=c.getresponse();result=r.status,r.read();c.close();return result
    try:
        assert get('/validation.html')[0]==200 and get('/validation.js')[0]==200
        assert json.loads(get('/api/verification-reports')[1])['reports'][0]['available']
        assert get('/verification-files/m16_test/raw.txt')==(200,b'raw record')
        assert get('/verification-files/m16_test/../manifest.json')[0]==404
        assert get('/api/verification-reports','example.com')[0]==403
        (tmp_path/'verification_reports/m16_test/raw.txt').write_text('changed')
        assert get('/api/verification-reports/m16_test')[0]==404
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
