"""Independent constraints, conservative diagnosis and explicit adoption contracts."""
import copy,io,json,math,zipfile
import numpy as np
import pytest
from shape_boundary import MountingBoundary
from nozzle_platform.design import DesignStore,generate
from nozzle_platform.layout_advisor import advise
from nozzle_platform.layout_search import distances
from tests.test_platform import request,model,FakeModels,server,call

def independent_check(scheme):
    assert scheme['feasible'];r=scheme['request'];points=scheme['coordinates_mm'];assert len(points)==r['N'] and len({tuple(p) for p in points})==r['N']
    boundary=MountingBoundary(r['region'],r['radius_mm'],r['start_angle_deg'],r['sweep_angle_deg'])
    assert min(boundary.signed_distance_mm(p)-r['diameter_mm']/2 for p in points)>=r['wall_clearance_mm']-1e-9
    assert all(math.dist(p,q)>=r['diameter_mm']+r['edge_gap_mm']-1e-9 for i,p in enumerate(points) for q in points[i+1:])
    assert scheme['simulation_status']=='not_started' and not scheme['scientific_eligible']

@pytest.mark.parametrize('family',['rectangular','honeycomb','sunflower','ring'])
@pytest.mark.parametrize('N',[1,2,10,11,15,20,50,100,128])
def test_arbitrary_counts_not_only_perfect_squares(family,N):
    independent_check(generate(request(N=N,family=family,radius_mm=5000),model()))

@pytest.mark.parametrize('start,sweep',[(0,15),(330,100),(45,120),(0,180),(300,270),(12,359)])
def test_vectorized_distance_agrees_with_exact_finite_segment_geometry(start,sweep):
    b=MountingBoundary('sector',80,start,sweep);points=[(x,y) for x in range(-90,91,9) for y in range(-90,91,11)]
    assert np.allclose(distances(points,b),[b.signed_distance_mm(p) for p in points],rtol=1e-12,atol=1e-10)

@pytest.mark.parametrize('start,sweep',[(0,120),(330,100),(20,270)])
def test_twenty_nozzles_in_sector_and_replay(start,sweep):
    r=request(N=20,radius_mm=120,region='sector',start_angle_deg=start,sweep_angle_deg=sweep)
    result=advise(dict(design=r,explore_families=True),model());assert result['scheme'];independent_check(result['scheme'])
    assert result['scheme']['request']['N']==20 and result['scheme']['request']['radius_mm']==120
    assert result==advise(dict(design=r,explore_families=True),model())

def test_area_bound_is_only_upper_bound_and_every_suggestion_really_feasible():
    r=request(N=50,radius_mm=40);before=copy.deepcopy(r);result=advise(dict(design=r,explore_families=True),model())
    assert r==before and result['scheme'] is None and not result['global_capacity_proven'] and not result['solver_started']
    proof=next(d for d in result['diagnostics'] if d['code']=='area_bound_exceeded');assert proof['area_only_upper_bound']==16
    assert result['feasibility']=='proven_infeasible'
    assert {'quantity','radius'}<=set(s['kind'] for s in result['suggestions'])
    for suggestion in result['suggestions']:
        proposed=suggestion['request'];independent_check(generate(proposed,model()))
        assert all(proposed[k]==r[k] for k in ('diameter_mm','edge_gap_mm','wall_clearance_mm','region','start_angle_deg','sweep_angle_deg'))
        if suggestion['kind']=='radius':assert proposed['N']==50 and proposed['family']==r['family'] and proposed['radius_mm']>40
        if suggestion['kind']=='quantity':assert proposed['radius_mm']==40 and proposed['N']<50

def test_single_nozzle_cannot_fit_narrow_sector_has_specific_proven_reason():
    result=advise(dict(design=request(N=1,radius_mm=20,region='sector',sweep_angle_deg=30),explore_families=True),model())
    assert result['scheme'] is None and any(d['code']=='single_envelope_does_not_fit' and d['proven_necessary_condition'] for d in result['diagnostics'])

def test_cross_family_permission_and_original_input_remain_visible():
    r=request(N=20,radius_mm=100,region='sector',sweep_angle_deg=120)
    strict=advise(dict(design=r,explore_families=False),model());auto=advise(dict(design=r,explore_families=True),model())
    assert strict['scheme'] is None and any(s['kind']=='family' for s in strict['suggestions'])
    independent_check(auto['scheme']);assert auto['scheme']['request']['N']==20
    assert auto['scheme']['design_assistance']['requested_design']==r
    assert auto['scheme']['design_assistance']['quantity_changed'] is False

def test_manual_constraints_are_never_automatically_rewritten():
    r=request(N=10,radius_mm=40,spacing_mode='manual',rotation_deg=12)
    result=advise(dict(design=r,explore_families=True),model());assert result['scheme'] is None and not result['suggestions']
    assert any(x['code']=='manual_layout_fixed' for x in result['diagnostics']) and result['requested_design']==r
    assert result['feasibility']=='search_not_found'

def test_adoption_requires_current_snapshot_and_export_preserves_original_goal(tmp_path):
    store=DesignStore(tmp_path,FakeModels());original=request(N=50);result=store.advise(dict(design=original,explore_families=True))
    option=next(s for s in result['suggestions'] if s['kind']=='quantity')
    payload=dict(advice_id=result['advice_id'],suggestion_id=option['suggestion_id'],current_design=original)
    with pytest.raises(ValueError):store.adopt(dict(payload,current_design=dict(original,N=49)))
    with pytest.raises(ValueError):store.adopt(dict(payload,suggestion_id='missing'))
    assert not store.schemes
    adopted=store.adopt(payload);independent_check(adopted);assert adopted['design_assistance']['requested_design']['N']==50
    assert adopted['design_assistance']['adopted_design']['N']==option['request']['N']
    reply=store.export(adopted['scheme_id']);path=store.downloads[reply['download_url'].rsplit('/',1)[-1]]
    with zipfile.ZipFile(path) as z:exported=json.loads(z.read('scheme.json'));assert exported['design_assistance']==adopted['design_assistance']

def test_stale_advice_reports_are_bounded(tmp_path):
    s=DesignStore(tmp_path,FakeModels())
    for _ in range(34):s.advise(dict(design=request(N=1),explore_families=False))
    assert len(s.advice_reports)==32

@pytest.mark.parametrize('data',[{},dict(design=[],explore_families=True),dict(design=request(),explore_families='yes'),dict(design=request(N=True),explore_families=True),dict(design=request(diameter_mm=10),explore_families=True)])
def test_invalid_advice_is_rejected_by_http(server,data):
    status,_,_=call(server,'POST','/api/design-assist',json.dumps(data).encode(),{'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'})
    assert status==400

def test_new_static_files_and_api_no_solver_calls(server):
    for path in ('/design_assist.js','/design_assist.css'):assert call(server,'GET',path)[0]==200

def test_http_adoption_rejects_stale_and_tampered_requests(server):
    server.designs.models=FakeModels();headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'}
    original=request(N=50);status,raw,_=call(server,'POST','/api/design-assist',json.dumps(dict(design=original,explore_families=True)).encode(),headers)
    assert status==200;report=json.loads(raw);suggestion=next(s for s in report['suggestions'] if s['kind']=='quantity')
    payload=dict(advice_id=report['advice_id'],suggestion_id=suggestion['suggestion_id'],current_design=original)
    for bad in (dict(payload,current_design=dict(original,radius_mm=42)),dict(payload,N=1)):
        assert call(server,'POST','/api/design-assist-adopt',json.dumps(bad).encode(),headers)[0]==400
    status,raw,_=call(server,'POST','/api/design-assist-adopt',json.dumps(payload).encode(),headers)
    assert status==200;scheme=json.loads(raw);independent_check(scheme)
    assert scheme['design_assistance']['quantity_changed'] and scheme['design_assistance']['requested_design']==original
