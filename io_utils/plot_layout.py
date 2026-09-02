"""Headless PNG plotting for layouts and Pareto trade-offs."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from geometry.constraints import as_point_array


plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def plot_layout(
    points,
    *,
    R: float,
    d: float,
    title: str,
    png_path: str | Path,
    metrics: dict[str, object] | None = None,
    annotate: bool = True,
) -> Path:
    """Plot the burner boundary, allowable centre boundary, and nozzle discs."""

    array = as_point_array(points)
    path = Path(png_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    ax.add_patch(
        plt.Circle((0, 0), R, fill=False, color="black", linewidth=2, label="Burner boundary")
    )
    ax.add_patch(
        plt.Circle(
            (0, 0),
            R - d / 2.0,
            fill=False,
            linestyle="--",
            color="gray",
            linewidth=1,
            label="Allowed centre boundary",
        )
    )
    for index, (x, y) in enumerate(array, start=1):
        ax.add_patch(plt.Circle((x, y), d / 2.0, color="#d95f02", alpha=0.72))
        if annotate:
            ax.text(x, y, str(index), fontsize=5.5, ha="center", va="center")

    subtitle = ""
    if metrics:
        minimum = metrics.get("min_center_distance")
        uniformity = metrics.get("uniformity_score")
        if isinstance(minimum, (int, float)) and isinstance(uniformity, (int, float)):
            subtitle = f"\nN={len(array)}, min distance={minimum:.3f} mm, uniformity={uniformity:.4f}"
    ax.set_title(title + subtitle)
    margin = max(5.0, 0.08 * R)
    ax.set_xlim(-R - margin, R + margin)
    ax.set_ylim(-R - margin, R + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x / mm")
    ax.set_ylabel("y / mm")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_pareto_tradeoffs(rows, png_path: str | Path) -> Path:
    """Plot N against both geometric objectives and highlight Pareto rows."""

    frame = pd.DataFrame(list(rows))
    required = {"N", "uniformity_score", "min_center_distance", "pareto_candidate"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing Pareto plotting fields: {sorted(missing)}")

    path = Path(png_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pareto = frame[frame["pareto_candidate"].astype(bool)]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    comparisons = [
        ("uniformity_score", "Geometric uniformity score"),
        ("min_center_distance", "Minimum centre distance / mm"),
    ]
    for ax, (column, ylabel) in zip(axes, comparisons):
        ax.scatter(frame["N"], frame[column], s=18, alpha=0.35, label="Feasible")
        ax.scatter(
            pareto["N"],
            pareto[column],
            s=58,
            facecolors="none",
            edgecolors="#d62728",
            linewidths=1.4,
            label="Pareto candidate",
        )
        ax.set_xlabel("N")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend()
    fig.suptitle("Geometry-only trade-offs (no CFD objectives)")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return path
