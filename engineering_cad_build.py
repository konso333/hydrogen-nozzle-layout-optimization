"""Build independent internal-flow CAD copies, without meshing or a CFD solver."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np

from engineering_cad import PLAN_NAME, verify_engineering_cad
from experiments.spec import canonical_json


GMSH_VERSION = "4.15.2"
MANIFEST_NAME = "cad_build_manifest.json"
REPORT_NAME = "cad_build_report.json"
CAD_FILES = ("array.brep", "array.step")
LENGTH_TOLERANCE_MM = 1e-6
VOLUME_ABSOLUTE_TOLERANCE_MM3 = 1e-7
VOLUME_RELATIVE_TOLERANCE = 1e-8


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_manifest(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(_bytes(value))
    temporary.replace(path)


def _inputs(handoff, automation, template_profile, placement):
    verify_engineering_cad(handoff, automation, template_profile, placement)
    path = Path(placement).resolve()
    plan = _read(path.with_name(PLAN_NAME))
    return path, plan


def inspect_engineering_cad_build(handoff, automation, template_profile, placement):
    """Verify preparation inputs without importing/initializing the optional CAD kernel."""
    _, plan = _inputs(handoff, automation, template_profile, placement)
    return {"placement_plan_valid": True, "nozzle_count": plan["nozzle_count"],
            "gmsh_available": importlib.util.find_spec("gmsh") is not None,
            "required_gmsh_version": GMSH_VERSION, "cad_built": False, "mesh_generated": False,
            "ready_to_execute": False}


@contextmanager
def _session():
    try:
        gmsh = importlib.import_module("gmsh")
    except (ImportError, OSError) as exc:
        raise RuntimeError("Install the optional requirements-engineering-cad.txt to build CAD.") from exc
    if gmsh.__version__ != GMSH_VERSION:
        raise ValueError(f"This CAD adapter requires Gmsh {GMSH_VERSION}.")
    if gmsh.isInitialized():
        raise ValueError("Refusing to disturb an already initialized Gmsh session.")
    gmsh.initialize([], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Geometry.OCCParallel", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
        gmsh.option.setString("Geometry.OCCSTEPModelName", "engineering_internal_passage_array")
        gmsh.option.setString("Geometry.OCCSTEPTimeStamp", "1970-01-01T00:00:00")
        yield gmsh
    except (ValueError, OSError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError(f"CAD kernel failure: {exc}") from exc
    finally:
        gmsh.finalize()


def _rotation(matrix):
    """Convert a proper rotation to axis/angle, including zero and 180 degrees."""
    r = np.asarray(matrix, dtype=float)
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-9, rtol=0) or not math.isclose(np.linalg.det(r), 1, abs_tol=1e-9):
        raise ValueError("CAD copies require a proper rigid rotation.")
    trace = float(np.trace(r))
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = [s / 4, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(r)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(1 + r[i, i] - r[j, j] - r[k, k]) * 2
        q = [(r[k, j] - r[j, k]) / s, 0., 0., 0.]
        q[i + 1], q[j + 1], q[k + 1] = s / 4, (r[j, i] + r[i, j]) / s, (r[k, i] + r[i, k]) / s
    q = np.array(q) / np.linalg.norm(q)
    sine = float(np.linalg.norm(q[1:]))
    return ([1., 0., 0.], 0.) if sine < 1e-14 else ((q[1:] / sine).tolist(), 2 * math.atan2(sine, q[0]))


def _stats(gmsh, entity):
    gmsh.model.occ.synchronize()
    mass = gmsh.model.occ.getMass(*entity)
    centre = list(gmsh.model.occ.getCenterOfMass(*entity))
    box = list(gmsh.model.getBoundingBox(*entity))
    faces = gmsh.model.getBoundary([entity], combined=False, oriented=False)
    if not faces or not math.isfinite(mass) or mass <= 0 or not all(math.isfinite(v) for v in centre + box):
        raise ValueError("CAD requires a finite, positive-volume BRep solid with boundary faces.")
    return {"volume_mm3": mass, "centre_of_mass_mm": centre, "bounding_box_mm": box,
            "surface_count": len(faces)}


def _volume_tolerance(volume):
    return max(VOLUME_ABSOLUTE_TOLERANCE_MM3, volume * VOLUME_RELATIVE_TOLERANCE)


def _difference_volume(gmsh, left, right):
    a, b = gmsh.model.occ.copy([left]), gmsh.model.occ.copy([right])
    difference, _ = gmsh.model.occ.cut(a, b)
    volume = sum(gmsh.model.occ.getMass(d, t) for d, t in difference if d == 3)
    if difference:
        gmsh.model.occ.remove(difference, recursive=True)
    return volume


def _construct(gmsh, path, plan):
    gmsh.model.add("engineering_internal_passages")
    template = gmsh.model.occ.importShapes(str(path.with_name("template.step")))
    if len(template) != 1 or template[0][0] != 3:
        raise ValueError("The internal-flow template must contain exactly one 3D solid.")
    source = _stats(gmsh, template[0])
    scale = 1000 if plan["template"]["source_length_unit"] == "m" else 1
    entities, instances = [], []
    for instance in plan["instances"]:
        matrix = np.array(instance["transform_mm_from_source"], dtype=float)
        # STEP import already converted to mm; never apply the source-unit scale twice.
        r, translation = matrix[:3, :3] / scale, matrix[:3, 3]
        axis, angle = _rotation(r)
        copied = gmsh.model.occ.copy(template)
        if angle:
            gmsh.model.occ.rotate(copied, 0, 0, 0, *axis, angle)
        gmsh.model.occ.translate(copied, *translation)
        stats = _stats(gmsh, copied[0])
        expected_centre = r @ np.array(source["centre_of_mass_mm"]) + translation
        if (abs(stats["volume_mm3"] - source["volume_mm3"]) > _volume_tolerance(source["volume_mm3"])
                or not np.allclose(stats["centre_of_mass_mm"], expected_centre, atol=LENGTH_TOLERANCE_MM, rtol=0)
                or stats["surface_count"] != source["surface_count"]):
            raise ValueError("Rigid CAD copying changed template volume, position or surface count.")
        entities.append(copied[0])
        instances.append({"nozzle_id": instance["nozzle_id"], **stats})
    gmsh.model.occ.remove(template, recursive=True)
    gmsh.model.occ.synchronize()
    if len(gmsh.model.getEntities(3)) != plan["nozzle_count"]:
        raise ValueError("Constructed CAD solid count differs from the verified layout.")
    # Identical orientations allow one cylindrical containment test for all translated copies.
    box = instances[0]["bounding_box_mm"]
    centre = plan["instances"][0]["centre_mm"]
    margin = max(1., box[5] - box[2]) * 1e-4
    cylinder = (3, gmsh.model.occ.addCylinder(centre[0], centre[1], box[2] - margin,
        0, 0, box[5] - box[2] + 2 * margin, plan["template"]["nozzle_outer_diameter_mm"] / 2))
    outside = _difference_volume(gmsh, entities[0], cylinder)
    gmsh.model.occ.remove([cylinder], recursive=True)
    if outside > _volume_tolerance(source["volume_mm3"]):
        raise ValueError("CAD template extends outside its declared nozzle footprint cylinder.")
    gmsh.model.occ.synchronize()
    report = {"schema_version": 1, "kind": "engineering_cad_build", "cad_built": True,
              "kernel": {"gmsh_version": GMSH_VERSION, "import_target_unit": "mm", "threads": 1},
              **{key: plan[key] for key in ("run_id", "case_id", "cfd_case_id", "nozzle_count")},
              "placement_manifest_sha256": _sha(path.read_bytes()),
              "source_template_sha256": plan["template"]["source_sha256"], "source_solid": source,
              "instances": instances, "total_volume_mm3": sum(item["volume_mm3"] for item in instances),
              "checks": {"single_source_solid": True, "rigid_copy_volume_and_position": True,
                         "declared_footprint_cylinder": True, "layout_solid_count": True},
              "tolerances": {"length_mm": LENGTH_TOLERANCE_MM,
                  "volume_absolute_mm3": VOLUME_ABSOLUTE_TOLERANCE_MM3, "volume_relative": VOLUME_RELATIVE_TOLERANCE},
              "scope": plan["scope"], "internal_passage_identity_checked": False,
              "shared_chamber_built": False, "boundary_mapping_ready": False,
              "mesh_generated": False, "ready_to_execute": False, "scientific_eligible": False}
    return entities, report


def _check_export(gmsh, file, expected, report):
    actual = gmsh.model.occ.importShapes(str(file))
    if len(actual) != len(expected) or any(d != 3 for d, _ in actual):
        raise ValueError("Reimported CAD solid count mismatch.")
    stats = [_stats(gmsh, entity) for entity in actual]
    unmatched = set(range(len(actual)))
    for entity, reference in zip(expected, report["instances"]):
        matches = [i for i in unmatched if np.allclose(stats[i]["centre_of_mass_mm"],
                   reference["centre_of_mass_mm"], atol=LENGTH_TOLERANCE_MM, rtol=0)]
        if len(matches) != 1:
            raise ValueError("Reimported CAD does not uniquely match a nozzle position.")
        i = matches[0]
        unmatched.remove(i)
        if (abs(stats[i]["volume_mm3"] - reference["volume_mm3"]) > _volume_tolerance(reference["volume_mm3"])
                or stats[i]["surface_count"] != reference["surface_count"]):
            raise ValueError("Reimported CAD volume or surface count mismatch.")
        difference = _difference_volume(gmsh, entity, actual[i]) + _difference_volume(gmsh, actual[i], entity)
        if difference > _volume_tolerance(reference["volume_mm3"]):
            raise ValueError("Reimported CAD symmetric-difference volume exceeds tolerance.")
    gmsh.model.occ.remove(actual, recursive=True)
    gmsh.model.occ.synchronize()


def build_engineering_cad(handoff, automation, template_profile, placement, output_directory):
    """Build actual STEP/BREP copies in a fresh directory; never launch Fluent."""
    path, plan = _inputs(handoff, automation, template_profile, placement)
    directory = Path(output_directory).resolve()
    package = _read(Path(handoff).resolve().with_name("cfd_case.json"))
    archive = (Path(handoff).resolve().parent / package["geometry"]["run_manifest"]).resolve().parent
    profile = Path(template_profile).resolve()
    template = (profile.parent / _read(profile)["source_step"]).resolve()
    protected = (path.parent, Path(handoff).resolve().parent, Path(automation).resolve().parent,
                 archive, profile.parent, template.parent)
    if any(directory.is_relative_to(root) for root in protected):
        raise ValueError("CAD build output must be outside all source directories.")
    if directory.exists():
        raise FileExistsError("CAD build output already exists.")
    # Refused dependencies/active sessions and invalid BReps create no output directory.
    with _session() as gmsh:
        entities, report = _construct(gmsh, path, plan)
        directory.mkdir(parents=True, exist_ok=False)
        marker = directory / MANIFEST_NAME
        try:
            for name in CAD_FILES:
                gmsh.write(str(directory / name))
                _check_export(gmsh, directory / name, entities, report)
            _, final_plan = _inputs(handoff, automation, template_profile, placement)
            if canonical_json(final_plan) != canonical_json(plan):
                raise ValueError("CAD placement inputs changed during construction.")
            report["checks"]["export_reimport_symmetric_difference"] = True
            (directory / REPORT_NAME).write_bytes(_bytes(report))
            manifest = {"schema_version": 1, "kind": "engineering_cad_build", "status": "built",
                        "cad_built": True, "ready_to_execute": False,
                        "files": {name: _sha((directory / name).read_bytes()) for name in (*CAD_FILES, REPORT_NAME)}}
            _write_manifest(marker, manifest)
        except BaseException as exc:
            _write_manifest(marker, {"schema_version": 1, "kind": "engineering_cad_build", "status": "failed",
                                     "error_type": type(exc).__name__, "ready_to_execute": False})
            raise
    return marker


def verify_engineering_cad_build(handoff, automation, template_profile, placement, manifest_path):
    """Rebuild in memory, then reimport/compare both saved CAD files without writes."""
    path, plan = _inputs(handoff, automation, template_profile, placement)
    marker = Path(manifest_path).resolve()
    if marker.name != MANIFEST_NAME:
        raise ValueError("Use cad_build_manifest.json without renaming it.")
    saved = _read(marker)
    if not isinstance(saved, dict) or saved.get("status") != "built":
        raise ValueError("Expected a completed CAD build manifest.")
    names = (*CAD_FILES, REPORT_NAME)
    for name in names:
        if not (marker.parent / name).resolve().is_relative_to(marker.parent):
            raise ValueError("CAD build artifact escapes its directory.")
    expected = {"schema_version": 1, "kind": "engineering_cad_build", "status": "built",
                "cad_built": True, "ready_to_execute": False,
                "files": {name: _sha((marker.parent / name).read_bytes()) for name in names}}
    if marker.read_bytes() != _bytes(expected):
        raise ValueError("CAD build manifest/checksum mismatch.")
    with _session() as gmsh:
        entities, report = _construct(gmsh, path, plan)
        for name in CAD_FILES:
            _check_export(gmsh, marker.parent / name, entities, report)
        report["checks"]["export_reimport_symmetric_difference"] = True
        if canonical_json(_read(marker.parent / REPORT_NAME)) != canonical_json(report):
            raise ValueError("CAD build report differs from the verified source replay.")
    return {"cad_built": True, "source_replay_match": True, "export_geometry_match": True,
            "nozzle_count": plan["nozzle_count"], "internal_passage_identity_checked": False,
            "mesh_generated": False, "ready_to_execute": False, "scientific_eligible": False}
