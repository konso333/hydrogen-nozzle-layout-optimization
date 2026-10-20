"""UI adapter for the existing deterministic shape generator; no solver values."""

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import threading
import uuid
import zipfile

from shape_search import FAMILIES, run_shape_search
from validation import require_count, require_finite


def number(value, label, *, minimum=0, strict=False):
    value = require_finite(value, label)
    if value < minimum or (strict and value == minimum):
        raise ValueError(f"{label} 必须{'大于' if strict else '不小于'} {minimum}。")
    return value


def generate(request, model):
    if not isinstance(request, dict):
        raise ValueError("设计参数必须是对象。")
    allowed = {"model_id", "N", "family", "region", "radius_mm", "start_angle_deg", "sweep_angle_deg",
               "diameter_mm", "edge_gap_mm", "wall_clearance_mm", "spacing_mode", "spacing_mm", "rotation_deg"}
    if set(request) != allowed:
        raise ValueError("设计参数缺失或包含未知字段，请刷新页面。")
    if request["model_id"] != model["model_id"]:
        raise ValueError("设计与模型不一致。")
    count = require_count(request["N"], "N")
    if count > 128:
        raise ValueError("首版每个方案最多 128 个喷嘴。")
    family = request["family"]
    if family not in FAMILIES:
        raise ValueError("请选择蜂巢、向日葵、方阵或环形。")
    if count * model["triangle_count"] > 2000000:
        raise ValueError("阵列预览超过 200 万三角形预算，请减少 N 或简化单喷嘴。")
    radius = number(request["radius_mm"], "安装区域半径", strict=True)
    if radius > 5000:
        raise ValueError("首版安装区域半径最多 5000 mm。")
    diameter = number(request["diameter_mm"], "喷嘴包络直径", strict=True)
    if diameter + 1e-6 < model["envelope_diameter_mm"]:
        raise ValueError(f"包络直径不能小于此模型已检查的 {model['envelope_diameter_mm']:g} mm。")
    gap = number(request["edge_gap_mm"], "喷嘴外缘最小间隙")
    wall = number(request["wall_clearance_mm"], "边界最小余量")
    if request["region"] not in {"circle", "sector"}:
        raise ValueError("请选择圆形或扇形安装区域。")
    start = require_finite(request["start_angle_deg"], "扇形起始角")
    sweep = require_finite(request["sweep_angle_deg"], "扇形角度")
    boundary = {"kind": request["region"], "radius_mm": radius,
                "start_angle_deg": start, "sweep_angle_deg": sweep}
    if request["region"] == "circle" and (start != 0 or sweep != 360):
        raise ValueError("圆形区域要求起始角 0°、角度 360°。")
    if request["spacing_mode"] not in {"auto", "manual"}:
        raise ValueError("请选择自动或指定间距。")
    automatic = request["spacing_mode"] == "auto"
    rotation = require_finite(request["rotation_deg"], "整体旋转角")
    lattice = family in {"honeycomb", "rectangular"}
    pitch = diameter + gap if automatic or not lattice else number(request["spacing_mm"], "中心距", strict=True)
    if not automatic and lattice and pitch + 1e-9 < diameter + gap:
        raise ValueError("指定中心距小于包络直径与最小间隙之和。")
    if lattice and radius / pitch + .71 > 15:
        raise ValueError("区域半径与中心距之比超过首版点池预算，请缩小区域或增大中心距。")
    layouts = []
    angles = [0, 15, 30, 45, 60, 75] if automatic else [rotation]
    if family in {"honeycomb", "rectangular"}:
        # Include the 3/8-pitch phase that recovered the prior nine-nozzle
        # honeycomb candidate; do not weaken the user's envelope/clearances.
        shifts = [(0, 0), (.5, 0), (0, .5), (.5, .5), (.375, 0), (0, .375), (.25, .25), (.375, .375)] if automatic else [(0, 0)]
        for angle in angles:
            for x, y in shifts:
                layouts.append({"family": family, "parameters": {"spacing_mm": pitch,
                    "rotation_deg": angle, "offset_x_mm": x * pitch, "offset_y_mm": y * pitch}})
    else:
        if request["region"] == "sector" and not 0 < sweep < 360:
            raise ValueError("扇形角度必须大于 0°且小于 360°。")
        multiplier = min(16, max(1, math.ceil(360 / sweep))) if request["region"] == "sector" else 1
        if count * multiplier > 2000:
            raise ValueError("扇形候选点超过预算，请减少 N 或增大扇形角度。")
        for angle in angles:
            for fraction in ([1, .9, .8] if automatic else [1]):
                layouts.append({"family": family, "parameters": {"rotation_deg": angle,
                    "radius_fraction": fraction, "pool_multiplier": multiplier}})
    design = {"schema_version": 1, "name": "platform geometry design", "mode": "fixed_space",
              "boundary": boundary, "nozzle": {"outer_diameter_mm": diameter,
              "edge_gap_mm": gap, "wall_clearance_mm": wall}, "counts": [count],
              "radius_candidates_mm": [], "layouts": layouts, "power_model": None,
              "target_power_kW": None, "tolerance_mm": 1e-9}
    report = run_shape_search(design)
    if not report["candidates"]:
        return {"feasible": False, "N": count, "request": request,
                "message": "当前参数下未找到可行排布。请减少 N、增大区域或调整间距／排列。程序未改变你的数量或约束。",
                "attempts": report["unique_requests"],
                "failure_reasons": list(dict.fromkeys(r["reason"] for r in report["infeasible_candidates"]))[:5],
                "search_scope": "有限参数族；未找到候选不等于不存在其他非规则排布。"}
    chosen = min(report["candidates"], key=lambda c: (-c["metrics"]["minimum_boundary_margin_mm"], c["layout_id"]))
    coordinates = chosen["coordinates_mm"]
    distances = [math.dist(a, b) for i, a in enumerate(coordinates) for b in coordinates[i + 1:]]
    minimum = min(distances) if distances else None
    summary = {"minimum_center_distance_mm": minimum,
               "minimum_edge_gap_mm": minimum - diameter if minimum is not None else None,
               "minimum_wall_clearance_mm": chosen["validation"]["minimum_wall_clearance_mm"],
               "boundary_area_mm2": chosen["metrics"]["boundary_area_mm2"],
               "nozzle_count": len(coordinates)}
    identity_payload = {"model_id": model["model_id"], "layout_id": chosen["layout_id"]}
    scheme_id = hashlib.sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()
    return {"schema_version": 1, "platform_version": "0.1.0", "scheme_id": scheme_id, "feasible": True,
            "request": request, "coordinates_mm": coordinates, "summary": summary,
            "candidate": chosen, "search_design": design, "attempts": report["unique_requests"],
            "feasible_candidates": report["feasible_count"], "model": {k: v for k, v in model.items()
                if k not in {"vertices", "triangles"}},
            "scope": "整个模型的同轴圆柱包络检查；未生成公共流体域、网格或 CFD 结果。",
            "selection_rule": "有限候选中优先选择边界剩余余量最大者，同值按布局编号确定。",
            "simulation_status": "not_started", "manufacturing_eligible": False,
            "scientific_eligible": False}


def layout_svg(scheme):
    request = scheme["request"]
    radius = request["radius_mm"]
    pad = max(radius * .15, 5)
    size = radius + pad
    if request["region"] == "circle":
        boundary = f'<circle cx="0" cy="0" r="{radius}"/>'
    else:
        a = math.radians(request["start_angle_deg"])
        b = a + math.radians(request["sweep_angle_deg"])
        x, y = radius * math.cos(a), -radius * math.sin(a)
        xx, yy = radius * math.cos(b), -radius * math.sin(b)
        boundary = f'<path d="M0 0 L{x} {y} A{radius} {radius} 0 {int(request["sweep_angle_deg"] > 180)} 0 {xx} {yy} Z"/>'
    circles = "".join(f'<circle cx="{x}" cy="{-y}" r="{request["diameter_mm"] / 2}" fill="#ccf7ee" stroke="#087c6c"/>'
                      f'<text x="{x}" y="{-y}" fill="#075447" text-anchor="middle" dominant-baseline="central" font-size="{radius / 15}">{i}</text>'
                      for i, (x, y) in enumerate(scheme["coordinates_mm"], 1))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-size} {-size} {2*size} {2*size}">'
            f'<rect x="{-size}" y="{-size}" width="{2*size}" height="{2*size}" fill="#f8fbfc"/>'
            f'<g fill="none" stroke="#183b46" stroke-width="{radius / 160}">{boundary}</g>'
            f'<g stroke-width="{radius / 250}">{circles}</g></svg>')


class DesignStore:
    def __init__(self, root, models):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.models = models
        self.schemes, self.downloads = {}, {}
        self.lock = threading.RLock()

    def create(self, request):
        model = self.models.get(request.get("model_id"))
        scheme = generate(request, model)
        if scheme["feasible"]:
            with self.lock:
                self.schemes[scheme["scheme_id"]] = scheme
        return scheme

    def export(self, scheme_id, *, cad=False):
        with self.lock:
            if scheme_id not in self.schemes:
                raise ValueError("请先生成有效方案。")
            scheme = self.schemes[scheme_id]
        directory = self.root / ("scheme_" + uuid.uuid4().hex)
        directory.mkdir()
        report = dict(scheme)
        files = {}
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["nozzle_id", "x_mm", "y_mm", "z_mm"])
        writer.writerows((i, x, y, 0) for i, (x, y) in enumerate(scheme["coordinates_mm"], 1))
        files["coordinates.csv"] = output.getvalue().encode("utf-8-sig")
        files["layout.svg"] = layout_svg(scheme).encode("utf-8")
        if cad:
            report["cad_export"] = self.models.export_array(scheme["model"]["model_id"], scheme["coordinates_mm"], directory / "array.step")
            files["array.step"] = (directory / "array.step").read_bytes()
        report["artifact_sha256"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
        files["scheme.json"] = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        files["README.txt"] = ("阵列设计节点 1\ncoordinates.csv: 安装平面 XY，喷嘴轴向 +Z，单位 mm。\n"
            "layout.svg: 完整模型保守圆柱包络的二维投影，不是喷嘴流通面积。\n"
            "scheme.json: 模型摘要、原始坐标变换、约束、实际选中参数和核验结果。\n"
            "array.step（如有）: 实体阵列，已重新读取核对数量与体积；不是流体域或 Fluent 网格。\n"
            "尚未执行 Fluent，不含 CFD 数据。全模型包络检查不等于制造批准。\n").encode("utf-8")
        archive = directory / "nozzle_array.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, raw in files.items():
                bundle.writestr(name, raw)
        download_id = uuid.uuid4().hex
        with self.lock:
            self.downloads[download_id] = archive
        return {"download_url": "/downloads/" + download_id, "cad_included": cad,
                "filename": "nozzle_array_" + scheme_id[:10] + ".zip"}
