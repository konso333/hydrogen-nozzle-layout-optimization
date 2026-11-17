"""Saved comparison uses checked coordinates and exposes differing conditions."""
import copy
import csv
import hashlib
import io
import json
import math
import zipfile

import pytest

from nozzle_platform.geometry_library import geometry_files
from nozzle_platform.layout_advisor import annotate
from nozzle_platform.saved_comparison import compare, export
from tests.test_geometry_workspace import library, scheme, ReferenceModels
from tests.test_platform import call, request, server
from nozzle_platform.design import DesignStore
from nozzle_platform.geometry_library import GeometryLibrary
from nozzle_platform.models import MODEL_ENGINE, digest


def saved(lib, designs, models, name='方案', **changes):
    item = scheme(designs, models, **changes)
    key = lib.save(dict(scheme_id=item['scheme_id'], name=name))['entry']['entry_id']
    return key, item


def test_comparison_keeps_original_coordinates_and_duplicate_record_identity(library):
    lib, designs, models = library
    a, first = saved(lib, designs, models, '甲', N=11, radius_mm=60)
    b, second = saved(lib, designs, models, '乙', N=11, radius_mm=60)
    before = copy.deepcopy(designs.schemes)
    result = compare(lib, dict(entry_ids=[b, a]))
    assert all(result['conditions'].values()) and not result['warnings']
    assert result['entry_ids'] == [b, a] and result['rows'][1]['same_coordinates_as'] == b
    assert result['records'][0]['scheme'] == second and result['records'][1]['scheme'] == first
    assert result['scientific_eligible'] is False and result['solver_started'] is False
    assert result['fuel_heat_input_comparison'] == 'not_evaluated'
    assert designs.schemes == before and models.loads == 0


def test_same_area_different_boundary_and_count_are_separate_conditions(library):
    lib, designs, models = library
    a, _ = saved(lib, designs, models, '圆形', N=11, radius_mm=60)
    b, _ = saved(lib, designs, models, '等面积扇形', N=7, radius_mm=60 * math.sqrt(2), region='sector', sweep_angle_deg=180)
    result = compare(lib, dict(entry_ids=[a, b]))
    assert result['conditions']['same_installation_area']
    assert not result['conditions']['same_boundary'] and not result['conditions']['same_nozzle_count']
    assert len(result['warnings']) == 2 and result['rows'][1]['same_coordinates_as'] is None


def test_different_constraints_area_and_reference_are_reported(library):
    lib, designs, models = library
    a, _ = saved(lib, designs, models, '原参考', N=1)
    models.value['origin_input_mm'] = [1, 2, 3]
    models.value['model_id'] = digest(json.dumps([MODEL_ENGINE, models.value['source_id'], 'y', [1, 2, 3]], sort_keys=True).encode())
    b, _ = saved(lib, designs, models, '新参考', N=1, radius_mm=50, edge_gap_mm=2)
    result = compare(lib, dict(entry_ids=[a, b]))
    assert not result['conditions']['same_model_reference']
    assert not result['conditions']['same_installation_area']
    assert not result['conditions']['same_envelope_and_clearances']
    assert all(row['minimum_center_distance_mm'] is None and row['minimum_edge_gap_mm'] is None for row in result['rows'])


def test_adopted_quantity_keeps_original_goal_and_actual_values(library):
    lib, designs, models = library
    direct, _ = saved(lib, designs, models, '直接排9', N=9)
    original = request(model_id=models.value['model_id'], N=50)
    report = designs.advise(dict(design=original, explore_families=True))
    option = next(s for s in report['suggestions'] if s['kind'] == 'quantity')
    adopted = designs.adopt(dict(advice_id=report['advice_id'], suggestion_id=option['suggestion_id'], current_design=original))
    key = lib.save(dict(scheme_id=adopted['scheme_id'], name='从50调整'))['entry']['entry_id']
    result = compare(lib, dict(entry_ids=[direct, key]))
    assert result['rows'][1]['original_N'] == 50
    assert result['rows'][1]['N'] == adopted['request']['N'] < 50
    assert result['records'][1]['scheme'] == adopted


@pytest.mark.parametrize('payload', [{}, {'entry_ids': []}, {'entry_ids': ['a']}, {'entry_ids': ['a'] * 2},
                                   {'entry_ids': ['a', 'b', 'c', 'd', 'e']}, {'entry_ids': [None, 'b']},
                                   {'entry_ids': 'ab'}, {'entry_ids': ['a', 'b'], 'extra': True},
                                   {'entry_ids': ['../source', 'b']}, None])
def test_bad_selection_rejected_without_loading_model(library, payload):
    lib, designs, models = library
    with pytest.raises(ValueError):
        compare(lib, payload)
    assert models.loads == 0 and not designs.schemes


@pytest.mark.parametrize('corruption', ['source', 'coordinates'])
def test_corruption_prevents_both_compare_and_export(library, corruption):
    lib, designs, models = library
    a, _ = saved(lib, designs, models, '甲', N=3)
    b, _ = saved(lib, designs, models, '乙', N=4)
    result = compare(lib, dict(entry_ids=[a, b]))
    if corruption == 'source':
        (models.root / (models.value['source_id'] + '.step')).write_bytes(b'broken')
    else:
        (lib.folder(a) / 'coordinates.csv').write_bytes(b'broken')
    with pytest.raises(ValueError):
        compare(lib, dict(entry_ids=[a, b]))
    with pytest.raises(ValueError):
        export(lib, dict(entry_ids=[a, b], comparison_id=result['comparison_id']))
    assert not designs.downloads


def test_export_checks_snapshot_and_contains_exact_original_data(library):
    lib, designs, models = library
    a, first = saved(lib, designs, models, '=恶意公式', N=3)
    b, second = saved(lib, designs, models, '乙', N=4)
    result = compare(lib, dict(entry_ids=[a, b]))
    with pytest.raises(ValueError, match='重新比较'):
        export(lib, dict(entry_ids=[a, b], comparison_id='0' * 64))
    with pytest.raises(ValueError):
        export(lib, dict(entry_ids=[a, b]))
    response = export(lib, dict(entry_ids=[a, b], comparison_id=result['comparison_id']))
    path = designs.downloads[response['download_url'].rsplit('/', 1)[1]]
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert set(archive.namelist()) == set(manifest) | {'manifest.json'}
        for name, h in manifest.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == h
        assert json.loads(archive.read('comparison.json')) == result
        rows = list(csv.DictReader(io.StringIO(archive.read('comparison.csv').decode('utf-8-sig'))))
        assert [int(row['N']) for row in rows] == [3, 4]
        assert rows[0]['name'] == "'=恶意公式"
        assert result['rows'][0]['name'] == '=恶意公式'
        for index, item in enumerate((first, second), 1):
            for name, raw in geometry_files(item).items():
                assert archive.read(f'scheme_{index:02d}/' + name) == raw
        assert not any(name.endswith(('.step', '.msh', '.cas', '.dat')) for name in archive.namelist())


def test_four_records_allowed_and_order_changes_snapshot(library):
    lib, designs, models = library
    ids = [saved(lib, designs, models, str(n), N=n)[0] for n in range(1, 5)]
    report = compare(lib, dict(entry_ids=ids))
    assert len(report['rows']) == 4
    assert compare(lib, dict(entry_ids=list(reversed(ids))))['comparison_id'] != report['comparison_id']


def test_incomplete_original_goal_is_actionable(library):
    lib, designs, models = library
    a, first = saved(lib, designs, models, '甲')
    changed = copy.deepcopy(first)
    changed['design_assistance'] = dict(requested_design={'N': 50})
    designs.schemes[changed['scheme_id']] = changed
    b = lib.save(dict(scheme_id=changed['scheme_id'], name='元信息缺失'))['entry']['entry_id']
    with pytest.raises(ValueError, match='原目标记录不完整'):
        compare(lib, dict(entry_ids=[a, b]))


def test_http_comparison_is_read_only_and_origin_checked(server, tmp_path):
    models = ReferenceModels(tmp_path / 'references')
    designs = DesignStore(tmp_path / 'designs', models)
    lib = GeometryLibrary(tmp_path / 'records', designs)
    server.geometry_library = lib
    server.designs = designs
    a, _ = saved(lib, designs, models, '甲', N=3)
    b, _ = saved(lib, designs, models, '乙', N=4)
    headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.server_port}'}
    def post(route, data, origin=headers):
        status, raw, _ = call(server, 'POST', route, json.dumps(data).encode(), origin)
        return status, json.loads(raw)
    assert post('/api/geometry-library-compare', dict(entry_ids=[a, b]), {})[0] == 403
    # Pure geometry comparison does not load/switch models or use the workflow slot.
    server.workflows.active = 'synthetic-test-only'
    status, result = post('/api/geometry-library-compare', dict(entry_ids=[a, b]))
    assert status == 200 and result['solver_started'] is False
    status, package = post('/api/geometry-library-compare-export', dict(entry_ids=[a, b], comparison_id=result['comparison_id']))
    assert status == 200 and call(server, 'GET', package['download_url'])[0] == 200
    assert models.loads == 0
    server.workflows.active = None
