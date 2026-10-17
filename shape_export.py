"""New-directory exports and replay checks for independent shape searches."""

import csv
import hashlib
import io
import json
from pathlib import Path

from experiments.provenance import collect_provenance
from experiments.spec import canonical_json
from io_utils.export_csv import export_coordinates
from shape_boundary import MountingBoundary
from shape_search import run_shape_search


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plot_candidate(candidate, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Wedge

    spec = candidate["specification"]
    boundary = MountingBoundary(**spec["boundary"])
    views = [{"name": "mounting", "boundary": spec["boundary"], **spec["nozzle"]},
             *spec.get("sections", [])]
    columns = min(3, len(views))
    rows = (len(views) + columns - 1) // columns
    fig, axes = plt.subplots(rows, columns, figsize=(6*columns, 6*rows), squeeze=False)
    try:
        for ax, view in zip(axes.flat, views):
            boundary = MountingBoundary(**view["boundary"])
            if boundary.kind == "circle":
                outline = Circle((0, 0), boundary.radius_mm, fill=False, linewidth=2)
            else:
                outline = Wedge((0, 0), boundary.radius_mm, boundary.start_angle_deg,
                                boundary.start_angle_deg + boundary.sweep_angle_deg,
                                fill=False, linewidth=2)
            ax.add_patch(outline)
            for x, y in candidate["coordinates_mm"]:
                ax.add_patch(Circle((x, y), view["outer_diameter_mm"] / 2,
                                    color="#1b8a8f", alpha=.8))
            radius = boundary.radius_mm * 1.08
            ax.set(xlim=(-radius, radius), ylim=(-radius, radius), aspect="equal",
                   xlabel="x / mm", ylabel="y / mm",
                   title=f"{spec['layout']['family']} | {view['name']} | N={spec['N']}\n"
                         f"D={view['outer_diameter_mm']:g} mm; R={boundary.radius_mm:g} mm")
            ax.grid(alpha=.2)
        for ax in list(axes.flat)[len(views):]:
            ax.set_visible(False)
        fig.suptitle("Section envelopes only; no combustion or manufacturing approval")
        fig.tight_layout()
        fig.savefig(path, dpi=150)
    finally:
        plt.close(fig)


def export_shape_search(design, output_directory):
    report = run_shape_search(design)
    _json(report)  # Reject non-finite data before creating output.
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    try:
        (directory / "candidates.csv").write_bytes(_summary_csv(report))
        if report["schema_version"] == 2:
            (directory / "section_clearances.csv").write_bytes(_sections_csv(report))
        # One representative per family. All feasible coordinates remain in
        # search_report.json, including non-selected and target-shortfall cases.
        selected = {row["best_layout_id"] for row in report["comparison"] if row["best_layout_id"]}
        for candidate in report["candidates"]:
            if candidate["layout_id"] not in selected:
                continue
            target = directory / candidate["layout_id"]
            target.mkdir()
            export_coordinates(candidate["coordinates_mm"], target / "coordinates.csv")
            (target / "candidate.json").write_bytes(_json(candidate).encode("utf-8"))
            plot_candidate(candidate, target / "layout.png")
        report.update(provenance=collect_provenance(), status="completed",
                      files={p.relative_to(directory).as_posix(): _hash(p)
                             for p in sorted(directory.rglob("*")) if p.is_file()})
        (directory / "search_report.json").write_bytes(_json(report).encode("utf-8"))
    except Exception:
        # No completed report is published on a partial export. Keep evidence;
        # callers choose a new directory rather than silently resume/overwrite.
        raise
    return directory / "search_report.json"


def _summary_csv(report):
    columns = ["layout_id", "family", "N", "boundary_kind", "radius_mm", "area_mm2",
               "estimated_power_kW", "power_basis", "target_met_by_model", "selected",
               "uniformity_score", "minimum_wall_clearance_mm", "projection_occupancy_fraction"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for candidate in report["candidates"]:
        spec, metrics = candidate["specification"], candidate["metrics"]
        writer.writerow({"layout_id": candidate["layout_id"], "family": spec["layout"]["family"],
            "N": spec["N"], "boundary_kind": spec["boundary"]["kind"],
            "radius_mm": spec["boundary"]["radius_mm"], "area_mm2": metrics["boundary_area_mm2"],
            "estimated_power_kW": candidate["estimated_power_kW"],
            "power_basis": report["configuration"]["power_model"]["basis"] if report["configuration"]["power_model"] else None,
            "target_met_by_model": candidate["target_met_by_model"], "selected": candidate["selected"],
            **{key: metrics[key] for key in ("uniformity_score", "minimum_wall_clearance_mm", "projection_occupancy_fraction")}})
    return stream.getvalue().encode("utf-8-sig")


def _sections_csv(report):
    columns = ["layout_id", "family", "N", "section", "outer_diameter_mm",
               "minimum_edge_gap_mm", "required_edge_gap_mm", "minimum_spacing_margin_mm",
               "minimum_wall_clearance_mm", "required_wall_clearance_mm",
               "minimum_boundary_margin_mm", "feasible"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for candidate in report["candidates"]:
        spec = candidate["specification"]
        for row in candidate["validation"]["section_reports"]:
            writer.writerow({"layout_id": candidate["layout_id"], "family": spec["layout"]["family"],
                "N": spec["N"], "section": row["name"],
                **{k: row[k] for k in columns[4:]}})
    return stream.getvalue().encode("utf-8-sig")


def verify_shape_search(report_path):
    """Read-only regeneration of candidates and checks of all exported artifacts."""
    path = Path(report_path).resolve()
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    if saved.get("status") != "completed":
        raise ValueError("Expected a completed shape search report.")
    expected = run_shape_search(saved["configuration"])
    if set(saved) != set(expected) | {"provenance", "status", "files"}:
        raise ValueError("Unknown or incomplete report fields.")
    if canonical_json(expected) != canonical_json({k: saved[k] for k in expected}):
        raise ValueError("Saved search does not match replay from its configuration.")
    # Derive the expected inventory; a removed hash must not hide a missing file.
    names = {"candidates.csv"}
    if expected["schema_version"] == 2:
        names.add("section_clearances.csv")
    for row in expected["comparison"]:
        if row["best_layout_id"]:
            names.update(row["best_layout_id"] + "/" + name
                         for name in ("coordinates.csv", "candidate.json", "layout.png"))
    if not isinstance(saved["files"], dict) or set(saved["files"]) != names:
        raise ValueError("Artifact inventory mismatch.")
    for name, digest in saved["files"].items():
        artifact = path.parent / name
        if not artifact.resolve().is_relative_to(path.parent) or _hash(artifact) != digest:
            raise ValueError("Artifact escapes output or checksum mismatch: " + name)
    if (path.parent / "candidates.csv").read_bytes() != _summary_csv(expected):
        raise ValueError("Summary CSV does not match replay.")
    if expected["schema_version"] == 2 and (path.parent / "section_clearances.csv").read_bytes() != _sections_csv(expected):
        raise ValueError("Section clearance CSV does not match replay.")
    for candidate in expected["candidates"]:
        if candidate["layout_id"] not in {r["best_layout_id"] for r in expected["comparison"]}:
            continue
        directory = path.parent / candidate["layout_id"]
        if (directory / "candidate.json").read_bytes() != _json(candidate).encode("utf-8"):
            raise ValueError("Candidate JSON does not match replay.")
        with (directory / "coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != ["nozzle_id", "x_mm", "y_mm", "z_mm"]:
                raise ValueError("Coordinate CSV schema mismatch.")
            rows = list(reader)
        wanted = candidate["coordinates_mm"]
        if len(rows) != len(wanted) or any(
            int(row["nozzle_id"]) != i or float(row["z_mm"]) != 0 or
            [float(row["x_mm"]), float(row["y_mm"])] != point
            for i, (row, point) in enumerate(zip(rows, wanted), 1)):
            raise ValueError("Coordinate CSV does not match replay.")
    return {"static_valid": True, "scientific_eligible": False, "solver_started": False,
            "scope": "replayed geometry and file integrity; not authenticated power evidence"}
