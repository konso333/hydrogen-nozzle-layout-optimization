"""Validation, lineage and portable exports for shared fluid-domain CAD."""

import hashlib
import json
import math
from pathlib import Path
import threading
import uuid
import zipfile

from shape_boundary import MountingBoundary
from validation import require_count, require_finite

DOMAIN_ENGINE = "shared-domain-1"
FIELDS = {"scheme_id", "chamber_radius_mm", "chamber_length_mm", "chamber_start_z_mm", "passage_radius_mm",
          "upstream_z_mm", "fuel_z_mm", "fuel_half_band_mm", "oxidizer_ports_per_nozzle",
          "fuel_ports_per_nozzle", "boundary_template", "template_confirmed"}
MESH_FIELDS = {"vertices", "triangles", "triangle_roles"}


def normalize(request, scheme, model):
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise ValueError("流体域参数缺失或包含未知字段，请刷新页面。")
    if request["scheme_id"] != scheme["scheme_id"] or scheme["model"]["model_id"] != model["model_id"]:
        raise ValueError("流体域、阵列方案和模型不一致，请重新生成。")
    if request["boundary_template"] != "axial_air_side_hydrogen":
        raise ValueError("当前支持轴向空气、侧向氢气的逐喷嘴入口模板。")
    if request["template_confirmed"] is not True:
        raise ValueError("请先在界面确认所选供气方式与实际模型一致。")
    count = len(scheme["coordinates_mm"])
    if count > 25:
        raise ValueError("当前公共流体域节点最多处理 25 个喷嘴，请先用少量代表方案。")
    config = dict(request)
    for key in ("chamber_radius_mm", "chamber_length_mm", "passage_radius_mm", "fuel_half_band_mm"):
        config[key] = require_finite(request[key],key)
        if config[key] <= 0:
            raise ValueError("燃烧室、内部包络半径及供氢带半宽必须为正数。")
    for key in ("upstream_z_mm", "fuel_z_mm", "chamber_start_z_mm"):
        config[key] = require_finite(request[key],key)
    for key in ("oxidizer_ports_per_nozzle", "fuel_ports_per_nozzle"):
        config[key] = require_count(request[key],key)
        if config[key] > 128:
            raise ValueError("每个喷嘴的入口数量最多为 128。")
    if config["chamber_radius_mm"] > 5000 or config["chamber_length_mm"] > 10000:
        raise ValueError("燃烧室尺寸超过首版预算。")
    if config["upstream_z_mm"] >= 0 or config["upstream_z_mm"] < model["bounds_mm"][0][2]-.01:
        raise ValueError("空气入口截面需位于参考面上游，且不能早于当前金属模型的上游端；公共供气腔尚未接入。")
    if not (config["upstream_z_mm"] < config["fuel_z_mm"]-config["fuel_half_band_mm"] <
            config["fuel_z_mm"]+config["fuel_half_band_mm"] < config["chamber_start_z_mm"] <= 0):
        raise ValueError("供氢带需完整位于空气入口与公共腔起点之间，公共腔起点不晚于安装参考面。")
    outlet_z = config["chamber_start_z_mm"]+config["chamber_length_mm"]
    if outlet_z <= max(0,model["bounds_mm"][1][2])+.1:
        raise ValueError("燃烧室出口需位于全部金属部件下游，请增加燃烧室长度。")
    if config["passage_radius_mm"] > model["envelope_diameter_mm"]/2+1e-6:
        raise ValueError("内部流道截取半径不能超过模型整体包络半径。")
    boundary_data = {**scheme["candidate"]["specification"]["boundary"], "radius_mm":config["chamber_radius_mm"]}
    boundary = MountingBoundary(**boundary_data)
    required = max(config["passage_radius_mm"],model["envelope_diameter_mm"]/2)
    if any(boundary.signed_distance_mm(p) < required-1e-6 for p in scheme["coordinates_mm"]):
        raise ValueError("燃烧室截面未完整容纳当前喷嘴包络，请增大半径或调整阵列。")
    for i,p in enumerate(scheme["coordinates_mm"]):
        if any(math.dist(p,q) <= 2*config["passage_radius_mm"]+1e-6 for q in scheme["coordinates_mm"][i+1:]):
            raise ValueError("相邻内部截取包络相交，不能作为独立喷嘴通道。")
    return {**config, "boundary":boundary.to_dict(), "outlet_z_mm":outlet_z,"outlet_area_mm2":boundary.area_mm2}


class DomainStore:
    def __init__(self, root, models, designs):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.models,self.designs = models,designs
        self.domains,self.directories = {},{}
        self.lock = threading.RLock()

    def create(self, request, *, cancel=None):
        scheme = self.designs.get(request.get("scheme_id"))
        model = self.models.get(scheme["model"]["model_id"])
        config = normalize(request,scheme,model)
        payload = {"engine":DOMAIN_ENGINE,"scheme_id":scheme["scheme_id"],"config":config}
        domain_id = hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
        with self.lock:
            if domain_id in self.domains:
                cached = self.domains[domain_id]
                folder = self.directories[domain_id]
                if all((folder/name).is_file() and hashlib.sha256((folder/name).read_bytes()).hexdigest() == expected
                       for name,expected in cached["artifact_sha256"].items()):
                    return cached
                del self.domains[domain_id]
                del self.directories[domain_id]
        directory = self.root/("domain_"+uuid.uuid4().hex)
        source = self.models.root/(model["source_id"]+".step")
        job = {"operation":"domain", "source":str(source),
            "source_sha256":model["source_id"], "source_unit":model["source_unit"],
            "axis":model["axis"],"origin":model["origin_mm"],
            "coordinates":scheme["coordinates_mm"],"config":config,"output_directory":str(directory)}
        result=self.models.cad(job,cancel=cancel) if cancel is not None else self.models.cad(job)
        artifacts = {name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
                     for name in ("fluid.step","fluid.brep","fluid.geo")}
        domain = {"schema_version":1,"engine":DOMAIN_ENGINE,"domain_id":domain_id,
            "scheme_id":scheme["scheme_id"],"model_id":model["model_id"],"source_sha256":model["source_id"],
            "model_reference":{k:model[k] for k in ("name","source_unit","axis","origin_mm",
                "transform_mm_from_source","archive_sha256") if k in model},
            "array_specification":scheme["candidate"]["specification"],"array_request":scheme["request"],
            "config":config,"coordinates_mm":scheme["coordinates_mm"],**result,"artifact_sha256":artifacts,
            "scope":"逐喷嘴入口的内部流道与公共燃烧室；边界按已确认模板标记。未建公共供气腔、体网格或 CFD 结果。"}
        record = {k:v for k,v in domain.items() if k not in MESH_FIELDS}
        (directory/"domain.json").write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        with self.lock:
            self.domains[domain_id],self.directories[domain_id] = domain,directory
        return domain

    def export(self, domain_id, scheme_id):
        with self.lock:
            domain = self.domains.get(domain_id)
            directory = self.directories.get(domain_id)
        if domain is None or domain["scheme_id"] != scheme_id:
            raise ValueError("请先为当前阵列构建有效流体域。")
        for name,expected in domain["artifact_sha256"].items():
            if hashlib.sha256((directory/name).read_bytes()).hexdigest() != expected:
                raise ValueError("流体域文件已改变，不能导出。请重新构建。")
        # Reconstruct report from the verified in-memory record, not edited disk text.
        record = {k:v for k,v in domain.items() if k not in MESH_FIELDS}
        files = {name:(directory/name).read_bytes() for name in domain["artifact_sha256"]}
        files["domain.json"] = json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
        output = directory/("fluid_domain_"+uuid.uuid4().hex+".zip")
        with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as bundle:
            for name,raw in files.items():
                bundle.writestr(name,raw)
            bundle.writestr("README.txt",("公共流体域与边界包\n单位 mm；安装平面 XY，轴向 +Z。\n"
                "fluid.step: 单一连通流体实体，不是金属阵列。\n"
                "fluid.brep + fluid.geo: 成套使用，在 Gmsh 中恢复四类命名边界；勿将 BREP 面编号套用于 STEP。\n"
                "domain.json: 来源阵列、实际边界面积、逐喷嘴入口数、检查与参数。\n"
                "当前仅有几何与显示曲面，没有体网格、Fluent 工程或 CFD 数据。\n"
                "采用逐喷嘴入口边界；不包含公共供气腔的流量分配。\n").encode("utf-8"))
        key = uuid.uuid4().hex
        with self.designs.lock:
            self.designs.downloads[key] = output
        return {"download_url":"/downloads/"+key,"filename":"fluid_domain_"+domain_id[:10]+".zip"}
