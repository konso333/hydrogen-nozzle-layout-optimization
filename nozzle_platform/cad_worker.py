"""Isolated CAD process. Writes results to files; native CAD logs are not an API."""

import argparse
import json
import math
from pathlib import Path
import sys


def run(job):
    if job.get("gmsh_path"):
        sys.path.insert(0, job["gmsh_path"])
    import gmsh
    import numpy as np

    gmsh.initialize([], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Geometry.OCCParallel", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
        gmsh.model.add("nozzle_platform")
        entities = gmsh.model.occ.importShapes(job["source"])
        solids = [e for e in entities if e[0] == 3]
        if not solids or len(solids) != len(entities) or len(solids) > 32:
            raise ValueError("模型必须包含 1～32 个封闭三维实体；请导入 STEP 实体。")
        gmsh.model.occ.synchronize()
        bounds = [gmsh.model.getBoundingBox(*e) for e in solids]
        low = np.min([b[:3] for b in bounds], axis=0)
        high = np.max([b[3:] for b in bounds], axis=0)
        if not np.isfinite([low, high]).all() or np.any(high - low > 10000):
            raise ValueError("模型尺寸无效或超过 10 m；请核对单位。")
        axis = job["axis"]
        axis_index = {"x": 0, "y": 1, "z": 2}[axis]
        origin = np.array(job.get("origin") or ((low + high) / 2).tolist(), dtype=float)
        if job.get("origin") is None:
            origin[axis_index] = high[axis_index]
        if origin.shape != (3,) or not np.isfinite(origin).all():
            raise ValueError("安装原点必须是三个有限坐标，单位 mm。")
        rotations = {
            "x": [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
            "y": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
            "z": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        }
        rotation = np.array(rotations[axis], dtype=float)
        matrix = np.eye(4)
        matrix[:3, :3] = rotation
        matrix[:3, 3] = -rotation @ origin
        gmsh.model.occ.affineTransform(solids, matrix.ravel().tolist())
        gmsh.model.occ.synchronize()
        volume = sum(gmsh.model.occ.getMass(*e) for e in solids)
        if not math.isfinite(volume) or volume <= 0:
            raise ValueError("模型没有有效正体积。")

        if job["operation"] == "export":
            placed, expected_solids = [], []
            for x, y in job["coordinates"]:
                copies = gmsh.model.occ.copy(solids)
                gmsh.model.occ.translate(copies, x, y, 0)
                placed.extend(copies)
                expected_solids.extend({"volume_mm3": gmsh.model.occ.getMass(*e),
                    "centre_mm": list(gmsh.model.occ.getCenterOfMass(*e))} for e in copies)
            gmsh.model.occ.remove(solids, recursive=True)
            gmsh.model.occ.synchronize()
            expected_volume = volume * len(job["coordinates"])
            expected_count = len(placed)
            gmsh.write(job["output_step"])
            gmsh.clear()
            gmsh.model.add("roundtrip")
            imported = gmsh.model.occ.importShapes(job["output_step"])
            gmsh.model.occ.synchronize()
            actual_volume = sum(gmsh.model.occ.getMass(*e) for e in imported if e[0] == 3)
            if len(imported) != expected_count or not math.isclose(actual_volume, expected_volume, rel_tol=1e-7, abs_tol=1e-6):
                raise ValueError("阵列 STEP 往返检查失败，不能发布导出文件。")
            remaining = list(imported)
            for expected in expected_solids:
                matching = [e for e in remaining if
                    math.isclose(gmsh.model.occ.getMass(*e), expected["volume_mm3"], rel_tol=1e-7, abs_tol=1e-6)
                    and np.allclose(gmsh.model.occ.getCenterOfMass(*e), expected["centre_mm"], atol=1e-5, rtol=0)]
                if not matching:
                    raise ValueError("阵列 STEP 的逐实体位置检查失败。")
                remaining.remove(matching[0])
            return {"solid_count": expected_count, "total_volume_mm3": actual_volume,
                    "roundtrip_checked": True, "placement_checked": True,
                    "instances": expected_solids, "fluid_domain_built": False}

        # Verify a conservative coaxial cylinder using exact CAD Boolean volumes.
        boxes = [gmsh.model.getBoundingBox(*e) for e in solids]
        low = np.min([b[:3] for b in boxes], axis=0)
        high = np.max([b[3:] for b in boxes], axis=0)
        radius = max(abs(low[0]), abs(low[1]), abs(high[0]), abs(high[1]))
        z0, length = low[2] - 1, high[2] - low[2] + 2
        tolerance = max(1e-7, volume * 1e-8)
        for _ in range(8):
            cylinder = (3, gmsh.model.occ.addCylinder(0, 0, z0, 0, 0, length, radius + 1e-5))
            outside, _ = gmsh.model.occ.cut(gmsh.model.occ.copy(solids), [cylinder])
            outside_volume = sum(gmsh.model.occ.getMass(*e) for e in outside if e[0] == 3)
            if outside:
                gmsh.model.occ.remove(outside, recursive=True)
            if outside_volume <= tolerance:
                break
            radius *= 1.1
        else:
            raise ValueError("不能建立可靠的喷嘴外包络。请核对轴线和安装原点。")
        diameter = math.ceil(2 * radius * 1000 - .001) / 1000
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", max(.25, diameter / 70))
        gmsh.option.setNumber("Mesh.MeshSizeMax", max(.8, diameter / 12))
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.mesh.generate(2)
        tags, coordinates, _ = gmsh.model.mesh.getNodes()
        vertices = np.array(coordinates).reshape(-1, 3)
        lookup = {int(tag): index for index, tag in enumerate(tags)}
        types, _, groups = gmsh.model.mesh.getElements(2)
        triangles = []
        for element_type, nodes in zip(types, groups):
            if element_type != 2:
                raise ValueError("预览曲面必须为一阶三角形。")
            triangles.extend([[lookup[int(t)] for t in row] for row in np.array(nodes).reshape(-1, 3)])
        if not triangles or len(triangles) > 150000:
            raise ValueError("模型预览三角形数量超过首版预算（150000）。")
        source_scale = 1000 if job.get("source_unit") == "m" else 1
        source_matrix = matrix @ np.diag([source_scale, source_scale, source_scale, 1])
        return {"axis": axis, "origin_mm": origin.tolist(), "transform_mm_from_source": source_matrix.tolist(),
                "transform_mm_from_import_mm": matrix.tolist(),
                "bounds_mm": [low.tolist(), high.tolist()], "solid_count": len(solids),
                "volume_mm3": volume, "envelope_diameter_mm": diameter,
                "envelope_checked": True, "envelope_scope": "whole-model coaxial cylinder",
                "vertices": np.round(vertices, 6).tolist(), "triangles": triangles,
                "triangle_count": len(triangles), "preview_only_mesh": True,
                "fluid_domain_built": False}
    finally:
        gmsh.finalize()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    job = json.loads(args.job.read_text(encoding="utf-8"))
    try:
        result = {"ok": True, "result": run(job)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    Path(job["result"]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
