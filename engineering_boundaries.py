"""Associate explicitly labelled source faces with verified CAD copies; no mesh/CFD."""

from __future__ import annotations

import math
import json
from pathlib import Path

import numpy as np

from cfd.spec import number
import engineering_cad_build as cad
from experiments.spec import canonical_json


ROLES = ("fuel_inlet", "oxidizer_inlet", "outlet", "wall")
MANIFEST_NAME = "boundary_manifest.json"
REPORT_NAME = "boundary_mapping.json"
LOADER_NAME = "array_boundaries.geo"
AREA_ABSOLUTE_TOLERANCE_MM2 = 1e-7
AREA_RELATIVE_TOLERANCE = 1e-7
PROFILE_FIELDS = {"schema_version", "kind", "source_sha256", "length_unit", "classification_note", "faces"}
FACE_FIELDS = {"surface_type", "area_mm2", "centre_of_mass_mm", "role"}


def _faces(gmsh, solid):
    return [{"tag": tag, "surface_type": gmsh.model.getType(2, tag),
             "area_mm2": gmsh.model.occ.getMass(2, tag),
             "centre_of_mass_mm": list(gmsh.model.occ.getCenterOfMass(2, tag))}
            for _, tag in gmsh.model.getBoundary([solid], combined=False, oriented=False)]


def _profile(path, plan):
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if (not isinstance(value, dict) or set(value) != PROFILE_FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "single_nozzle_boundary_labels" or value["length_unit"] != "mm"
            or value["source_sha256"] != plan["template"]["source_sha256"]):
        raise ValueError("Expected explicit v1 boundary labels for the same STEP SHA-256, in mm.")
    if not isinstance(value["classification_note"], str) or not value["classification_note"].strip():
        raise ValueError("A classification_note describing the boundary label evidence is required.")
    faces = value["faces"]
    if not isinstance(faces, list) or not faces:
        raise ValueError("Label every source boundary face explicitly.")
    for face in faces:
        if not isinstance(face, dict) or set(face) != FACE_FIELDS or face["role"] not in ROLES:
            raise ValueError("Every face requires one explicit supported boundary role.")
        if not isinstance(face["surface_type"], str) or not face["surface_type"].strip():
            raise ValueError("surface_type must be explicit text from the CAD inventory.")
        if number(face["area_mm2"], "area_mm2") <= 0:
            raise ValueError("Face area must be finite and positive.")
        centre = face["centre_of_mass_mm"]
        if not isinstance(centre, list) or len(centre) != 3:
            raise ValueError("Every face requires three centre_of_mass_mm coordinates.")
        for item in centre:
            number(item, "centre_of_mass_mm")
    if set(face["role"] for face in faces) != set(ROLES):
        raise ValueError("All four boundary roles must be nonempty.")
    return value, cad._sha(raw)


def _same_face(actual, reference):
    return (actual["surface_type"] == reference["surface_type"]
            and math.isclose(actual["area_mm2"], reference["area_mm2"],
                             abs_tol=AREA_ABSOLUTE_TOLERANCE_MM2, rel_tol=AREA_RELATIVE_TOLERANCE)
            and np.allclose(actual["centre_of_mass_mm"], reference["centre_of_mass_mm"],
                            atol=cad.LENGTH_TOLERANCE_MM, rtol=0))


def _match_faces(actual, reference):
    if len(actual) != len(reference):
        raise ValueError("Boundary face coverage mismatch: missing or extra faces.")
    assignments, used = [], set()
    for index, face in enumerate(reference, 1):
        matches = [item for item in actual if _same_face(item, face)]
        if len(matches) != 1 or matches[0]["tag"] in used:
            raise ValueError("A source face has no unique CAD match (ambiguous or duplicate labels).")
        used.add(matches[0]["tag"])
        assignments.append({**matches[0], "source_face_index": index, "role": face["role"]})
    return assignments


def _components(gmsh, assignments):
    """Connected surface patches, not a claim about physical nozzle/channel counts."""
    result = {}
    for role in ROLES:
        tags = [item["tag"] for item in assignments if item["role"] == role]
        parent = {tag: tag for tag in tags}
        def find(tag):
            while parent[tag] != tag:
                parent[tag] = parent[parent[tag]]
                tag = parent[tag]
            return tag
        owners = {}
        for tag in tags:
            for _, edge in gmsh.model.getBoundary([(2, tag)], oriented=False):
                if edge in owners:
                    parent[find(tag)] = find(owners[edge])
                owners[edge] = tag
        result[role] = len({find(tag) for tag in tags})
    return result


def inventory_engineering_boundaries(handoff, automation, template_profile, placement):
    """Return a source-face inventory with null roles; never guess physical labels."""
    path, plan = cad._inputs(handoff, automation, template_profile, placement)
    with cad._session() as gmsh:
        gmsh.model.add("boundary_inventory")
        solids = gmsh.model.occ.importShapes(str(path.with_name("template.step")))
        if len(solids) != 1 or solids[0][0] != 3:
            raise ValueError("Boundary inventory requires one 3D source solid.")
        cad._stats(gmsh, solids[0])
        faces = sorted(_faces(gmsh, solids[0]), key=lambda f:
                       (f["surface_type"], f["area_mm2"], f["centre_of_mass_mm"]))
    return {"schema_version": 1, "kind": "single_nozzle_boundary_labels",
            "source_sha256": plan["template"]["source_sha256"], "length_unit": "mm",
            "classification_note": None,
            "faces": [{key: face[key] for key in FACE_FIELDS - {"role"}} | {"role": None} for face in faces]}


def _inputs(handoff, automation, template_profile, placement, cad_build, boundary_profile):
    path, plan = cad._inputs(handoff, automation, template_profile, placement)
    profile, profile_sha = _profile(boundary_profile, plan)
    marker = Path(cad_build).resolve()
    snapshot = {p: cad._sha(p.read_bytes()) for p in
                (marker, *(marker.with_name(name) for name in (*cad.CAD_FILES, cad.REPORT_NAME)),
                 Path(boundary_profile).resolve(), Path(template_profile).resolve(), path)}
    if snapshot[Path(boundary_profile).resolve()] != profile_sha:
        raise ValueError("Boundary labels changed while reading the profile.")
    cad.verify_engineering_cad_build(handoff, automation, template_profile, placement, cad_build)
    _unchanged((handoff, automation, template_profile, placement), snapshot)
    report = cad._read(marker.with_name(cad.REPORT_NAME))
    return path, plan, profile, profile_sha, marker, report, snapshot


def _mapping(gmsh, path, plan, profile, profile_sha, marker, cad_report):
    gmsh.model.add("boundary_source")
    solids = gmsh.model.occ.importShapes(str(path.with_name("template.step")))
    cad._stats(gmsh, solids[0])
    source = _match_faces(_faces(gmsh, solids[0]), profile["faces"])
    components = _components(gmsh, source)
    gmsh.model.remove()
    scale = 1000 if plan["template"]["source_length_unit"] == "m" else 1
    artifacts = {}
    for name in cad.CAD_FILES:
        gmsh.model.add("boundary_" + name)
        solids = gmsh.model.occ.importShapes(str(marker.with_name(name)))
        stats = [cad._stats(gmsh, entity) for entity in solids]
        instances, used, used_faces = [], set(), set()
        for instance, expected in zip(plan["instances"], cad_report["instances"]):
            matches = [i for i, item in enumerate(stats) if np.allclose(item["centre_of_mass_mm"],
                       expected["centre_of_mass_mm"], atol=cad.LENGTH_TOLERANCE_MM, rtol=0)]
            if len(matches) != 1 or matches[0] in used:
                raise ValueError("CAD boundary solid association is not unique.")
            i = matches[0]
            used.add(i)
            matrix = np.asarray(instance["transform_mm_from_source"])
            r, t = matrix[:3, :3] / scale, matrix[:3, 3]
            transformed = [{**face, "centre_of_mass_mm":
                            (r @ np.array(face["centre_of_mass_mm"]) + t).tolist()} for face in source]
            faces = _match_faces(_faces(gmsh, solids[i]), transformed)
            if used_faces.intersection(face["tag"] for face in faces):
                raise ValueError("Nozzle instances share boundary faces unexpectedly.")
            used_faces.update(face["tag"] for face in faces)
            if _components(gmsh, faces) != components:
                raise ValueError("CAD boundary connectivity changed during array export.")
            instances.append({"nozzle_id": instance["nozzle_id"], "volume_tag": solids[i][1], "faces": faces})
        if len(used) != len(solids) or len(used_faces) != len(gmsh.model.getEntities(2)):
            raise ValueError("Array contains unmapped solids or boundary faces.")
        artifacts[name] = {"instances": instances, "physical_surfaces": {
            role: sorted(face["tag"] for item in instances for face in item["faces"] if face["role"] == role)
            for role in ROLES}, "physical_volumes": [item["volume_tag"] for item in instances]}
        gmsh.model.remove()
    return {"schema_version": 1, "kind": "engineering_boundary_mapping",
            **{key: plan[key] for key in ("run_id", "case_id", "cfd_case_id", "nozzle_count")},
            "cad_build_manifest_sha256": cad._sha(marker.read_bytes()), "boundary_profile_sha256": profile_sha,
            "source_sha256": profile["source_sha256"], "classification_note": profile["classification_note"],
            "source_role_face_counts": {role: sum(f["role"] == role for f in source) for role in ROLES},
            "source_role_connected_patch_counts": components,
            "source_role_area_mm2": {role: sum(f["area_mm2"] for f in source if f["role"] == role) for role in ROLES},
            "artifacts": artifacts, "boundary_mapping_ready": True,
            "checks": {"source_face_coverage_unique": True, "array_face_coverage_unique": True,
                       "boundary_connectivity_preserved": True, "step_and_brep_mapped": True},
            "tolerances": {"length_mm": cad.LENGTH_TOLERANCE_MM,
                           "area_absolute_mm2": AREA_ABSOLUTE_TOLERANCE_MM2,
                           "area_relative": AREA_RELATIVE_TOLERANCE},
            "kernel": {"gmsh_version": cad.GMSH_VERSION, "import_target_unit": "mm"},
            "scope": plan["scope"], "physical_boundary_roles_confirmed": False,
            "internal_passage_identity_checked": False, "shared_chamber_built": False,
            "mesh_generated": False, "ready_to_execute": False, "scientific_eligible": False}


def _loader(report):
    mapping = report["artifacts"]["array.brep"]
    def tags(values):
        return ", ".join(str(value) for value in values)
    lines = ['// Gmsh 4.15.2; geometry in mm. No meshing or solver commands.',
             'SetFactory("OpenCASCADE");', 'Geometry.OCCTargetUnit = "MM";', 'Merge "array.brep";',
             f'Physical Volume("internal_fluid", 1) = {{{tags(mapping["physical_volumes"])}}};']
    lines.extend(f'Physical Surface("{role}", {index}) = {{{tags(mapping["physical_surfaces"][role])}}};'
                 for index, role in enumerate(ROLES, 11))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _check_loader(gmsh, loader, report):
    gmsh.model.add("boundary_loader")
    gmsh.merge(str(loader))
    mapping = report["artifacts"]["array.brep"]
    expected = {(3, 1): ("internal_fluid", mapping["physical_volumes"])}
    expected.update({(2, i): (role, mapping["physical_surfaces"][role]) for i, role in enumerate(ROLES, 11)})
    if set(gmsh.model.getPhysicalGroups()) != set(expected):
        raise ValueError("Boundary loader physical groups mismatch.")
    for (dim, tag), (name, entities) in expected.items():
        if (gmsh.model.getPhysicalName(dim, tag) != name
                or sorted(gmsh.model.getEntitiesForPhysicalGroup(dim, tag)) != sorted(entities)):
            raise ValueError("Boundary loader group membership mismatch.")
    # Recheck actual loaded face descriptors: CAD entity tags only apply to this import.
    for item in mapping["instances"]:
        actual = {face["tag"]: face for face in _faces(gmsh, (3, item["volume_tag"]))}
        if set(actual) != {face["tag"] for face in item["faces"]}:
            raise ValueError("Boundary loader face coverage mismatch.")
        if any(not _same_face(actual[face["tag"]], face) for face in item["faces"]):
            raise ValueError("Boundary loader face geometry mismatch.")
    gmsh.model.remove()


def _unchanged(args, snapshot):
    cad._inputs(*args[:4])
    if any(cad._sha(path.read_bytes()) != value for path, value in snapshot.items()):
        raise ValueError("Boundary preparation inputs changed during mapping.")


def prepare_engineering_boundaries(handoff, automation, template_profile, placement, cad_build,
                                   boundary_profile, output_directory):
    """Create a labelled, unmeshed BREP loader and replayable STEP/BREP face mapping."""
    args = (handoff, automation, template_profile, placement, cad_build, boundary_profile)
    directory = Path(output_directory).resolve()
    package = cad._read(Path(handoff).resolve().with_name("cfd_case.json"))
    archive = (Path(handoff).resolve().parent / package["geometry"]["run_manifest"]).resolve().parent
    source = (Path(template_profile).resolve().parent / cad._read(Path(template_profile))["source_step"]).resolve()
    if any(directory.is_relative_to(root) for root in (archive, source.parent, *(Path(p).resolve().parent for p in args))):
        raise ValueError("Boundary output must be outside all source directories.")
    if directory.exists():
        raise FileExistsError("Boundary output already exists.")
    path, plan, profile, profile_sha, marker, cad_report, snapshot = _inputs(*args)
    with cad._session() as gmsh:
        report = _mapping(gmsh, path, plan, profile, profile_sha, marker, cad_report)
        directory.mkdir(parents=True, exist_ok=False)
        manifest = directory / MANIFEST_NAME
        try:
            (directory / "array.brep").write_bytes(marker.with_name("array.brep").read_bytes())
            (directory / LOADER_NAME).write_bytes(_loader(report))
            _check_loader(gmsh, directory / LOADER_NAME, report)
            _unchanged(args, snapshot)
            report["checks"]["physical_group_loader_reimport"] = True
            (directory / REPORT_NAME).write_bytes(cad._bytes(report))
            value = {"schema_version": 1, "kind": "engineering_boundary_mapping", "status": "prepared",
                     "boundary_mapping_ready": True, "ready_to_execute": False,
                     "files": {name: cad._sha((directory / name).read_bytes())
                               for name in ("array.brep", LOADER_NAME, REPORT_NAME)}}
            cad._write_manifest(manifest, value)
        except BaseException as exc:
            cad._write_manifest(manifest, {"schema_version": 1, "kind": "engineering_boundary_mapping",
                "status": "failed", "error_type": type(exc).__name__, "ready_to_execute": False})
            raise
    return manifest


def verify_engineering_boundaries(handoff, automation, template_profile, placement, cad_build,
                                  boundary_profile, manifest_path):
    """Read-only replay from explicit sources, including the labelled BREP loader."""
    args = (handoff, automation, template_profile, placement, cad_build, boundary_profile)
    marker = Path(manifest_path).resolve()
    if marker.name != MANIFEST_NAME:
        raise ValueError("Use boundary_manifest.json without renaming it.")
    saved = cad._read(marker)
    if not isinstance(saved, dict) or saved.get("status") != "prepared":
        raise ValueError("Expected a completed boundary manifest.")
    names = ("array.brep", LOADER_NAME, REPORT_NAME)
    if any(not (marker.parent / name).resolve().is_relative_to(marker.parent) for name in names):
        raise ValueError("Boundary artifact escapes its directory.")
    expected = {"schema_version": 1, "kind": "engineering_boundary_mapping", "status": "prepared",
                "boundary_mapping_ready": True, "ready_to_execute": False,
                "files": {name: cad._sha(marker.with_name(name).read_bytes()) for name in names}}
    if marker.read_bytes() != cad._bytes(expected):
        raise ValueError("Boundary manifest/checksum mismatch.")
    path, plan, profile, profile_sha, built, cad_report, snapshot = _inputs(*args)
    if marker.with_name("array.brep").read_bytes() != built.with_name("array.brep").read_bytes():
        raise ValueError("Boundary BREP differs from the verified CAD build.")
    with cad._session() as gmsh:
        report = _mapping(gmsh, path, plan, profile, profile_sha, built, cad_report)
        # Never execute an arbitrary edited .geo: compare with generated safe bytes first.
        if marker.with_name(LOADER_NAME).read_bytes() != _loader(report):
            raise ValueError("Boundary loader differs from the source replay.")
        _check_loader(gmsh, marker.with_name(LOADER_NAME), report)
        report["checks"]["physical_group_loader_reimport"] = True
        if canonical_json(cad._read(marker.with_name(REPORT_NAME))) != canonical_json(report):
            raise ValueError("Boundary report differs from the verified source replay.")
    _unchanged(args, snapshot)
    return {"boundary_mapping_ready": True, "source_replay_match": True,
            "nozzle_count": plan["nozzle_count"], "physical_boundary_roles_confirmed": False,
            "mesh_generated": False, "ready_to_execute": False, "scientific_eligible": False}
