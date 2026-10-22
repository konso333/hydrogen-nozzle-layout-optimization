"""A study export must bind every nested file to its verified child result."""
import json
import zipfile

import pytest

from nozzle_platform.workflows import WorkflowStore,sha256
from tests.test_platform_domains import stores,config


def test_study_export_checks_nested_artifacts_and_uses_canonical_reports(stores,tmp_path):
    _,designs,scheme,domains=stores
    domain=domains.create(config(scheme));store=WorkflowStore(tmp_path/'w',domains)
    child=tmp_path/'child';child.mkdir()
    (child/'flow.msh').write_bytes(b'synthetic mesh bytes for export integrity test')
    (child/'job.json').write_text('{"private_local_settings":"do not export"}')
    (child/'report.json').write_text('{"forged":true}')
    mesh=dict(kind='mesh',result_id='child',domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'],
        artifact_sha256={'flow.msh':sha256(child/'flow.msh')})
    parent=tmp_path/'study';parent.mkdir()
    (parent/'comparison.json').write_text('{"pressure_sensitivity_passed":false}')
    study=dict(kind='study',result_id='study',domain_id=domain['domain_id'],scheme_id=scheme['scheme_id'],
        rows=[dict(level='fine',mesh_id='child')],artifact_sha256={'comparison.json':sha256(parent/'comparison.json')})
    store.results.update(child=mesh,study=study);store.folders.update(child=child,study=parent)
    result=store.export('study',domain['domain_id'],scheme['scheme_id'])
    bundle=designs.downloads[result['download_url'].rsplit('/',1)[-1]]
    with zipfile.ZipFile(bundle) as z:
        assert z.read('fine/mesh/flow.msh')==(child/'flow.msh').read_bytes()
        assert json.loads(z.read('fine/mesh/report.json'))==mesh
        assert json.loads(z.read('report.json'))==study
        assert not any(name.endswith('job.json') for name in z.namelist())
    with pytest.raises(ValueError):store.export('study','other',scheme['scheme_id'])
    before=len(designs.downloads)
    (child/'flow.msh').write_bytes(b'changed after study completed')
    with pytest.raises(ValueError,match='文件已改变'):
        store.export('study',domain['domain_id'],scheme['scheme_id'])
    assert len(designs.downloads)==before
