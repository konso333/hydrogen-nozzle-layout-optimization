"""Fluid-domain constraints, lineage and export integrity without a solver."""

import copy
import hashlib
import json
import math
from pathlib import Path
import zipfile

import pytest

from nozzle_platform.design import DesignStore
from nozzle_platform.domains import DomainStore, normalize
from tests.test_platform import model as base_model, request as design_request


def model():
    return {**base_model(),"source_id":"a"*64,"source_unit":"mm","axis":"y","origin_mm":[0,0,0],
            "bounds_mm":[[-10,-10,-28],[10,10,4]]}


def config(scheme,**changes):
    return {"scheme_id":scheme["scheme_id"],"chamber_radius_mm":42,"chamber_length_mm":60,
        "chamber_start_z_mm":-1.5,"passage_radius_mm":7,"upstream_z_mm":-28,
        "fuel_z_mm":-4,"fuel_half_band_mm":.5,"oxidizer_ports_per_nozzle":10,"fuel_ports_per_nozzle":16,
        "boundary_template":"axial_air_side_hydrogen","template_confirmed":True,**changes}


class FakeModels:
    def __init__(self,root):
        self.root=root
        self.calls=[]

    def get(self,model_id):
        assert model_id=="test-model"
        return model()

    def cad(self,job):
        self.calls.append(job)
        directory=Path(job["output_directory"])
        directory.mkdir()
        for name in ("fluid.step","fluid.brep","fluid.geo"):
            (directory/name).write_text("SYNTHETIC FILE FOR EXPORT TEST ONLY",encoding="utf-8")
        return {"fluid_domain_built":True,"connected_fluid_components":1,"volume_mm3":123,
            "vertices":[[0,0,0]],"triangles":[[0,0,0]],"triangle_roles":[0],
            "boundary_patch_counts":{"oxidizer_inlet":90,"fuel_inlet":144,"outlet":1,"wall":1},
            "volume_mesh_generated":False,"fluent_executed":False}


@pytest.fixture
def stores(tmp_path):
    models=FakeModels(tmp_path)
    designs=DesignStore(tmp_path/"designs",models)
    scheme=designs.create(design_request())
    domains=DomainStore(tmp_path/"domains",models,designs)
    return models,designs,scheme,domains


def test_parameters_preserve_installation_shape_and_derive_outlet(stores):
    _,_,scheme,_=stores
    original=config(scheme)
    copy_before=copy.deepcopy(original)
    normalized=normalize(original,scheme,model())
    assert original==copy_before
    assert normalized["boundary"]=={"kind":"circle","radius_mm":42.0,"start_angle_deg":0.0,"sweep_angle_deg":360.0}
    assert normalized["outlet_z_mm"]==58.5
    assert normalized["outlet_area_mm2"]==pytest.approx(math.pi*42**2)


@pytest.mark.parametrize("changes",[
    {"template_confirmed":False},{"template_confirmed":1},{"boundary_template":"guess"},
    {"chamber_radius_mm":20},{"chamber_radius_mm":float("nan")},{"chamber_length_mm":4},
    {"chamber_length_mm":0},{"passage_radius_mm":11},{"passage_radius_mm":0},
    {"upstream_z_mm":-30},{"upstream_z_mm":0},{"fuel_z_mm":-1},
    {"chamber_start_z_mm":1},{"fuel_half_band_mm":4},{"oxidizer_ports_per_nozzle":0},
    {"fuel_ports_per_nozzle":True},{"fuel_ports_per_nozzle":129},{"scheme_id":"other"}
])
def test_invalid_physical_template_and_incompatible_geometry_are_rejected(stores,changes):
    models,_,scheme,domains=stores
    with pytest.raises(ValueError):
        domains.create(config(scheme,**changes))
    assert not models.calls


def test_sector_boundary_is_not_replaced_by_a_circle(tmp_path):
    models=FakeModels(tmp_path)
    designs=DesignStore(tmp_path/"d",models)
    scheme=designs.create(design_request(N=3,region="sector",radius_mm=80,start_angle_deg=330,sweep_angle_deg=120))
    result=normalize(config(scheme,chamber_radius_mm=82),scheme,model())
    assert result["boundary"]["kind"]=="sector"
    assert result["outlet_area_mm2"]==pytest.approx(math.pi*82**2/3)


def test_domain_identity_binds_parent_and_dimensions(stores):
    models,_,scheme,domains=stores
    first=domains.create(config(scheme))
    assert domains.create(config(scheme))["domain_id"]==first["domain_id"]
    assert len(models.calls)==1
    changed=domains.create(config(scheme,chamber_length_mm=70))
    assert changed["domain_id"]!=first["domain_id"]
    assert changed["scheme_id"]==scheme["scheme_id"]
    assert models.calls[0]["source_sha256"]==model()["source_id"]


def test_export_is_compact_preserves_lineage_and_verifies_hashes(stores):
    _,designs,scheme,domains=stores
    domain=domains.create(config(scheme))
    export=domains.export(domain["domain_id"],scheme["scheme_id"])
    file=designs.downloads[export["download_url"].rsplit("/",1)[-1]]
    with zipfile.ZipFile(file) as bundle:
        record=json.loads(bundle.read("domain.json"))
        assert record["scheme_id"]==scheme["scheme_id"]
        assert "vertices" not in record and "triangles" not in record
        assert not record["volume_mesh_generated"] and not record["fluent_executed"]
        for name,expected in record["artifact_sha256"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest()==expected


def test_changed_artifact_cannot_be_exported(stores):
    _,_,scheme,domains=stores
    domain=domains.create(config(scheme))
    (domains.directories[domain["domain_id"]]/"fluid.brep").write_text("changed")
    with pytest.raises(ValueError,match="文件已改变"):
        domains.export(domain["domain_id"],scheme["scheme_id"])


def test_rebuild_repairs_changed_cached_domain(stores):
    models,_,scheme,domains=stores
    first=domains.create(config(scheme))
    old_directory=domains.directories[first["domain_id"]]
    (old_directory/"fluid.geo").write_text("changed")
    second=domains.create(config(scheme))
    assert second["domain_id"]==first["domain_id"]
    assert len(models.calls)==2 and domains.directories[second["domain_id"]]!=old_directory
    domains.export(second["domain_id"],scheme["scheme_id"])


def test_export_rejects_mismatched_array(stores):
    _,_,scheme,domains=stores
    domain=domains.create(config(scheme))
    with pytest.raises(ValueError):
        domains.export(domain["domain_id"],"different-array")


def test_failed_cad_does_not_publish_result(stores,monkeypatch):
    models,_,scheme,domains=stores
    def failure(job):
        raise ValueError("boundary mismatch")
    monkeypatch.setattr(models,"cad",failure)
    with pytest.raises(ValueError,match="boundary mismatch"):
        domains.create(config(scheme))
    assert not domains.domains and not domains.directories
