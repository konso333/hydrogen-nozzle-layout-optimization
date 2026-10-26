"""Local model catalog, immutable upload copies and isolated CAD conversion."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
import zipfile

from engineering_cad import _step_unit


LABELS = {"B0_baseline": "B0 · 原始基准", "V1_tip_D3p3": "V1 · 尾端 D3.3",
          "V2_neck_D4p5": "V2 · 缩颈 D4.5", "V3_head_D6p2": "V3 · 头部 D6.2"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MODEL_ENGINE = "cad-rigid-2"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ModelStore:
    def __init__(self, root, *, archive=None, gmsh_path=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.gmsh_path = str(Path(gmsh_path).resolve()) if gmsh_path else None
        self.sources, self.models = {}, {}
        self.lock = threading.RLock()
        self.cad_lock = threading.Lock()
        self.archive_hash = None
        if archive:
            self.add_archive(archive)

    def add_archive(self, path):
        raw = Path(path).read_bytes()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("模型包超过 20 MB。")
        self.archive_hash = digest(raw)
        # Read selected entries only. Never extract or execute archive paths.
        with zipfile.ZipFile(path) as bundle:
            for info in bundle.infolist():
                name = info.filename
                if name != Path(name).name or not name.endswith("_merged_solid.step"):
                    continue
                if info.file_size > MAX_UPLOAD_BYTES:
                    raise ValueError("STEP 解压后超过 20 MB。")
                key = name.removesuffix("_merged_solid.step")
                self.add_source(name, bundle.read(info), label=LABELS.get(key, key),
                                origin=[-1.23997517936, 11.67710738097, 25] if key in LABELS else None,
                                archive_hash=self.archive_hash)

    def add_source(self, name, raw, *, label=None, origin=None, archive_hash=None):
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("请选择不超过 20 MB 的 STEP 文件。")
        if Path(name).suffix.lower() not in {".step", ".stp"}:
            raise ValueError("此节点支持 STEP／STP 实体；请先转换其他格式。")
        unit = _step_unit(raw)
        source_id = digest(raw)
        path = self.root / (source_id + ".step")
        if not path.exists():
            path.write_bytes(raw)
        with self.lock:
            source = {"source_id": source_id, "name": Path(name).name,
                      "label": label or Path(name).name, "source_unit": unit,
                      "default_axis": "y", "default_origin_mm": origin,
                      "archive_sha256": archive_hash}
            self.sources[source_id] = source
        return source

    def catalog(self):
        with self.lock:
            return list(self.sources.values())

    def cad(self, job, *, cancel=None):
        directory = self.root / ("job_" + uuid.uuid4().hex)
        directory.mkdir()
        job = {**job, "gmsh_path": self.gmsh_path, "result": str(directory / "result.json")}
        (directory / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if cancel is not None:
            from nozzle_platform.bounded import run
            while not self.cad_lock.acquire(timeout=.1):
                if cancel.is_set():raise InterruptedError('CAD 任务已停止。')
            try:
                code=run([sys.executable,str(Path(__file__).with_name('cad_worker.py')),str(directory/'job.json')],
                         cwd=directory,log=directory/'cad.log',timeout=180,cancel=cancel)
            finally:self.cad_lock.release()
            if not Path(job['result']).is_file():raise ValueError('CAD 转换未返回结果，已保留本地日志。')
            result=json.loads(Path(job['result']).read_text(encoding='utf-8'))
            if code or not result['ok']:raise ValueError(result.get('error','CAD 转换失败。'))
            return result['result']
        with self.cad_lock, (directory / "cad.log").open("wb") as log:
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).with_name("cad_worker.py")),
                                          str(directory / "job.json")], stdout=log, stderr=log,
                                         stdin=subprocess.DEVNULL, timeout=180, creationflags=creationflags)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("CAD 处理超过 180 秒，任务已终止；请简化模型后重试。") from exc
        if not Path(job["result"]).exists():
            raise ValueError("CAD 转换未完成。请检查 Gmsh 安装及本地 cad.log。")
        result = json.loads(Path(job["result"]).read_text(encoding="utf-8"))
        if process.returncode or not result["ok"]:
            raise ValueError(result.get("error", "CAD 转换失败。"))
        return result["result"]

    def load(self, source_id, *, axis="y", origin=None):
        with self.lock:
            if source_id not in self.sources:
                raise ValueError("找不到模型，请重新导入。")
            source = dict(self.sources[source_id])
        if axis not in {"x", "y", "z"}:
            raise ValueError("请选择 X、Y 或 Z 作为喷嘴轴线。")
        if origin is not None:
            import math
            if (not isinstance(origin, list) or len(origin) != 3 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in origin)):
                raise ValueError("安装原点必须为三个有限数值，单位 mm。")
        key = digest(json.dumps([MODEL_ENGINE, source_id, axis, origin], sort_keys=True).encode())
        with self.lock:
            if key in self.models:
                return self.models[key]
        metadata = self.cad({"operation": "preview", "source": str(self.root / (source_id + ".step")),
                             "axis": axis, "origin": origin, "source_unit": source["source_unit"]})
        model = {**source, **metadata, "model_id": key,"model_engine":MODEL_ENGINE,"origin_input_mm":list(origin) if origin is not None else None}
        with self.lock:
            self.models[key] = model
        (self.root / (key + ".json")).write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        return model

    def get(self, model_id):
        with self.lock:
            if model_id not in self.models:
                raise ValueError("请先导入并确认单喷嘴模型。")
            return self.models[model_id]

    def export_array(self, model_id, coordinates, destination, *, cancel=None):
        model = self.get(model_id)
        job={"operation": "export", "source": str(self.root / (model["source_id"] + ".step")),"source_sha256":model['source_id'],
                         "axis": model["axis"], "origin": model["origin_mm"],
                         "source_unit": model["source_unit"],
                         "coordinates": coordinates, "output_step": str(destination)}
        return self.cad(job,cancel=cancel) if cancel is not None else self.cad(job)
