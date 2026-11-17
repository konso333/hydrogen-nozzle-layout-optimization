"""Compare immutable saved geometry, without regenerating positions or running CFD."""
import csv
import hashlib
import io
import math
import uuid
import zipfile

from nozzle_platform.geometry_library import geometry_files
from nozzle_platform.geometry_records import canonical, digest

SCOPE = '仅比较已保存的几何，不计算燃烧、流量或压差，不评选物理性能最优方案。'
MAX_RECORDS = 4


def selected_ids(payload, *, exporting=False):
    fields = {'entry_ids', 'comparison_id'} if exporting else {'entry_ids'}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('请选择 2～4 条几何记录进行比较。')
    ids = payload['entry_ids']
    if (not isinstance(ids, list) or not 2 <= len(ids) <= MAX_RECORDS
            or any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids)):
        raise ValueError('请选择 2～4 条不同的几何记录进行比较。')
    return ids


def compare(library, payload):
    records = []
    for key in selected_ids(payload):
        entry, scheme = library.read(key)
        records.append(dict(entry=entry, scheme=scheme))
    requests = [r['scheme']['request'] for r in records]
    areas = [r['scheme']['summary']['boundary_area_mm2'] for r in records]

    def same(fields):
        return all(all(r[f] == requests[0][f] for f in fields) for r in requests[1:])

    def boundary(r):
        return (r['region'], r['radius_mm'],
                (r['start_angle_deg'] % 360, r['sweep_angle_deg']) if r['region'] == 'sector' else None)

    conditions = dict(
        same_model_reference=same(['model_id']),
        same_nozzle_count=same(['N']),
        same_installation_area=all(math.isclose(a, areas[0], rel_tol=1e-10, abs_tol=1e-9) for a in areas[1:]),
        same_boundary=all(boundary(r) == boundary(requests[0]) for r in requests[1:]),
        same_envelope_and_clearances=same(['diameter_mm', 'edge_gap_mm', 'wall_clearance_mm']),
    )
    labels = dict(same_model_reference='模型或安装参考坐标不同', same_nozzle_count='实际喷嘴数量不同',
                  same_installation_area='安装面积不同', same_boundary='安装边界形状、尺寸或方向不同',
                  same_envelope_and_clearances='包络直径或间隙约束不同')
    warnings = [labels[key] for key, value in conditions.items() if not value]
    rows = []
    for record in records:
        entry, scheme = record['entry'], record['scheme']
        r, summary = scheme['request'], scheme['summary']
        assistance = scheme.get('design_assistance', {})
        original = assistance.get('requested_design', r) if isinstance(assistance, dict) else None
        if (not isinstance(original, dict) or type(original.get('N')) is not int or original['N'] < 1
                or type(original.get('radius_mm')) not in (int, float)
                or not math.isfinite(original['radius_mm']) or original['radius_mm'] <= 0):
            raise ValueError('方案的原目标记录不完整，请检查保存记录。')
        rows.append(dict(entry_id=entry['entry_id'], name=entry['name'], model_name=scheme['model'].get('name', 'STEP'),
                         model_id=r['model_id'], original_N=original['N'], N=r['N'], family=r['family'],
                         region=r['region'], radius_mm=r['radius_mm'], start_angle_deg=r['start_angle_deg'],
                         sweep_angle_deg=r['sweep_angle_deg'], original_radius_mm=original['radius_mm'],
                         diameter_mm=r['diameter_mm'], edge_gap_mm=r['edge_gap_mm'], wall_clearance_mm=r['wall_clearance_mm'],
                         **{key: summary[key] for key in ('boundary_area_mm2', 'minimum_center_distance_mm',
                                                          'minimum_edge_gap_mm', 'minimum_wall_clearance_mm')},
                         same_coordinates_as=next((other['entry']['entry_id'] for other in records[:len(rows)]
                                                   if other['entry']['geometry_key'] == entry['geometry_key']), None)))
    result = dict(schema_version=1, entry_ids=list(payload['entry_ids']), conditions=conditions, warnings=warnings,
                  rows=rows, records=records, scope=SCOPE, solver_started=False, scientific_eligible=False,
                  fuel_heat_input_comparison='not_evaluated')
    result['comparison_id'] = digest(result)
    return result


def spreadsheet_value(value):
    # CSV is opened in spreadsheets; saved names and model filenames are user input.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def export(library, payload):
    ids = selected_ids(payload, exporting=True)
    result = compare(library, dict(entry_ids=ids))
    if result['comparison_id'] != payload['comparison_id']:
        raise ValueError('比较记录已改变，请重新比较后再导出。')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(result['rows'][0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows({k: spreadsheet_value(v) for k, v in row.items()} for row in result['rows'])
    files = {'comparison.json': canonical(result).encode('utf-8'),
             'comparison.csv': stream.getvalue().encode('utf-8-sig'),
             'README.txt': ('已保存几何方案比较\n' + SCOPE + '\n'
                            'comparison.json 包含条件差异与原始记录，comparison.csv 包含几何指标。\n'
                            '各方案目录保留原坐标、二维图和参数；不含单喷嘴STEP、装配STEP、网格或CFD。\n'
                            '原目标来自保存记录，采用后的实际数量单独列出。相同面积不代表相同边界。\n'
                            '相同燃料热输入尚未在此核对；需另行配置供气并经真实仿真验证。\n'
                            'CSV用户文本以单引号防止被表格软件当作公式，JSON保留原文。\n').encode('utf-8')}
    for index, record in enumerate(result['records'], 1):
        prefix = f'scheme_{index:02d}/'
        files.update({prefix + name: raw for name, raw in geometry_files(record['scheme']).items()})
        files[prefix + 'entry.json'] = canonical(record['entry']).encode('utf-8')
    files['manifest.json'] = canonical({name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}).encode('utf-8')
    directory = library.root / 'downloads'
    directory.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    path = directory / (token + '.zip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    with library.designs.lock:
        library.designs.downloads[token] = path
    return dict(download_url='/downloads/' + token, filename='saved_geometry_comparison_' + result['comparison_id'][:10] + '.zip',
                solver_started=False)
