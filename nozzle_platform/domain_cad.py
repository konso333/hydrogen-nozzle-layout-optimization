"""Shared-chamber fluid CAD for an explicit axial-air/side-fuel template.

Called only inside the isolated, initialized CAD worker. No volume mesh or solver.
"""

import math
from pathlib import Path

import numpy as np

ROLES = ("oxidizer_inlet", "fuel_inlet", "outlet", "wall")
LENGTH_TOLERANCE = 2e-4  # CAD import/Boolean tolerance, in mm.


def connected_patches(gmsh, tags):
    parents = {tag: tag for tag in tags}

    def find(tag):
        while parents[tag] != tag:
            parents[tag] = parents[parents[tag]]
            tag = parents[tag]
        return tag

    owners = {}
    for tag in tags:
        for _, edge in gmsh.model.getBoundary([(2, tag)], oriented=False):
            if edge in owners:
                parents[find(tag)] = find(owners[edge])
            owners[edge] = tag
    groups = {}
    for tag in tags:
        groups.setdefault(find(tag), []).append(tag)
    return list(groups.values())


def surface_points(gmsh, tag):
    low, high = map(np.asarray, gmsh.model.getParametrizationBounds(2, tag))
    uv = [low + fraction * (high - low) for fraction in (.2, .5, .8)]
    return np.asarray(gmsh.model.getValue(2, tag, np.asarray(uv).ravel().tolist())).reshape(-1, 3)


def classify(gmsh, volume, config, coordinates):
    roles = {role: [] for role in ROLES}
    faces = []
    axis_points = np.asarray(coordinates, dtype=float)
    for _, tag in gmsh.model.getBoundary([volume], oriented=False):
        kind = gmsh.model.getType(2, tag)
        centre = list(gmsh.model.occ.getCenterOfMass(2, tag))
        bounds = list(gmsh.model.getBoundingBox(2, tag))
        area = gmsh.model.occ.getMass(2, tag)
        role = "wall"
        if kind == "Plane" and max(abs(bounds[2]-config["upstream_z_mm"]), abs(bounds[5]-config["upstream_z_mm"])) < LENGTH_TOLERANCE:
            role = "oxidizer_inlet"
        elif kind == "Plane" and max(abs(bounds[2]-config["outlet_z_mm"]), abs(bounds[5]-config["outlet_z_mm"])) < LENGTH_TOLERANCE:
            role = "outlet"
        elif kind == "Cylinder" and bounds[5] < config["chamber_start_z_mm"]+LENGTH_TOLERANCE:
            samples = surface_points(gmsh, tag)
            radial = np.linalg.norm(samples[:, None, :2]-axis_points[None, :, :], axis=2)
            on_envelope = np.any(np.max(abs(radial-config["passage_radius_mm"]), axis=0) < LENGTH_TOLERANCE)
            if on_envelope:
                if (bounds[2] < config["fuel_z_mm"]-config["fuel_half_band_mm"]-LENGTH_TOLERANCE or
                        bounds[5] > config["fuel_z_mm"]+config["fuel_half_band_mm"]+LENGTH_TOLERANCE):
                    raise ValueError(f"内部流道外包络出现预期供氢带以外的开口（Z={bounds[2]:.4f}～{bounds[5]:.4f} mm）；请核对通道半径、参考点与供氢带位置。")
                role = "fuel_inlet"
        roles[role].append(tag)
        faces.append({"tag": tag, "role": role, "surface_type": kind,
                      "area_mm2": area, "centre_mm": centre, "bounds_mm": bounds})
    if any(not tags for tags in roles.values()):
        raise ValueError("未能识别完整的空气入口、氢气入口、出口和壁面；此模型与所选供气模板不匹配。")
    by_tag = {f["tag"]: f for f in faces}
    patches = {role: connected_patches(gmsh, tags) for role, tags in roles.items()}
    per_nozzle = [{"nozzle_id": i+1, "oxidizer_inlet": 0, "fuel_inlet": 0,
                  "faces":{"oxidizer_inlet":[],"fuel_inlet":[]},
                  "area_mm2":{"oxidizer_inlet":0.,"fuel_inlet":0.}}
                 for i in range(len(coordinates))]
    for role in ("oxidizer_inlet", "fuel_inlet"):
        for group in patches[role]:
            weights = np.array([by_tag[t]["area_mm2"] for t in group])
            centre = np.average([by_tag[t]["centre_mm"] for t in group], axis=0, weights=weights)
            distances = np.linalg.norm(axis_points-centre[:2], axis=1)
            index = int(np.argmin(distances))
            if distances[index] > config["passage_radius_mm"]+LENGTH_TOLERANCE:
                raise ValueError("入口面无法关联到唯一的喷嘴轴线。")
            per_nozzle[index][role] += 1
            per_nozzle[index]["faces"][role].extend(group)
            per_nozzle[index]["area_mm2"][role] += float(weights.sum())
    for item in per_nozzle:
        if (item["oxidizer_inlet"] != config["oxidizer_ports_per_nozzle"] or
                item["fuel_inlet"] != config["fuel_ports_per_nozzle"]):
            raise ValueError(f"喷嘴 {item['nozzle_id']} 的实际入口为 {item['oxidizer_inlet']} 个空气口、{item['fuel_inlet']} 个氢气口，与模板期望不符。请核对模型或输入。")
    if len(patches["outlet"]) != 1:
        raise ValueError("公共燃烧室没有唯一连通的出口。")
    target_outlet_area = config["outlet_area_mm2"]
    outlet_area = sum(by_tag[t]["area_mm2"] for t in roles["outlet"])
    if not math.isclose(outlet_area, target_outlet_area, rel_tol=1e-7, abs_tol=1e-5):
        raise ValueError("公共出口面积与设置的燃烧室截面不符，请增加长度或核对几何。")
    return {"faces": faces, "physical_surfaces": roles,
            "boundary_patch_counts": {role: len(groups) for role, groups in patches.items()},
            "boundary_area_mm2": {role: sum(by_tag[t]["area_mm2"] for t in tags) for role, tags in roles.items()},
            "per_nozzle_ports": per_nozzle}


def add_chamber(gmsh, config):
    radius, length = config["chamber_radius_mm"], config["chamber_length_mm"]
    start = config["chamber_start_z_mm"]
    boundary = config["boundary"]
    if boundary["kind"] == "circle":
        return (3, gmsh.model.occ.addCylinder(0, 0, start, 0, 0, length, radius))
    sweep = math.radians(boundary["sweep_angle_deg"])
    chamber = (3, gmsh.model.occ.addCylinder(0, 0, start, 0, 0, length, radius, angle=sweep))
    gmsh.model.occ.rotate([chamber], 0, 0, 0, 0, 0, 1, math.radians(boundary["start_angle_deg"]))
    return chamber


def preview(gmsh, mapping, diameter):
    gmsh.option.setNumber("Mesh.MeshSizeMin", .3)
    gmsh.option.setNumber("Mesh.MeshSizeMax", max(1.2, diameter/10))
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
    gmsh.option.setNumber("Mesh.ElementOrder", 1)
    gmsh.model.mesh.generate(2)
    tags, values, _ = gmsh.model.mesh.getNodes()
    vertices = np.asarray(values).reshape(-1, 3)
    lookup = {int(tag): i for i, tag in enumerate(tags)}
    triangles, labels = [], []
    for role_index, role in enumerate(ROLES):
        for tag in mapping["physical_surfaces"][role]:
            types, _, groups = gmsh.model.mesh.getElements(2, tag)
            for element_type, nodes in zip(types, groups):
                if element_type != 2:
                    raise ValueError("流体域预览存在非一阶三角形。")
                rows = np.asarray(nodes).reshape(-1, 3)
                triangles.extend([[lookup[int(n)] for n in row] for row in rows])
                labels.extend([role_index]*len(rows))
    if not triangles or len(triangles) > 600000:
        raise ValueError("流体域曲面预览超过首版 60 万三角形预算，请减少喷嘴数。")
    return {"vertices": np.round(vertices, 6).tolist(), "triangles": triangles,
            "triangle_roles": labels, "triangle_count": len(triangles), "role_names": list(ROLES),
            "preview_only_mesh": True}


def build_domain(gmsh, job, source_solids):
    config, coordinates = job["config"], job["coordinates"]
    directory = Path(job["output_directory"])
    directory.mkdir(parents=True, exist_ok=False)
    metals, envelopes = [], []
    for x, y in coordinates:
        copies = gmsh.model.occ.copy(source_solids)
        gmsh.model.occ.translate(copies, x, y, 0)
        metals.extend(copies)
        envelopes.append((3, gmsh.model.occ.addCylinder(x, y, config["upstream_z_mm"],
            0, 0, config["chamber_start_z_mm"]-config["upstream_z_mm"], config["passage_radius_mm"])))
    gmsh.model.occ.remove(source_solids, recursive=True)
    chamber = add_chamber(gmsh, config)
    enclosure, _ = gmsh.model.occ.fuse([chamber], envelopes)
    if len(enclosure) != 1:
        raise ValueError("喷嘴内部包络未全部接入公共燃烧室。")
    fluid, _ = gmsh.model.occ.cut(enclosure, metals)
    gmsh.model.occ.synchronize()
    if len(fluid) != 1 or fluid[0][0] != 3:
        raise ValueError("流体域存在多个分离空间；请核对内部通道、安装参考点及供气模板。程序不会自动丢弃空间。")
    entity = fluid[0]
    initial_mapping = classify(gmsh, entity, config, coordinates)
    volume = gmsh.model.occ.getMass(*entity)
    centre = list(gmsh.model.occ.getCenterOfMass(*entity))
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("流体域体积无效。")
    for filename in ("fluid.step", "fluid.brep"):
        gmsh.write(str(directory/filename))
    checks = {}
    # Re-identify boundary roles after each import; never reuse STEP face tags.
    for filename in ("fluid.step", "fluid.brep"):
        gmsh.clear()
        gmsh.model.add("roundtrip")
        imported = gmsh.model.occ.importShapes(str(directory/filename))
        gmsh.model.occ.synchronize()
        if len(imported) != 1 or imported[0][0] != 3:
            raise ValueError("流体域往返读取后不再是单一三维实体。")
        entity = imported[0]
        actual_volume = gmsh.model.occ.getMass(*entity)
        if (not math.isclose(actual_volume, volume, rel_tol=1e-7, abs_tol=1e-5) or
                not np.allclose(gmsh.model.occ.getCenterOfMass(*entity), centre, atol=1e-5, rtol=0)):
            raise ValueError("流体域往返体积或位置检查失败。")
        mapping = classify(gmsh, entity, config, coordinates)
        if mapping["boundary_patch_counts"] != initial_mapping["boundary_patch_counts"]:
            raise ValueError("流体域导出后边界连通性改变。")
        for role in ROLES:
            if not math.isclose(mapping["boundary_area_mm2"][role], initial_mapping["boundary_area_mm2"][role], rel_tol=1e-6, abs_tol=1e-5):
                raise ValueError("流体域导出后边界面积改变。")
        checks[filename] = {"single_volume": True, "volume_position_match": True, "boundary_mapping_checked": True}
    bounds = list(gmsh.model.getBoundingBox(*entity))
    lines = ['// Geometry in mm; surface preview is not a CFD volume mesh.',
             'SetFactory("OpenCASCADE");', 'Geometry.OCCTargetUnit = "MM";', 'Merge "fluid.brep";',
             f'Physical Volume("fluid", 1) = {{{entity[1]}}};']
    for index, role in enumerate(ROLES, 11):
        tags = ", ".join(map(str, mapping["physical_surfaces"][role]))
        lines.append(f'Physical Surface("{role}", {index}) = {{{tags}}};')
    (directory/"fluid.geo").write_text("\n".join(lines)+"\n", encoding="utf-8")
    mesh = preview(gmsh, mapping, config["passage_radius_mm"]*2)
    # Check that the portable loader restores the same geometry and named groups.
    expected_groups = {(3, 1): ("fluid", [entity[1]])}
    expected_groups.update({(2,i):(role,tags) for i,(role,tags) in enumerate(mapping["physical_surfaces"].items(),11)})
    gmsh.clear()
    gmsh.open(str(directory/"fluid.geo"))
    if set(gmsh.model.getPhysicalGroups()) != set(expected_groups):
        raise ValueError("流体域命名边界加载检查失败。")
    for (dim, tag), (name, members) in expected_groups.items():
        if gmsh.model.getPhysicalName(dim,tag) != name or sorted(gmsh.model.getEntitiesForPhysicalGroup(dim,tag)) != sorted(members):
            raise ValueError("流体域命名边界成员检查失败。")
    return {"fluid_domain_built": True, "connected_fluid_components": 1,
            "volume_mm3": volume, "centre_mm": centre, "bounds_mm": [bounds[:3],bounds[3:]],
            "envelope_diameter_mm": config["chamber_radius_mm"]*2,
            **mapping, **mesh, "cad_roundtrip_checks": checks, "portable_boundary_loader_checked": True,
            "boundary_template": config["boundary_template"], "shared_supply_plenum_built": False,
            "volume_mesh_generated": False, "fluent_executed": False, "scientific_eligible": False}
