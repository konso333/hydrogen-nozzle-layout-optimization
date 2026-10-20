"""Real CAD regressions: preserve source geometry and build a generic flow path."""

import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from nozzle_platform.models import ModelStore
from nozzle_platform.design import DesignStore
from nozzle_platform.domains import DomainStore
from tests.test_platform import request as design_request
from tests.test_platform_domains import config


def runtime():
    if importlib.util.find_spec("gmsh") is not None:
        return None
    candidate=Path(__file__).resolve().parents[3]/"分析/M8B最新模型_内部通流_20261008/tools/python"
    return str(candidate) if (candidate/"gmsh.py").is_file() else False


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    cad_runtime=runtime()
    if cad_runtime is False:
        pytest.skip("Optional Gmsh runtime is not installed")
    directory=tmp_path_factory.mktemp("rigid")
    script="""import json,sys
from pathlib import Path
if sys.argv[1]: sys.path.insert(0,sys.argv[1])
import gmsh
out=Path(sys.argv[2]);gmsh.initialize([],readConfigFiles=False)
try:
 gmsh.option.setNumber('General.Terminal',0);gmsh.option.setString('Geometry.OCCTargetUnit','MM')
 pipe=(3,gmsh.model.occ.addCylinder(0,0,-20,0,0,20,5))
 air=(3,gmsh.model.occ.addCylinder(0,0,-21,0,0,22,2))
 fuel=(3,gmsh.model.occ.addCylinder(1.5,0,-5,4,0,0,.4))
 metal,_=gmsh.model.occ.cut([pipe],[air,fuel]);gmsh.model.occ.synchronize()
 volume=sum(gmsh.model.occ.getMass(*e) for e in metal)
 gmsh.write(str(out/'source.step'))
 (out/'expected.json').write_text(json.dumps({'volume_mm3':volume}),encoding='utf-8')
finally: gmsh.finalize()
"""
    result=subprocess.run([sys.executable,"-c",script,cad_runtime or "",str(directory)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    assert result.returncode==0,result.stderr.decode("utf-8",errors="replace")
    return directory,cad_runtime,json.loads((directory/"expected.json").read_text())


@pytest.mark.parametrize("axis,bounds",[
    ("x",[[-5,-20,-5],[5,0,5]]),
    ("y",[[-5,0,-5],[5,20,5]]),
    ("z",[[-5,-5,-20],[5,5,0]])])
def test_real_rigid_transform_preserves_mass_and_analytic_bounds(source,tmp_path,axis,bounds):
    directory,cad_runtime,expected=source
    store=ModelStore(tmp_path/"m",gmsh_path=cad_runtime)
    item=store.add_source("pipe.step",(directory/"source.step").read_bytes())
    result=store.load(item["source_id"],axis=axis,origin=[0,0,0])
    assert result["rigid_transform_volume_checked"]
    assert result["volume_mm3"]==pytest.approx(expected["volume_mm3"],rel=1e-8)
    assert np.allclose(result["bounds_mm"],bounds,rtol=0,atol=1e-5)


def test_generic_three_nozzle_fluid_domain_has_independent_expected_volume(source,tmp_path):
    directory,cad_runtime,expected=source
    models=ModelStore(tmp_path/"m",gmsh_path=cad_runtime)
    item=models.add_source("pipe.step",(directory/"source.step").read_bytes())
    model=models.load(item["source_id"],axis="z",origin=[0,0,0])
    designs=DesignStore(tmp_path/"d",models)
    scheme=designs.create(design_request(model_id=model["model_id"],N=3,diameter_mm=10,spacing_mm=11))
    domains=DomainStore(tmp_path/"f",models,designs)
    request=config(scheme,chamber_start_z_mm=0,passage_radius_mm=5,upstream_z_mm=-20,
                   fuel_z_mm=-5,oxidizer_ports_per_nozzle=1,fuel_ports_per_nozzle=1)
    domain=domains.create(request)
    expected_volume=math.pi*42**2*60+3*(math.pi*5**2*20-expected["volume_mm3"])
    assert domain["volume_mm3"]==pytest.approx(expected_volume,rel=1e-7)
    assert domain["boundary_patch_counts"]["oxidizer_inlet"]==3
    assert domain["boundary_patch_counts"]["fuel_inlet"]==3
    for nozzle in domain["per_nozzle_ports"]:
        assert nozzle["oxidizer_inlet"]==1 and nozzle["fuel_inlet"]==1
        assert nozzle["area_mm2"]["oxidizer_inlet"]==pytest.approx(math.pi*2**2,rel=1e-7)
    assert not domain["volume_mesh_generated"] and not domain["fluent_executed"]
