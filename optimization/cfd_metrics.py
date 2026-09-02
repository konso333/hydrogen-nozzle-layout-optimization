"""Read-only interface for future Fluent CFD results."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path


CFD_FIELDS = (
    "hydrogen_conversion",
    "outlet_temperature_mean",
    "outlet_temperature_std",
    "pressure_loss",
    "max_wall_heat_flux",
)


@dataclass(frozen=True)
class CFDResult:
    """One externally computed CFD result linked to a geometry candidate."""

    candidate_id: str
    hydrogen_conversion: float | None
    outlet_temperature_mean: float | None
    outlet_temperature_std: float | None
    pressure_loss: float | None
    max_wall_heat_flux: float | None


def _optional_float(value: str | None, field_name: str) -> float | None:
    if value is None or not value.strip():
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite when provided.")
    return result


def load_cfd_results(csv_path: str | Path) -> list[CFDResult]:
    """Load CFD values from a Fluent post-processing CSV.

    This function only reads supplied data.  It never synthesizes, estimates,
    fills, or randomizes missing CFD metrics.
    """

    path = Path(csv_path)
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"candidate_id", *CFD_FIELDS}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing CFD columns: {sorted(missing)}")
        results = []
        for row_number, row in enumerate(reader, start=2):
            candidate_id = (row.get("candidate_id") or "").strip()
            if not candidate_id:
                raise ValueError(f"Missing candidate_id on CSV row {row_number}.")
            results.append(
                CFDResult(
                    candidate_id=candidate_id,
                    **{
                        field: _optional_float(row.get(field), field)
                        for field in CFD_FIELDS
                    },
                )
            )
    return results
