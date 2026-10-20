"""Independent geometry checks and HTTP/file boundaries of the local platform."""

import copy
import csv
import hashlib
import http.client
import io
import json
from pathlib import Path
import math
import threading
import zipfile

import pytest

from nozzle_platform.design import DesignStore, generate
from nozzle_platform.models import ModelStore
from nozzle_platform.server import PlatformServer
from shape_boundary import MountingBoundary


def model():
    return {"model_id": "test-model", "source_id": "source", "label": "SYNTHETIC TEST",
            "envelope_diameter_mm": 20, "triangle_count": 12}


def request(**changes):
    return {"model_id": "test-model", "N": 9, "family": "rectangular", "region": "circle",
            "radius_mm": 40, "start_angle_deg": 0, "sweep_angle_deg": 360,
            "diameter_mm": 20, "edge_gap_mm": 1, "wall_clearance_mm": .1,
            "spacing_mode": "auto", "spacing_mm": 21, "rotation_deg": 0, **changes}


@pytest.mark.parametrize("family,N,radius", [("rectangular",9,40), ("honeycomb",9,40), ("sunflower",7,80), ("ring",9,55)])
def test_coordinates_independently_satisfy_user_constraints(family, N, radius):
    config = request(family=family, N=N, radius_mm=radius)
    result = generate(config, model())
    assert result["feasible"]
    points = result["coordinates_mm"]
    assert len(points) == N
    assert len(set(map(tuple, points))) == N
    assert min(math.dist(a,b) for i,a in enumerate(points) for b in points[i+1:]) >= 21-1e-9
    assert min(radius-math.hypot(x,y)-10 for x,y in points) >= .1-1e-9
    assert result["simulation_status"] == "not_started"
    assert not result["scientific_eligible"]


@pytest.mark.parametrize("start,sweep", [(0,120),(330,100),(0,270)])
def test_sector_entire_envelopes_respect_arc_and_both_radial_edges(start,sweep):
    result = generate(request(N=3, radius_mm=80, region="sector", start_angle_deg=start, sweep_angle_deg=sweep), model())
    assert result["feasible"]
    boundary = MountingBoundary("sector",80,start,sweep)
    assert min(boundary.signed_distance_mm(p)-10 for p in result["coordinates_mm"]) >= .1-1e-9


def test_impossible_count_does_not_relax_region_or_constraints():
    config = request(N=40, radius_mm=30)
    before = copy.deepcopy(config)
    result = generate(config, model())
    assert not result["feasible"] and result["N"] == 40
    assert config == before
    assert "coordinates_mm" not in result and result["failure_reasons"]


@pytest.mark.parametrize("changes", [{"N":True},{"N":0},{"N":1.5},{"N":129},
    {"diameter_mm":14},{"edge_gap_mm":-1},{"radius_mm":float("nan")},
    {"region":"other"},{"family":"other"},{"spacing_mode":"manual","spacing_mm":20},
    {"sweep_angle_deg":180},{"model_id":"other"}])
def test_invalid_inputs_and_incorrect_footprint_are_rejected(changes):
    with pytest.raises(ValueError):
        generate(request(**changes),model())


def test_one_nozzle_metrics_use_null_instead_of_nan_and_json_is_finite():
    result = generate(request(N=1),model())
    assert result["summary"]["minimum_center_distance_mm"] is None
    assert result["summary"]["minimum_edge_gap_mm"] is None
    json.dumps(result,allow_nan=False)


def test_reproducible_scheme_identity_includes_model_alignment():
    first = generate(request(),model())
    assert first == generate(request(),model())
    other = generate(request(model_id="different-alignment"),{**model(),"model_id":"different-alignment"})
    assert other["scheme_id"] != first["scheme_id"]


class FakeModels:
    def get(self,key):
        assert key == "test-model"
        return model()

    def export_array(self,*args):
        raise AssertionError("Non-CAD export must not start any CAD process")


def test_exported_coordinates_match_verified_scheme_and_files_have_hashes(tmp_path):
    store = DesignStore(tmp_path,FakeModels())
    scheme = store.create(request())
    exported = store.export(scheme["scheme_id"])
    path = store.downloads[exported["download_url"].rsplit("/",1)[-1]]
    with zipfile.ZipFile(path) as bundle:
        records = list(csv.DictReader(io.StringIO(bundle.read("coordinates.csv").decode("utf-8-sig"))))
        assert len(records) == 9
        assert [[float(r["x_mm"]),float(r["y_mm"])] for r in records] == scheme["coordinates_mm"]
        assert all(float(r["z_mm"])==0 for r in records)
        report = json.loads(bundle.read("scheme.json"))
        for name, expected in report["artifact_sha256"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == expected
        assert "array.step" not in bundle.namelist()


def test_failed_generation_is_not_exportable(tmp_path):
    store = DesignStore(tmp_path,FakeModels())
    assert not store.create(request(N=40,radius_mm=30))["feasible"]
    assert not store.schemes
    with pytest.raises(ValueError):
        store.export("made-up")


def test_archive_does_not_extract_or_trust_arbitrary_paths(tmp_path):
    archive = tmp_path/"bad.zip"
    with zipfile.ZipFile(archive,"w") as bundle:
        bundle.writestr("../../outside_merged_solid.step",b"anything")
        bundle.writestr("notes.md",b"untrusted documentation")
    store = ModelStore(tmp_path/"models",archive=archive)
    assert store.catalog() == []
    assert not (tmp_path/"outside_merged_solid.step").exists()


@pytest.fixture
def server(tmp_path):
    host = PlatformServer(("127.0.0.1",0),tmp_path)
    thread = threading.Thread(target=host.serve_forever,daemon=True)
    thread.start()
    yield host
    host.shutdown()
    host.server_close()
    thread.join(timeout=5)


def call(server,method,path,body=None,headers=None):
    connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=10)
    connection.request(method,path,body=body,headers=headers or {})
    response=connection.getresponse()
    result=response.status,response.read(),dict(response.getheaders())
    connection.close()
    return result


def test_browser_page_is_local_and_has_no_remote_dependency(server):
    status,raw,headers=call(server,"GET","/")
    assert status==200 and "生成阵列" in raw.decode()
    assert b"https://" not in raw
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert json.loads(call(server,"GET","/api/bootstrap")[1])["simulation_available"] is False
    assert call(server,"GET","/../../models/source.step")[0]==404


def test_foreign_origin_and_host_cannot_operate_local_cad(server):
    assert call(server,"POST","/api/load-model",b"{}",{"Origin":"https://elsewhere.example"})[0]==403
    assert call(server,"GET","/api/bootstrap",headers={"Host":"elsewhere.example"})[0]==403


def test_second_server_cannot_bind_the_same_port(server, tmp_path):
    with pytest.raises(OSError):
        PlatformServer(("127.0.0.1",server.server_port),tmp_path/"second")


def test_http_rejects_nonfinite_and_invalid_payloads(server):
    headers={"Origin":f"http://127.0.0.1:{server.server_port}","Content-Type":"application/json"}
    assert call(server,"POST","/api/generate",b'{"N":NaN}',headers)[0]==400
    assert call(server,"POST","/api/generate",b'[]',headers)[0]==400
    assert call(server,"POST","/api/export",b'{"cad":"yes"}',headers)[0]==400
