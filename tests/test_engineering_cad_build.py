"""Real optional CAD-kernel checks on synthetic solids, never Fluent or a mesh."""

import hashlib
import json
import re
import subprocess

import numpy as np
import pytest

import engineering_cad_build as build
from engineering_archive import archive_engineering_candidates
from engineering_cad import export_engineering_cad
from engineering_cfd import prepare_engineering_cfd
from engineering_config import EngineeringGeometryConfig
from engineering_fluent import export_engineering_fluent
from engineering_search import run_engineering_search
from scripts import build_engineering_cad as cli


gmsh = pytest.importorskip("gmsh", reason="Install requirements-engineering-cad.txt for real CAD tests")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("e9")
    report = run_engineering_search(config=EngineeringGeometryConfig("SYNTHETIC CAD TEST ONLY", 14, 60, 2, 3),
        design={"schema_version": 1, "name": "Synthetic CAD solids",
                "groups": [{"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]}]})
    write(root / "search.json", report)
    archive = archive_engineering_candidates(root / "search.json", output_dir=root / "a", selection="all")
    handoff = prepare_engineering_cfd(archive, case_id=read(archive)["cases"][0]["case_id"],
        output_root=root / "c", profile={"schema_version": 1, "operating_condition": {},
        "simulation_config": {"solver": "Fluent", "dimensionality": "3D"}, "required_metrics": ["pressure_loss"]})
    automation = export_engineering_fluent(handoff, root / "f")
    return handoff, automation


def make_template(root, unit="mm", radius=None, multiple=False, origin=None, x=None, axis=None):
    folder = root / "template"
    folder.mkdir()
    step = folder / "source.step"
    gmsh.initialize([], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
        if radius is None:
            gmsh.model.occ.addBox(-2, -12, -1, 4, 12, 2)
        else:
            gmsh.model.occ.addCylinder(0, -12, 0, 0, 12, 0, radius)
        if multiple:
            gmsh.model.occ.addBox(3, -12, -1, 1, 12, 1)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    if unit == "m":
        text = step.read_text(encoding="utf-8")
        def scale_point(match):
            return match[1] + ",".join(format(float(v) / 1000, ".17g") for v in match[2].split(",")) + match[3]
        text = re.sub(r"(CARTESIAN_POINT\s*\('[^']*',\s*\()([^)]*)(\)\))", scale_point, text)
        text = text.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)")
        step.write_text(text, encoding="utf-8")
    profile = folder / "profile.json"
    write(profile, {"schema_version": 1, "kind": "single_nozzle_internal_fluid", "source_step": "source.step",
        "source_sha256": sha(step.read_bytes()), "source_length_unit": unit, "nozzle_outer_diameter_mm": 14,
        "reference_origin": [0, 0, 0] if origin is None else origin,
        "reference_x_direction": [1, 0, 0] if x is None else x,
        "reference_axis_direction": [0, 1, 0] if axis is None else axis})
    return profile


def inputs(sources, root, **options):
    profile = make_template(root, **options)
    placement = export_engineering_cad(*sources, profile, root / "placement")
    return *sources, profile, placement


@pytest.mark.parametrize("options", [
    {}, {"unit": "m"}, {"axis": [0, -1, 0]},
    {"axis": [0, 0, -1], "x": [1 / 2**.5, 1 / 2**.5, 0], "origin": [0, -6, 0]},
    {"unit": "m", "origin": [.001, -.002, 0]},
])
def test_real_cad_copies_units_origins_rotations_and_read_only_replay(sources, tmp_path, options):
    args = inputs(sources, tmp_path, **options)
    source_before = snapshot(sources[0].parents[2])
    before = snapshot(tmp_path)
    marker = build.build_engineering_cad(*args, tmp_path / "out")
    report = read(marker.with_name(build.REPORT_NAME))
    assert report["nozzle_count"] == 4 and report["cad_built"]
    assert report["source_solid"]["volume_mm3"] == pytest.approx(96)
    assert report["total_volume_mm3"] == pytest.approx(384)
    assert len(report["instances"]) == 4
    assert all(report["checks"].values())
    assert not report["mesh_generated"] and not report["ready_to_execute"]
    assert not report["shared_chamber_built"] and not report["internal_passage_identity_checked"]
    assert not gmsh.isInitialized()
    built_before = snapshot(tmp_path)
    result = build.verify_engineering_cad_build(*args, marker)
    assert result["export_geometry_match"] and result["source_replay_match"] and result["nozzle_count"] == 4
    assert not result["ready_to_execute"] and not result["scientific_eligible"]
    assert snapshot(tmp_path) == built_before
    assert all(snapshot(tmp_path)[key] == value for key, value in before.items())
    assert snapshot(sources[0].parents[2]) == source_before
    assert not list(sources[0].parent.glob("attempts/*"))
    assert not gmsh.isInitialized()


@pytest.mark.parametrize("options", [{"radius": 8}, {"multiple": True}])
def test_oversize_or_multiple_solids_are_rejected_before_output(sources, tmp_path, options):
    args = inputs(sources, tmp_path, **options)
    with pytest.raises(ValueError, match="footprint|exactly one"):
        build.build_engineering_cad(*args, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not gmsh.isInitialized()


def test_invalid_brep_is_not_certified_by_step8_unit_check(sources, tmp_path):
    profile = make_template(tmp_path)
    step = profile.parent / "source.step"
    step.write_text("ISO-10303-21;\nDATA;\n#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));\nENDSEC;\nEND-ISO-10303-21;", encoding="ascii")
    value = read(profile)
    value["source_sha256"] = sha(step.read_bytes())
    write(profile, value)
    placement = export_engineering_cad(*sources, profile, tmp_path / "placement")
    with pytest.raises((ValueError, RuntimeError)):
        build.build_engineering_cad(*sources, profile, placement, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not gmsh.isInitialized()


def test_source_corruption_fails_before_initialization(sources, tmp_path, monkeypatch):
    args = inputs(sources, tmp_path)
    target = args[-1].with_name("template.step")
    target.write_bytes(target.read_bytes() + b" ")
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid upstream source must be rejected before kernel initialization")
    monkeypatch.setattr(gmsh, "initialize", forbidden)
    with pytest.raises(ValueError, match="artifact mismatch"):
        build.build_engineering_cad(*args, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("artifact", [*build.CAD_FILES, build.REPORT_NAME, build.MANIFEST_NAME])
def test_changed_artifacts_are_detected(sources, tmp_path, artifact):
    args = inputs(sources, tmp_path)
    marker = build.build_engineering_cad(*args, tmp_path / "out")
    target = marker.with_name(artifact)
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="mismatch"):
        build.verify_engineering_cad_build(*args, marker)


def test_recomputed_hashes_do_not_hide_different_shape_with_same_mass_and_centres(sources, tmp_path):
    args = inputs(sources, tmp_path)
    marker = build.build_engineering_cad(*args, tmp_path / "out")
    report = read(marker.with_name(build.REPORT_NAME))
    gmsh.initialize([], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        for item in report["instances"]:
            x, y, z = item["centre_of_mass_mm"]
            gmsh.model.occ.addBox(x - 1, y - 2, z - 6, 2, 4, 12)
        gmsh.model.occ.synchronize()
        gmsh.write(str(marker.with_name("array.brep")))
    finally:
        gmsh.finalize()
    value = read(marker)
    value["files"]["array.brep"] = sha(marker.with_name("array.brep").read_bytes())
    marker.write_bytes(build._bytes(value))
    with pytest.raises(ValueError, match="symmetric-difference"):
        build.verify_engineering_cad_build(*args, marker)


def test_report_forgery_with_new_hashes_cannot_change_readiness(sources, tmp_path):
    args = inputs(sources, tmp_path)
    marker = build.build_engineering_cad(*args, tmp_path / "out")
    report = read(marker.with_name(build.REPORT_NAME))
    report["ready_to_execute"] = True
    write(marker.with_name(build.REPORT_NAME), report)
    value = read(marker)
    value["files"][build.REPORT_NAME] = sha(marker.with_name(build.REPORT_NAME).read_bytes())
    marker.write_bytes(build._bytes(value))
    with pytest.raises(ValueError, match="report differs"):
        build.verify_engineering_cad_build(*args, marker)


def test_existing_kernel_session_is_preserved(sources, tmp_path):
    args = inputs(sources, tmp_path)
    gmsh.initialize([], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        box = gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        before = gmsh.model.getEntities(3)
        with pytest.raises(ValueError, match="already initialized"):
            build.build_engineering_cad(*args, tmp_path / "out")
        assert gmsh.isInitialized() and gmsh.model.getEntities(3) == before == [(3, box)]
    finally:
        gmsh.finalize()


def test_failed_write_has_failed_marker_and_finalizes_kernel(sources, tmp_path, monkeypatch):
    args = inputs(sources, tmp_path)
    def fail(*args, **kwargs):
        raise OSError("Synthetic CAD export failure")
    monkeypatch.setattr(gmsh, "write", fail)
    with pytest.raises(OSError):
        build.build_engineering_cad(*args, tmp_path / "out")
    marker = tmp_path / "out" / build.MANIFEST_NAME
    assert read(marker)["status"] == "failed"
    assert not gmsh.isInitialized()
    with pytest.raises(ValueError, match="completed"):
        build.verify_engineering_cad_build(*args, marker)


def test_no_overwrite_or_output_inside_inputs(sources, tmp_path):
    args = inputs(sources, tmp_path)
    marker = build.build_engineering_cad(*args, tmp_path / "out")
    before = snapshot(marker.parent)
    with pytest.raises(FileExistsError):
        build.build_engineering_cad(*args, marker.parent)
    for directory in (args[0].parent / "build", args[1].parent / "build", args[2].parent / "build", args[3].parent / "build"):
        with pytest.raises(ValueError, match="outside"):
            build.build_engineering_cad(*args, directory)
        assert not directory.exists()
    assert snapshot(marker.parent) == before


def test_default_cli_does_not_initialize_cad(sources, tmp_path, monkeypatch, capsys):
    args = inputs(sources, tmp_path)
    argv = [token for name, path in zip(("handoff", "automation", "template-profile", "placement"), args)
            for token in ("--" + name, str(path))]
    def forbidden(*args, **kwargs):
        pytest.fail("Default inspect must not initialize the CAD kernel")
    monkeypatch.setattr(gmsh, "initialize", forbidden)
    before = snapshot(tmp_path)
    assert cli.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["placement_plan_valid"] and not result["cad_built"]
    assert snapshot(tmp_path) == before
    with pytest.raises(SystemExit) as exc:
        cli.main([*argv, "--build"])
    assert exc.value.code == 2


@pytest.mark.parametrize("reason", ["missing", "version"])
def test_missing_or_wrong_kernel_is_rejected_without_output(sources, tmp_path, monkeypatch, reason):
    args = inputs(sources, tmp_path)
    if reason == "version":
        monkeypatch.setattr(gmsh, "__version__", "0.0.0")
    else:
        original = build.importlib.import_module
        def unavailable(name, *args, **kwargs):
            if name == "gmsh":
                raise ImportError("Synthetic missing CAD kernel")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(build.importlib, "import_module", unavailable)
    with pytest.raises((RuntimeError, ValueError), match="optional|requires Gmsh"):
        build.build_engineering_cad(*args, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_build_does_not_launch_process_gui_or_mesh(sources, tmp_path, monkeypatch):
    args = inputs(sources, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("CAD build must not launch processes, a GUI, or meshing")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(gmsh.fltk, "run", forbidden)
    monkeypatch.setattr(gmsh.model.mesh, "generate", forbidden)
    marker = build.build_engineering_cad(*args, tmp_path / "out")
    assert build.verify_engineering_cad_build(*args, marker)["cad_built"]


@pytest.mark.parametrize("matrix", [np.diag([-1, 1, 1]), np.diag([2, 2, 2])])
def test_reflection_and_physical_scaling_are_not_rigid_copy_operations(matrix):
    with pytest.raises(ValueError, match="proper rigid rotation"):
        build._rotation(matrix)
