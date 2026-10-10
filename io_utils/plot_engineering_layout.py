"""Headless plotting with actual installation and clearance boundaries."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from engineering_config import EngineeringGeometryConfig


def plot_engineering_layout(points, config: EngineeringGeometryConfig, title: str, path: Path) -> None:
    """Draw the physical installation boundary and both clearance limits."""
    config.require_complete()
    radius = config.installation_radius_mm
    edge_limit = radius - config.wall_clearance_mm
    centre_limit = edge_limit - config.nozzle_outer_diameter_mm / 2
    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    try:
        for limit, color, style, label in (
            (radius, "black", "-", "Installation boundary"),
            (edge_limit, "#2166ac", "--", "Nozzle edge limit (wall clearance)"),
            (centre_limit, "gray", ":", "Allowed centre boundary"),
        ):
            ax.add_patch(plt.Circle((0, 0), limit, fill=False, color=color,
                                    linestyle=style, label=label))
        for index, (x, y) in enumerate(points, start=1):
            ax.add_patch(plt.Circle((x, y), config.nozzle_outer_diameter_mm / 2,
                                    color="#d95f02", alpha=0.72))
            ax.text(x, y, str(index), fontsize=6, ha="center", va="center")
        margin = max(5.0, radius * 0.08)
        ax.set_xlim(-radius - margin, radius + margin)
        ax.set_ylim(-radius - margin, radius + margin)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("x / mm")
        ax.set_ylabel("y / mm")
        ax.set_title(f"{config.name}\n{title}: N={len(points)}, "
                     f"nozzle OD={config.nozzle_outer_diameter_mm:g} mm")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=7)
        fig.savefig(path, dpi=300, bbox_inches="tight")
    finally:
        plt.close(fig)
