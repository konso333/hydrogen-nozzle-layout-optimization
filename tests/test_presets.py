"""Preset persistence and reference identity, with synthetic CAD only."""
import copy
import json
from pathlib import Path
import pytest
from nozzle_platform.models import ModelStore
from nozzle_platform.presets import PresetStore,settings,model_key
from tests.test_pipelines import request

RAW=b'ISO-10303-21;\n#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));\nEND-ISO-10303-21;'


def models(root):
    store=ModelStore(root);store.calls=[]
    def cad(job):
        store.calls.append(job)
        return dict(axis=job['axis'],origin_mm=[float(v) for v in job['origin']] if job['origin'] is not None else [1.,2.,3.],
                    bounds_mm=[[-10,-10,-28],[10,10,4]],envelope_diameter_mm=20,solid_count=1,triangle_count=12,vertices=[],triangles=[])
    store.cad=cad
    return store


@pytest.mark.parametrize('origin',[None,[0,0,0],[0.,0.,0.]])
@pytest.mark.parametrize('profile',[None,'strict-linear'])
def test_preset_survives_restart_and_preserves_original_reference_types(tmp_path,origin,profile):
    m=models(tmp_path/'models');source=m.add_source('fixture.step',RAW);model=m.load(source['source_id'],axis='z',origin=origin)
    config=request();config['design']['model_id']=model['model_id']
    if profile:config['flow']['species_solver_control']=profile
    original=copy.deepcopy(config)
    presets=PresetStore(tmp_path/'presets',m);saved=presets.store('整套冷流',config)
    assert config==original and len(m.calls)==1,'Saving settings must not run CAD'
    fresh=models(tmp_path/'new_models');restored=PresetStore(presets.root,fresh).load(saved['preset_id'])
    assert restored['model']['model_id']==model['model_id']
    assert restored['model']['axis']=='z' and restored['model']['origin_input_mm']==origin
    assert restored['request']['domain']['template_confirmed'] is False
    assert restored['request']['flow']['compute_device']=='gpu'
    assert restored['request']['flow'].get('species_solver_control')==profile
    assert restored['request']['mesh']['min_size_mm']==config['mesh']['min_size_mm']
    assert restored['preset']['scientific_eligible'] is False


def saved_preset(tmp_path):
    m=models(tmp_path/'models');source=m.add_source('fixture.step',RAW);model=m.load(source['source_id'],axis='y',origin=[1,2,3])
    config=request();config['design']['model_id']=model['model_id'];store=PresetStore(tmp_path/'presets',m)
    return store,store.store('test',config)


def test_export_import_requires_matching_step_and_does_not_load_cad(tmp_path):
    original,record=saved_preset(tmp_path);document=json.loads(original.export(record['preset_id']).read_text(encoding='utf-8'))
    fresh=models(tmp_path/'new_models');store=PresetStore(tmp_path/'imported',fresh)
    with pytest.raises(ValueError,match='先导入'):store.import_settings(document)
    fresh.add_source('fixture.step',RAW);imported=store.import_settings(document)
    assert imported['preset_id']==record['preset_id'] and not fresh.calls
    assert store.read(imported['preset_id'])['request']['domain']['template_confirmed'] is False


@pytest.mark.parametrize('target',['preset.json','single_nozzle.step'])
def test_tampered_settings_or_model_revokes_load_and_export(tmp_path,target):
    store,record=saved_preset(tmp_path);folder=store.root/('preset_'+record['preset_id'])
    if target=='preset.json':
        document=json.loads((folder/target).read_text(encoding='utf-8'));document['request']['design']['N']=1
        (folder/target).write_text(json.dumps(document),encoding='utf-8')
    else:(folder/target).write_bytes(b'altered source')
    assert not store.catalog()[0]['available']
    with pytest.raises(ValueError):store.load(record['preset_id'])
    with pytest.raises(ValueError):store.export(record['preset_id'])


@pytest.mark.parametrize('bad',['../secret','a'*64+'/file','x',None])
def test_preset_identifiers_cannot_access_other_files(tmp_path,bad):
    store,record=saved_preset(tmp_path)
    with pytest.raises(ValueError):store.export(bad)


@pytest.mark.parametrize('fault',['unknown','executable','reference','version','origin','confirmation'])
def test_import_rejects_untrusted_instructions_and_inconsistent_reference(tmp_path,fault):
    store,record=saved_preset(tmp_path);d=store.read(record['preset_id'])
    if fault=='unknown':d['instructions']='run a command'
    elif fault=='executable':d['request']['flow']['executable']='arbitrary.exe'
    elif fault=='reference':d['model_reference']['axis']='x'
    elif fault=='version':d['schema_version']=True
    elif fault=='origin':d['model_reference']['origin_input_mm']=[True,0,0]
    else:d['request']['domain']['template_confirmed']=1
    with pytest.raises(ValueError):settings(d)


def test_real_b0_example_is_self_consistent_and_not_a_scientific_certificate():
    p=Path(__file__).parents[1]/'examples/b0_coldflow_preset.json';doc=settings(json.loads(p.read_text(encoding='utf-8')))
    assert doc['request']['design']['model_id']==model_key(doc['model_reference'])
    assert doc['request']['domain']['template_confirmed'] is False
    assert doc['request']['flow']['iterations']==100
    assert doc['request']['flow']['transport_model']=='mass-weighted-mixing-law'
    assert doc['request']['mesh']['cell_budget']==1500000


def test_builtin_is_not_offered_for_a_different_model(tmp_path):
    m=models(tmp_path/'models');m.add_source('other.step',RAW)
    store=PresetStore(tmp_path/'presets',m,builtin=Path(__file__).parents[1]/'examples/b0_coldflow_preset.json')
    assert store.catalog()==[]
