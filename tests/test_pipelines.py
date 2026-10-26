"""Controller tests use explicitly synthetic CAD/solver seams, never CFD evidence."""
import copy
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
import zipfile
import pytest

from nozzle_platform.design import DesignStore
from nozzle_platform.domains import DomainStore
from nozzle_platform.workflows import WorkflowStore,sha256,save
from nozzle_platform.pipelines import PipelineStore,normalize,heat_velocity
from tests.test_platform import request as design_request
from tests.test_platform_domains import model as model_fixture,config as domain_config


def request():
    domain=domain_config({'scheme_id':'planned'});del domain['scheme_id']
    return dict(design=design_request(),domain=domain,
                mesh=dict(min_size_mm=.04,max_size_mm=2.,inlet_size_mm=.1,refine_distance_mm=2.),
                flow=dict(fuel_velocity_m_s=1.,oxidizer_velocity_m_s=1.,temperature_K=300.,oxidizer_o2_mass_fraction=.233,
                          iterations=25,convergence_mode='gpu-validation',time_budget_seconds=240,compute_device='gpu',transport_model='mass-weighted-mixing-law'),
                comparison=dict(basis='single',cases=[],fuel_lhv_MJ_kg=120.),section=dict(axis='y',position_mm=0.))


class Models:
    def __init__(self,root):
        self.root=root;root.mkdir();self.gmsh_path=None
        source=b'SYNTHETIC TEST STEP, NOT REAL CAD';self.source_id=hashlib.sha256(source).hexdigest();(root/(self.source_id+'.step')).write_bytes(source)
    def get(self,key):
        assert key=='test-model';return dict(model_fixture(),source_id=self.source_id)
    def export_array(self,model_id,coords,path,*,cancel):
        path.write_text('SYNTHETIC ASSEMBLY');return {'verified':True}
    def cad(self,job,*,cancel=None):
        out=Path(job['output_directory']);out.mkdir()
        for n in ['fluid.step','fluid.brep','fluid.geo']:(out/n).write_text('SYNTHETIC DOMAIN')
        return dict(connected_fluid_components=1,volume_mm3=100.,boundary_patch_counts={},
                    boundary_area_mm2={'fuel_inlet':len(job['coordinates'])*10.},vertices=[],triangles=[],triangle_roles=[])


class Workflows(WorkflowStore):
    def __init__(self,root,domains):
        super().__init__(root,domains);self.calls=[];self.fail_mesh=False;self.mesh_area_factor=1.
    def capability(self):return dict(fluent_available=True)
    def _start(self,kind,config,domain,source,mesh=None,*,owner=None):
        assert owner==self.reservation
        self.calls.append((kind,copy.deepcopy(config)));key=uuid.uuid4().hex;out=self.root/(kind+'_'+key);out.mkdir()
        names=['flow.msh'] if kind=='mesh' else ['final.cas.h5','final.dat.h5']
        for n in names:(out/n).write_text('SYNTHETIC SOLVER TEST FILE, NOT CFD DATA')
        result=dict(kind=kind,result_id=key,domain_id=domain['domain_id'],scheme_id=domain['scheme_id'],
                    artifact_sha256={n:sha256(out/n) for n in names})
        if kind=='mesh':result.update(cells=256,boundary_mesh_area_m2={'fuel_inlet':domain['boundary_area_mm2']['fuel_inlet']*1e-6*self.mesh_area_factor})
        else:
            density=101325*2.01594/(8314.46261815324*config['temperature_K'])
            fuel=density*config['fuel_velocity_m_s']*mesh['boundary_mesh_area_m2']['fuel_inlet']
            result.update(iterations_completed=config['iterations'],numerical_criteria_met=False,gpu_validation_only=True,
                          history=[dict(mass_flow_kg_s={'fuel_inlet':fuel,'oxidizer_inlet':1e-5},area_weighted_total_pressure_drop_Pa={'fuel_inlet':1.,'oxidizer_inlet':2.})])
        self.jobs[key]=dict(job_id=key,status='failed' if self.fail_mesh and kind=='mesh' else 'completed',message='SYNTHETIC MESH FAILURE' if self.fail_mesh else 'SYNTHETIC COMPLETED',result=result)
        self.results[key]=result;self.folders[key]=out
        return copy.deepcopy(self.jobs[key])


@pytest.fixture
def store(tmp_path,monkeypatch):
    models=Models(tmp_path/'models');designs=DesignStore(tmp_path/'designs',models);domains=DomainStore(tmp_path/'domains',models,designs)
    workflows=Workflows(tmp_path/'workflows',domains);store=PipelineStore(tmp_path/'pipelines',designs,domains,workflows)
    monkeypatch.setattr(store,'resources',lambda:None)
    def post(state,row,spec,cancel,suffix):
        if spec['operation']=='extract':
            out=Path(spec['output']);out.mkdir();(out/'section.npz').write_bytes(b'SYNTHETIC');(out/'section_values.csv').write_text('SYNTHETIC')
            return dict(source='SYNTHETIC TEST')
        for out in spec['folders']:
            out=Path(out);images={}
            for n in ['speed.png','pressure.png','temperature.png','h2.png']:(out/n).write_bytes(b'SYNTHETIC PNG TEST');images[n]=sha256(out/n)
            save(out/'maps.json',dict(images_sha256=images))
        return {}
    monkeypatch.setattr(store,'post',post)
    return store


def finished(store,request):
    start=store.start(request);end=time.monotonic()+8
    while store.workflows.reservation is not None and time.monotonic()<end:time.sleep(.01)
    assert store.workflows.reservation is None,'Synthetic controller did not finish'
    return store.status(start['job_id'])


def test_serial_one_click_comparison_keeps_unconverged_results_and_verifies_heat(store):
    config=request();config['comparison'].update(basis='same-heat',cases=[{'N':7,'family':'honeycomb'}],fuel_heat_input_kW=.08)
    task=finished(store,config)
    assert task['status']=='completed' and task['comparison_rule_verified']
    assert [r['status'] for r in task['cases']]==['not_converged','not_converged']
    assert [k for k,_ in store.workflows.calls]==['mesh','flow','mesh','flow']
    assert all(r['actual_fuel_heat_input_kW']==pytest.approx(.08) for r in task['cases'])
    assert not task['comparison_eligible'] and task['recommended_case_id'] is None
    assert all(all(s['status']=='completed' for s in r['stages']) for r in task['cases'])
    assert any(f['name']=='case1_final.cas.h5' for f in task['files'])
    assert '不提供正式方案排名' in store.file(task['job_id'],'report.md').read_text(encoding='utf-8')
    output=store.export(task['job_id']);archive=store.file(task['job_id'],'results.zip')
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert 'case1_final.cas.h5' in manifest and 'case2_speed.png' in manifest
        for name,digest in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==digest


def test_infeasible_design_stops_that_case_but_preserves_other_cases(store):
    config=request();config['design']['radius_mm']=20
    config['comparison'].update(basis='same-area',cases=[{'N':1,'family':'honeycomb'}])
    task=finished(store,config)
    assert task['status']=='completed_with_failures'
    assert task['cases'][0]['status']=='failed' and task['cases'][0]['stages'][1]['status']=='skipped'
    assert task['cases'][1]['status']=='not_converged'
    assert [k for k,_ in store.workflows.calls]==['mesh','flow']


def test_mesh_failure_never_launches_fluent_and_coordinates_still_export(store):
    store.workflows.fail_mesh=True;task=finished(store,request())
    assert [k for k,_ in store.workflows.calls]==['mesh']
    assert task['cases'][0]['error']=='SYNTHETIC MESH FAILURE'
    assert task['cases'][0]['stages'][4]['status']=='skipped'
    assert any(f['name']=='case1_coordinates.csv' for f in task['files'])


def test_failed_flow_exports_unverified_pair_without_success_or_maps(store,monkeypatch):
    original=store.workflows._start
    def fail(kind,*args,**kwargs):
        child=original(kind,*args,**kwargs)
        if kind=='flow':
            task=store.workflows.jobs[child['job_id']];task.update(status='failed',message='SYNTHETIC saved-field failure',diagnostics={'iterations_completed':25})
        return child
    monkeypatch.setattr(store.workflows,'_start',fail)
    task=finished(store,request());row=task['cases'][0]
    assert row['status']=='failed' and row['iterations']==25 and not task['comparison_eligible']
    store.export(task['job_id'])
    with zipfile.ZipFile(store.file(task['job_id'],'results.zip')) as bundle:
        assert 'case1_unverified_flow_final.cas.h5' in bundle.namelist()
        assert 'case1_unverified_flow_final.dat.h5' in bundle.namelist()
        assert 'diagnostic_README.txt' in bundle.namelist()
        assert 'case1_final.cas.h5' not in bundle.namelist() and 'case1_speed.png' not in bundle.namelist()


def test_old_failed_record_collects_diagnostic_files_on_first_export_without_rewriting_result(store):
    store.workflows.fail_mesh=True;task=finished(store,request());key=task['job_id'];row=task['cases'][0]
    child_folder=store.workflows.root/('mesh_'+row['mesh_job_id']);(child_folder/'cleanup.json').write_text('{"all_tracked_processes_exited":true}')
    summary=store.file(key,'summary.json').read_bytes();old_message=task['message'];old_error=row['error']
    restored=PipelineStore(store.root,store.designs,store.domains,Workflows(store.workflows.root,store.domains));restored.export(key)
    fresh=restored.status(key)
    assert fresh['message']==old_message and fresh['cases'][0]['error']==old_error
    assert restored.file(key,'summary.json').read_bytes()==summary
    assert any(f['name']=='case1_unverified_mesh_cleanup.json' for f in fresh['files'])


def test_cancel_during_assembly_does_not_start_domain_mesh_or_solver(store,monkeypatch):
    original=store.designs.models.export_array
    def stop(*a,**kw):
        result=original(*a,**kw);kw['cancel'].set();return result
    monkeypatch.setattr(store.designs.models,'export_array',stop)
    task=finished(store,request())
    assert task['status']=='cancelled' and task['cases'][0]['status']=='cancelled'
    assert not store.workflows.calls and any(f['name']=='case1_assembly.step' for f in task['files'])


def test_history_survives_server_restart_and_corrupted_download_fails(store):
    task=finished(store,request());other=Workflows(store.workflows.root,store.domains)
    restored=PipelineStore(store.root,store.designs,store.domains,other)
    assert restored.status(task['job_id'])['status']=='completed'
    path=restored.file(task['job_id'],'case1_coordinates.csv');path.write_text('changed')
    with pytest.raises(ValueError,match='哈希'):restored.file(task['job_id'],'case1_coordinates.csv')
    with pytest.raises(ValueError):restored.file(task['job_id'],'../../models/secret')


def test_interrupted_history_never_restarts_native_jobs(store):
    key='a'*32;folder=store.root/('run_'+key);folder.mkdir();save(folder/'task.json',dict(job_id=key,status='running',message='old',files={}))
    other=Workflows(store.workflows.root,store.domains);restored=PipelineStore(store.root,store.designs,store.domains,other)
    assert restored.status(key)['status']=='interrupted' and not other.calls and other.reservation is None


def test_restored_history_uses_start_time_instead_of_directory_iteration_order(store):
    for key,when in [('a'*32,200),('b'*32,100)]:
        folder=store.root/('run_'+key);folder.mkdir();save(folder/'task.json',dict(job_id=key,status='completed',message='historical',files={},started_at=when))
    other=Workflows(store.workflows.root,store.domains);restored=PipelineStore(store.root,store.designs,store.domains,other)
    assert [r['job_id'] for r in restored.catalog()]==['a'*32,'b'*32]


def test_original_request_is_frozen_and_active_task_cannot_be_queued_twice(store,monkeypatch):
    import nozzle_platform.pipelines as module
    class HeldThread:
        def __init__(self,*a,**kw):pass
        def start(self):pass
    monkeypatch.setattr(module.threading,'Thread',HeldThread)
    config=request();task=store.start(config);config['design']['N']=1
    assert store.status(task['job_id'])['config']['design']['N']==9
    with pytest.raises(ValueError,match='已有'):store.start(request())


@pytest.mark.parametrize('fault',['duplicate','too_many','cpu_long','foreign_ids','unconfirmed','invalid_power'])
def test_bad_comparison_and_unsafe_launches_rejected_before_any_work(fault):
    config=request()
    if fault=='duplicate':config['comparison'].update(basis='same-area',cases=[{'N':9,'family':'rectangular'}])
    elif fault=='too_many':config['comparison'].update(basis='same-area',cases=[{'N':i,'family':'ring'} for i in range(1,4)])
    elif fault=='cpu_long':config['flow'].update(compute_device='cpu',transport_model='ideal-gas-mixing-law',convergence_mode='trial',time_budget_seconds=1800)
    elif fault=='foreign_ids':config['mesh']['domain_id']='historical'
    elif fault=='unconfirmed':config['domain']['template_confirmed']=False
    else:config['comparison'].update(basis='same-heat',cases=[{'N':7,'family':'ring'}],fuel_heat_input_kW=-1)
    with pytest.raises(ValueError):normalize(config)


def test_heat_rule_uses_actual_total_fuel_area_and_explicit_units():
    a=heat_velocity(.08,120,10,300);b=heat_velocity(.08,120,20,300)
    assert a['fuel_velocity_m_s']==pytest.approx(2*b['fuel_velocity_m_s'])
    assert a['target_fuel_mass_flow_kg_s']==pytest.approx(.08/(120*1000))


def test_heat_rule_corrects_mesh_vs_cad_area_difference_before_solving(store):
    store.workflows.mesh_area_factor=.985
    config=request();config['comparison'].update(basis='same-heat',cases=[{'N':7,'family':'honeycomb'}],fuel_heat_input_kW=.08)
    task=finished(store,config)
    assert task['comparison_rule_verified']
    assert all(r['actual_fuel_heat_input_kW']==pytest.approx(.08) for r in task['cases'])


def test_thread_start_failure_releases_machine_reservation(store,monkeypatch):
    import nozzle_platform.pipelines as module
    def fail(thread):raise RuntimeError('thread unavailable')
    monkeypatch.setattr(module.threading.Thread,'start',fail)
    with pytest.raises(ValueError,match='未启动'):store.start(request())
    assert store.workflows.reservation is None
    assert store.catalog()[0]['status']=='failed'


@pytest.mark.parametrize('origin,has_input',[(None,False),([1.,2.,3.],False),([1,2,3],True),([1,2,3],False)])
def test_restore_model_source_preserves_axis_and_origin_mode(store,tmp_path,monkeypatch,origin,has_input):
    from nozzle_platform.models import ModelStore,MODEL_ENGINE,digest
    task=finished(store,request());key=task['job_id']
    models=ModelStore(tmp_path/'restored_models')
    raw=b'ISO-10303-21;\n#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));\nEND-ISO-10303-21;'
    source_id=digest(raw);path=store.root/'saved_model.step';path.write_bytes(raw)
    model_id=digest(json.dumps([MODEL_ENGINE,source_id,'z',origin],sort_keys=True).encode())
    state=store.workflows.status(key);state['model_reference']=dict(source_id=source_id,model_id=model_id,axis='z',origin_mm=origin or [4.,5.,6.],model_engine=MODEL_ENGINE,name='fixture.step')
    if origin is not None:state['model_reference']['origin_mm']=[float(v) for v in origin]
    if has_input:state['model_reference']['origin_input_mm']=origin
    def load(source_id,*,axis,origin):
        return dict(model_id=digest(json.dumps([MODEL_ENGINE,source_id,axis,origin],sort_keys=True).encode()),origin_input_mm=origin)
    monkeypatch.setattr(models,'load',load)
    store.add_file(state,path,'single_nozzle.step');store.publish(state);store.designs.models=models
    restored=store.restore_source(key)
    assert restored['source']['source_id']==source_id
    assert models.catalog()[0]['default_axis']=='z'
    assert restored['source']['default_origin_mm']==origin
    assert json.dumps(restored['source']['default_origin_mm'])==json.dumps(origin)
    assert restored['model']['model_id']==model_id
