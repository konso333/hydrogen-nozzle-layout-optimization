"""Synthetic placement algebra and source replay; no CAD kernel or CFD results."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

import engineering_cad as cad
from engineering_archive import archive_engineering_candidates
from engineering_cfd import prepare_engineering_cfd
from engineering_config import EngineeringGeometryConfig
from engineering_fluent import export_engineering_fluent
from engineering_search import run_engineering_search
from scripts import prepare_engineering_cad as cli


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot(path):
    return {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("e8")
    report = run_engineering_search(config=EngineeringGeometryConfig("SYNTHETIC TEST ONLY", 14, 60, 2, 3),
        design={"schema_version": 1, "name": "Synthetic CAD placement",
                "groups": [{"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]}]})
    write(root / "search.json", report)
    archive = archive_engineering_candidates(root / "search.json", output_dir=root / "a", selection="all")
    handoff = prepare_engineering_cfd(archive, case_id=read(archive)["cases"][0]["case_id"],
        output_root=root / "c", profile={"schema_version": 1, "operating_condition": {},
        "simulation_config": {"solver": "Fluent", "dimensionality": "3D"}, "required_metrics": ["pressure_loss"]})
    automation = export_engineering_fluent(handoff, root / "f")
    return handoff, automation


def template(tmp_path, unit="mm", **changes):
    directory = tmp_path / "template"
    directory.mkdir()
    prefix = ".MILLI." if unit == "mm" else "$"
    # Minimal unit-bearing text only; deliberately not a real BRep, which this layer cannot certify.
    raw = ("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
           f"#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT({prefix},.METRE.));\n"
           "ENDSEC;\nEND-ISO-10303-21;\n").encode("ascii")
    (directory / "source.step").write_bytes(raw)
    profile = {"schema_version": 1, "kind": "single_nozzle_internal_fluid", "source_step": "source.step",
               "source_sha256": sha(raw), "source_length_unit": unit, "nozzle_outer_diameter_mm": 14,
               "reference_origin": [0, 0, 0], "reference_x_direction": [1, 0, 0],
               "reference_axis_direction": [0, 1, 0], **changes}
    path = directory / "profile.json"
    write(path, profile)
    return path


def export(sources, profile, output):
    return cad.export_engineering_cad(*sources, profile, output)


def verify(sources, profile, marker):
    return cad.verify_engineering_cad(*sources, profile, marker)


@pytest.mark.parametrize("unit,origin,x,axis", [
    ("mm", [0, 0, 0], [1, 0, 0], [0, 1, 0]),
    ("m", [.002, -.032, .001], [0, 0, -1], [0, 1, 0]),
    ("mm", [10, -32, 7], [1, 0, 0], [0, 0, 1]),
    ("mm", [1, 2, 3], [1 / 2**.5, 1 / 2**.5, 0], [0, 0, -1]),
])
def test_mapping_origin_axes_units_and_preserving_chirality(sources, tmp_path, unit, origin, x, axis):
    profile = template(tmp_path, unit=unit, reference_origin=origin, reference_x_direction=x,
                       reference_axis_direction=axis)
    source_before = snapshot(sources[0].parents[2])
    profile_before = snapshot(profile.parent)
    marker = export(sources, profile, tmp_path / "out")
    plan = read(marker.with_name(cad.PLAN_NAME))
    assert plan["nozzle_count"] == 4
    assert plan["template"]["file"] == "template.step" and "source_step" not in plan["template"]
    assert not plan["source_geometry_checked"] and not plan["cad_built"] and not plan["mesh_generated"]
    assert not plan["ready_to_execute"] and not plan["scientific_eligible"]
    assert plan["cfd_case_id"] == read(sources[0])["cfd_case_id"]
    scale = 1000 if unit == "m" else 1
    y = np.cross(axis, x)
    for instance in plan["instances"]:
        matrix = np.array(instance["transform_mm_from_source"])
        rotation = matrix[:3, :3] / scale
        centre = np.array(instance["centre_mm"])
        assert np.linalg.det(rotation) == pytest.approx(1)
        assert rotation @ rotation.T == pytest.approx(np.eye(3))
        assert (matrix @ [*origin, 1])[:3] == pytest.approx(centre)
        # One physical mm along each source reference axis becomes one target mm.
        for vector, expected in ((x, [1, 0, 0]), (y, [0, 1, 0]), (axis, [0, 0, 1])):
            point = np.array(origin) + np.array(vector) / scale
            assert (matrix @ [*point, 1])[:3] == pytest.approx(centre + expected)
    assert marker.with_name("coordinates_mm.csv").read_bytes() == (sources[0].parent / "geometry/coordinates.csv").read_bytes()
    assert marker.with_name("template.step").read_bytes() == (profile.parent / "source.step").read_bytes()
    before = snapshot(marker.parent)
    assert verify(sources, profile, marker) == {"placement_plan_valid": True, "source_geometry_checked": False,
        "cad_built": False, "mesh_generated": False, "ready_to_execute": False, "scientific_eligible": False}
    assert snapshot(marker.parent) == before
    assert snapshot(sources[0].parents[2]) == source_before
    assert snapshot(profile.parent) == profile_before
    assert not list(sources[0].parent.glob("attempts/*"))


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"kind": "hardware"}, {"source_step": None},
    {"source_step": "old.msh"}, {"source_step": "old.cas.h5"}, {"source_sha256": None},
    {"source_sha256": "0" * 64}, {"source_length_unit": None}, {"source_length_unit": "m"},
    {"nozzle_outer_diameter_mm": 4}, {"nozzle_outer_diameter_mm": True},
    {"nozzle_outer_diameter_mm": float("nan")}, {"reference_origin": None},
    {"reference_origin": [0, 0]}, {"reference_origin": [0, False, 0]},
    {"reference_origin": [0, float("inf"), 0]}, {"reference_axis_direction": None},
    {"reference_axis_direction": [0, 0, 0]}, {"reference_axis_direction": [0, 2, 0]},
    {"reference_x_direction": [0, 1, 0]}, {"reference_x_direction": [1, .01, 0]},
    {"source_length_unit": "inch"}, {"additional_override": True},
])
def test_invalid_or_unknown_template_inputs_create_no_output(sources, tmp_path, changes):
    profile = template(tmp_path, **changes)
    with pytest.raises((ValueError, OSError)):
        export(sources, profile, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("body", [
    "#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));\n#2=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.));",
    "#1=(LENGTH_UNIT() NAMED_UNIT(*) CONVERSION_BASED_UNIT('INCH',#2));",
    "#1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.CENTI.,.METRE.));",
    "/* #1=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.)); */",
    "#1=DESCRIPTION('#2=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));');",
])
def test_unsupported_or_missing_step_unit_entities_are_rejected(sources, tmp_path, body):
    profile = template(tmp_path)
    raw = ("ISO-10303-21;\nDATA;\n" + body + "\nENDSEC;\nEND-ISO-10303-21;\n").encode("ascii")
    (profile.parent / "source.step").write_bytes(raw)
    value = read(profile)
    value["source_sha256"] = sha(raw)
    write(profile, value)
    with pytest.raises(ValueError):
        export(sources, profile, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("artifact", [cad.PLAN_NAME, "template.step", "coordinates_mm.csv", cad.MANIFEST_NAME])
def test_modified_files_cannot_pass_replay(sources, tmp_path, artifact):
    profile = template(tmp_path)
    marker = export(sources, profile, tmp_path / "out")
    target = marker.with_name(artifact)
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="mismatch"):
        verify(sources, profile, marker)


def test_forged_matrix_and_recomputed_checksums_cannot_pass(sources, tmp_path):
    profile = template(tmp_path)
    marker = export(sources, profile, tmp_path / "out")
    path = marker.with_name(cad.PLAN_NAME)
    value = read(path)
    value["instances"][0]["transform_mm_from_source"][0][3] += 1
    write(path, value)
    manifest = read(marker)
    manifest["files"][cad.PLAN_NAME] = sha(path.read_bytes())
    manifest["placement_digest"] = sha(cad.canonical_json(value).encode("utf-8"))
    marker.write_bytes(cad._bytes(manifest))
    with pytest.raises(ValueError, match="manifest mismatch"):
        verify(sources, profile, marker)


@pytest.mark.parametrize("dimensionality", ["2D", None])
def test_incompatible_or_unknown_3d_scope_creates_no_output(sources, tmp_path, dimensionality):
    handoff = prepare_engineering_cfd(
        sources[0].parents[2] / "a" / read(sources[0])["run_id"] / "engineering_run.json",
        case_id=read(sources[0])["case_id"], output_root=tmp_path / "c",
        profile={"schema_version": 1, "operating_condition": {},
                 "simulation_config": {"solver": "Fluent", "dimensionality": dimensionality},
                 "required_metrics": ["pressure_loss"]})
    automation = export_engineering_fluent(handoff, tmp_path / "f")
    profile = template(tmp_path)
    with pytest.raises(ValueError, match="3D"):
        export((handoff, automation), profile, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("target", ["step", "profile", "automation", "handoff"])
def test_changed_explicit_source_input_is_detected(sources, tmp_path, target):
    # Isolate the full M2/M6 dependency tree so this test cannot corrupt shared inputs.
    moved = tmp_path / "sources"
    original = sources[0].parents[2]
    shutil.copytree(original, moved)
    copied = tuple(moved / path.relative_to(original) for path in sources)
    profile = template(tmp_path)
    marker = export(copied, profile, tmp_path / "out")
    changed = {"step": profile.parent / "source.step", "profile": profile,
               "automation": copied[1].with_name("coordinates_mm.csv"), "handoff": copied[0]}[target]
    if target == "profile":
        value = read(changed)
        value["source_length_unit"] = "m"
        write(changed, value)
    elif target == "handoff":
        value = read(changed)
        value["configuration"]["nozzle_outer_diameter_mm"] = 4
        write(changed, value)
    else:
        changed.write_bytes(changed.read_bytes() + b" ")
    with pytest.raises(ValueError):
        verify(copied, profile, marker)
    with pytest.raises(ValueError):
        export(copied, profile, tmp_path / "other")
    assert not (tmp_path / "other").exists()


def test_no_process_launch_no_source_writes_and_no_overwrite(sources, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("CAD preparation must not launch any process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    profile = template(tmp_path)
    marker = export(sources, profile, tmp_path / "out")
    before = snapshot(marker.parent)
    with pytest.raises(FileExistsError):
        export(sources, profile, marker.parent)
    for directory in (sources[0].parent / "cad", sources[1].parent / "cad", profile.parent / "cad"):
        with pytest.raises(ValueError, match="outside"):
            export(sources, profile, directory)
        assert not directory.exists()
    verify(sources, profile, marker)
    assert snapshot(marker.parent) == before


def test_later_write_failure_is_not_a_prepared_plan(sources, tmp_path, monkeypatch):
    profile = template(tmp_path)
    original = Path.write_bytes
    def fail_template(path, raw):
        if path.name == "template.step":
            raise OSError("Synthetic CAD template copy failure")
        return original(path, raw)
    monkeypatch.setattr(Path, "write_bytes", fail_template)
    with pytest.raises(OSError):
        export(sources, profile, tmp_path / "out")
    marker = tmp_path / "out" / cad.MANIFEST_NAME
    assert read(marker)["status"] == "failed"
    with pytest.raises(ValueError, match="prepared"):
        verify(sources, profile, marker)


def test_cli_export_verify_and_unknown_tracked_template(sources, tmp_path, capsys):
    profile = template(tmp_path)
    common = ["--handoff", str(sources[0]), "--automation", str(sources[1]), "--template-profile", str(profile)]
    assert cli.main([*common, "--output-dir", str(tmp_path / "out")]) == 0
    capsys.readouterr()
    assert cli.main([*common, "--verify", str(tmp_path / "out" / cad.MANIFEST_NAME)]) == 0
    assert json.loads(capsys.readouterr().out)["placement_plan_valid"]
    common[-1] = str(ROOT / "examples/engineering_cad_template.template.json")
    with pytest.raises(SystemExit) as exc:
        cli.main([*common, "--output-dir", str(tmp_path / "invalid")])
    assert exc.value.code == 2
    assert not (tmp_path / "invalid").exists()
    config = read(ROOT / "examples/engineering_nozzle_14mm.json")
    assert all(config[key] is None for key in ("installation_radius_mm", "nozzle_edge_gap_mm", "wall_clearance_mm"))
