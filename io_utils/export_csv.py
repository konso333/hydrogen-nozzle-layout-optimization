"""Coordinate CSV export."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from geometry.constraints import as_point_array


def export_coordinates(
    points,
    csv_path: str | Path,
    *,
    legacy_xy_only: bool = False,
) -> Path:
    """Write coordinates, optionally using the original two-column schema."""

    array = as_point_array(points)
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = {"x_mm": array[:, 0], "y_mm": array[:, 1]}
    if legacy_xy_only:
        frame = pd.DataFrame(columns)
    else:
        frame = pd.DataFrame(
            {
                "nozzle_id": range(1, len(array) + 1),
                **columns,
                "z_mm": 0.0,
            }
        )
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path
