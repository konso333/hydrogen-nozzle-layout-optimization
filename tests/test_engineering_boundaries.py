"""Native CAD face/physical-group replay, including renumbering and forged outputs."""

import json
import re
import subprocess

import pytest

import engineering_boundaries as boundary
import engineering_cad_build as cad
from engineering_archive import archive_engineering_candidates
from engineering_cad import export_engineering_cad
from engineering_cfd import prepare_engineering_cfd
from engineering_config import EngineeringGeometryConfig
from engineering_fluent import export_engineering_fluent
from engineering_search import run_engineering_search
from scripts import prepare_engineering_boundaries as cli

gmsh = pytest.importorskip("gmsh", reason="Install requirements-engineering-cad.txt for real CAD tests")


def write(path, value):
    path.write_bytes(cad._bytes(value))


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module", params=["mm", "m"])
def sources(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("e10")
    report = run_engineering_search(config=EngineeringGeometryConfig("SYNTHETIC BOUNDARY TEST ONLY", 14, 60, 2, 3),
        design={"schema_version": 1, "name": "Synthetic labelled CAD",
                "groups": [{"N": 4, "layout_type": "rectangular", "parameter_sets": [{"spacing": 16}]}]})
    write(root / "search.json", report)
    archive = archive_engineering_candidates(root / "search.json", output_dir=root / "a", selection="all")
    handoff = prepare_engineering_cfd(archive, case_id=cad._read(archive)["cases"][0]["case_id"],
        output_root=root / "c", profile={"schema_version": 1, "operating_condition": {},
        "simulation_config": {"solver": "Fluent", "dimensionality": "3D"}, "required_metrics": ["pressure_loss"]})
    automation = export_engineering_fluent(handoff, root / "f")
    template = root / "template"
    template.mkdir()
    step = template / "source.step"
    with cad._session() as kernel:
        kernel.model.occ.addBox(-2, -12, -1, 4, 12, 2)
        kernel.model.occ.synchronize()
        kernel.write(str(step))
    if request.param == "m":
        text = step.read_text(encoding="utf-8")
        def scaled(match):
            return match[1] + ",".join(format(float(v) / 1000, ".17g") for v in match[2].split(",")) + match[3]
        text = re.sub(r"(CARTESIAN_POINT\s*\('[^']*',\s*\()([^)]*)(\)\))", scaled, text)
        step.write_text(text.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)"), encoding="utf-8")
    profile = template / "profile.json"
    write(profile, {"schema_version": 1, "kind": "single_nozzle_internal_fluid", "source_step": "source.step",
        "source_sha256": cad._sha(step.read_bytes()), "source_length_unit": request.param,
        "nozzle_outer_diameter_mm": 14, "reference_origin": [0, 0, 0],
        "reference_x_direction": [1, 0, 0], "reference_axis_direction": [0, 1, 0]})
    placement = export_engineering_cad(handoff, automation, profile, root / "p")
    basic = (handoff, automation, profile, placement)
    built = cad.build_engineering_cad(*basic, root / "b")
    labels = boundary.inventory_engineering_boundaries(*basic)
    assert all(face["role"] is None for face in labels["faces"])
    labels["classification_note"] = "Synthetic box faces explicitly labelled for software tests only."
    for face in labels["faces"]:
        x, y, _ = face["centre_of_mass_mm"]
        face["role"] = "fuel_inlet" if x == -2 else "oxidizer_inlet" if y == -12 else "outlet" if y == 0 else "wall"
    boundary_profile = template / "labels.json"
    write(boundary_profile, labels)
    return *basic, built, boundary_profile


def test_real_boundary_mapping_complete_and_loader_replays_without_writes(sources, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Boundary preparation must not mesh or launch Fluent, a process, or GUI")
    monkeypatch.setattr(gmsh.model.mesh, "generate", forbidden)
    monkeypatch.setattr(gmsh.fltk, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    before = snapshot(sources[0].parents[2])
    marker = boundary.prepare_engineering_boundaries(*sources, tmp_path / "out")
    report = cad._read(marker.with_name(boundary.REPORT_NAME))
    assert report["source_role_face_counts"] == dict(fuel_inlet=1, oxidizer_inlet=1, outlet=1, wall=3)
    assert report["source_role_connected_patch_counts"] == dict.fromkeys(boundary.ROLES, 1)
    assert report["source_role_area_mm2"] == pytest.approx(dict(fuel_inlet=24, oxidizer_inlet=8, outlet=8, wall=120))
    assert all(report["checks"].values())
    assert report["boundary_mapping_ready"] and report["nozzle_count"] == 4
    for name in cad.CAD_FILES:
        mapping = report["artifacts"][name]
        assert sorted(mapping["physical_volumes"]) == [1, 2, 3, 4]
        assert sorted(len(tags) for tags in mapping["physical_surfaces"].values()) == [4, 4, 4, 12]
        assert len({f["tag"] for item in mapping["instances"] for f in item["faces"]}) == 24
        # Source +Y is now target +Z; fuel face at x=-2 relative to each instance's centre.
        for item, plan in zip(mapping["instances"], cad._read(sources[3].with_name(cad.PLAN_NAME))["instances"]):
            fuel = next(f for f in item["faces"] if f["role"] == "fuel_inlet")
            assert fuel["centre_of_mass_mm"] == pytest.approx([plan["centre_mm"][0] - 2, plan["centre_mm"][1], -6])
    assert not any(report[key] for key in ("mesh_generated", "ready_to_execute", "scientific_eligible",
                                           "physical_boundary_roles_confirmed", "shared_chamber_built"))
    outputs = snapshot(marker.parent)
    result = boundary.verify_engineering_boundaries(*sources, marker)
    assert result["source_replay_match"] and result["boundary_mapping_ready"]
    assert snapshot(marker.parent) == outputs
    assert snapshot(sources[0].parents[2]) == before
    assert not gmsh.isInitialized()


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "unknown_role", "empty_role", "wrong_hash",
                                  "area", "centre", "nonfinite", "bool", "note", "unit"])
def test_bad_source_labels_never_produce_a_prepared_package(sources, tmp_path, damage):
    profile = cad._read(sources[-1])
    if damage == "missing":
        profile["faces"].pop()
    elif damage == "extra":
        profile["faces"].append(profile["faces"][0].copy())
    elif damage == "duplicate":
        profile["faces"][1] = profile["faces"][0].copy()
    elif damage == "unknown_role":
        profile["faces"][0]["role"] = "guessed_inlet"
    elif damage == "empty_role":
        for face in profile["faces"]:
            if face["role"] == "fuel_inlet":
                face["role"] = "wall"
    elif damage == "wrong_hash":
        profile["source_sha256"] = "0" * 64
    elif damage == "area":
        profile["faces"][0]["area_mm2"] += 1
    elif damage == "centre":
        profile["faces"][0]["centre_of_mass_mm"][0] += .001
    elif damage == "nonfinite":
        profile["faces"][0]["area_mm2"] = float("nan")
    elif damage == "bool":
        profile["faces"][0]["centre_of_mass_mm"][0] = True
    elif damage == "note":
        profile["classification_note"] = None
    else:
        profile["length_unit"] = "m"
    (tmp_path / "profile").mkdir()
    path = tmp_path / "profile" / "labels.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ValueError):
        boundary.prepare_engineering_boundaries(*sources[:-1], path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not gmsh.isInitialized()


@pytest.mark.parametrize("damage", ["report", "loader", "brep"])
def test_rehashed_outputs_cannot_forge_mapping_or_execute_edited_loader(sources, tmp_path, monkeypatch, damage):
    marker = boundary.prepare_engineering_boundaries(*sources, tmp_path / "out")
    manifest = cad._read(marker)
    if damage == "report":
        name = boundary.REPORT_NAME
        report = cad._read(marker.with_name(name))
        report["ready_to_execute"] = True
        write(marker.with_name(name), report)
    elif damage == "loader":
        name = boundary.LOADER_NAME
        marker.with_name(name).write_text('SystemCall "invalid-command";\n', encoding="utf-8")
        def forbidden(*args, **kwargs):
            pytest.fail("An edited .geo file must be rejected before parsing")
        monkeypatch.setattr(gmsh, "merge", forbidden)
    else:
        name = "array.brep"
        marker.with_name(name).write_bytes(marker.with_name(name).read_bytes() + b"\n")
    manifest["files"][name] = cad._sha(marker.with_name(name).read_bytes())
    write(marker, manifest)
    with pytest.raises(ValueError, match="source replay|verified CAD"):
        boundary.verify_engineering_boundaries(*sources, marker)
    assert not gmsh.isInitialized()


def test_failed_loader_marks_failure_and_preserves_sources(sources, tmp_path, monkeypatch):
    before = snapshot(sources[0].parents[2])
    def failure(*args, **kwargs):
        raise RuntimeError("Synthetic loader failure")
    monkeypatch.setattr(boundary, "_check_loader", failure)
    with pytest.raises(RuntimeError, match="Synthetic"):
        boundary.prepare_engineering_boundaries(*sources, tmp_path / "out")
    marker = tmp_path / "out" / boundary.MANIFEST_NAME
    assert cad._read(marker)["status"] == "failed"
    with pytest.raises(ValueError, match="completed"):
        boundary.verify_engineering_boundaries(*sources, marker)
    assert snapshot(sources[0].parents[2]) == before
    assert not gmsh.isInitialized()


def test_cad_corruption_is_rejected_before_mapping(sources, tmp_path, monkeypatch):
    built = sources[4]
    damaged = tmp_path / "cad"
    damaged.mkdir()
    for path in built.parent.iterdir():
        (damaged / path.name).write_bytes(path.read_bytes())
    (damaged / "array.step").write_bytes(b"not STEP")
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid CAD must be rejected before boundary mapping")
    monkeypatch.setattr(boundary, "_mapping", forbidden)
    with pytest.raises(ValueError, match="checksum"):
        boundary.prepare_engineering_boundaries(*sources[:4], damaged / cad.MANIFEST_NAME, sources[-1], tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_cli_inspect_inventory_and_required_explicit_arguments(sources, tmp_path, monkeypatch, capsys):
    argv = [value for name, path in zip(("handoff", "automation", "template-profile", "placement"), sources[:4])
            for value in ("--" + name, str(path))]
    with monkeypatch.context() as scoped:
        def forbidden(*args, **kwargs):
            pytest.fail("Default inspect must not initialize CAD")
        scoped.setattr(gmsh, "initialize", forbidden)
        assert cli.main(argv) == 0
    assert not json.loads(capsys.readouterr().out)["boundary_mapping_ready"]
    assert cli.main([*argv, "--inventory"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert len(inventory["faces"]) == 6 and all(f["role"] is None for f in inventory["faces"])
    for extra in (["--prepare", "--output-dir", str(tmp_path / "out")], ["--cad-build", str(sources[4])]):
        with pytest.raises(SystemExit) as exc:
            cli.main([*argv, *extra])
        assert exc.value.code == 2


def test_output_cannot_overwrite_or_enter_any_input_directory(sources, tmp_path):
    for directory in (sources[-1].parent / "new", sources[4].parent / "new", sources[0].parent / "new"):
        with pytest.raises(ValueError, match="outside"):
            boundary.prepare_engineering_boundaries(*sources, directory)
        assert not directory.exists()
    directory = tmp_path / "existing"
    directory.mkdir()
    with pytest.raises(FileExistsError):
        boundary.prepare_engineering_boundaries(*sources, directory)


def test_ambiguous_geometric_selectors_fail_closed():
    face = dict(tag=1, surface_type="Plane", area_mm2=1., centre_of_mass_mm=[0., 0., 0.])
    with pytest.raises(ValueError, match="unique"):
        boundary._match_faces([face, face | {"tag": 2}], [face | {"role": "wall"}, face | {"role": "outlet"}])


def test_profile_change_during_preparation_cannot_publish_success(sources, tmp_path, monkeypatch):
    folder = tmp_path / "profile"
    folder.mkdir()
    profile = folder / "labels.json"
    profile.write_bytes(sources[-1].read_bytes())
    original = boundary._check_loader
    def changed(*args):
        original(*args)
        profile.write_bytes(profile.read_bytes() + b" ")
    monkeypatch.setattr(boundary, "_check_loader", changed)
    with pytest.raises(ValueError, match="inputs changed"):
        boundary.prepare_engineering_boundaries(*sources[:-1], profile, tmp_path / "out")
    assert cad._read(tmp_path / "out" / boundary.MANIFEST_NAME)["status"] == "failed"


def test_boundary_inventory_preserves_callers_existing_kernel(sources):
    with cad._session() as kernel:
        box = kernel.model.occ.addBox(0, 0, 0, 1, 1, 1)
        kernel.model.occ.synchronize()
        with pytest.raises(ValueError, match="already initialized"):
            boundary.inventory_engineering_boundaries(*sources[:4])
        assert kernel.isInitialized() and kernel.model.getEntities(3) == [(3, box)]
