"""Engineering geometry inputs, separate from legacy experiment defaults."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from validation import InputValidationError, require_finite


PENDING_FIELDS = (
    "installation_radius_mm",
    "nozzle_edge_gap_mm",
    "wall_clearance_mm",
)
CONFIG_FIELDS = ("name", "nozzle_outer_diameter_mm", *PENDING_FIELDS)


@dataclass(frozen=True)
class EngineeringGeometryConfig:
    """Circular mounting region and minimum edge clearances, all in mm.

    None means unspecified; zero is an explicitly supplied zero clearance.
    Complete inputs describe geometry only, not CFD or manufacturing readiness.
    """

    name: str
    nozzle_outer_diameter_mm: float
    installation_radius_mm: float | None = None
    nozzle_edge_gap_mm: float | None = None
    wall_clearance_mm: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InputValidationError("name must be nonempty text.")
        for field in CONFIG_FIELDS[1:]:
            value = getattr(self, field)
            if value is None and field in PENDING_FIELDS:
                continue
            value = require_finite(value, field)
            positive = field in {"nozzle_outer_diameter_mm", "installation_radius_mm"}
            if value < 0 or (positive and value == 0):
                qualifier = "positive" if positive else "non-negative"
                raise InputValidationError(f"{field} must be {qualifier}.")
            object.__setattr__(self, field, value)

        if self.nozzle_edge_gap_mm is not None:
            require_finite(
                self.nozzle_outer_diameter_mm + self.nozzle_edge_gap_mm,
                "minimum_center_distance_mm",
            )
        if self.installation_radius_mm is not None:
            available = self.installation_radius_mm - self.nozzle_outer_diameter_mm / 2
            if available < 0:
                raise InputValidationError("installation_radius_mm cannot fit one nozzle.")
            if self.wall_clearance_mm is not None and available < self.wall_clearance_mm:
                raise InputValidationError("wall_clearance_mm leaves no room for one nozzle.")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EngineeringGeometryConfig:
        required = {"schema_version", *CONFIG_FIELDS}
        if not isinstance(data, dict) or set(data) != required:
            raise InputValidationError(
                "Engineering configuration requires exactly: "
                + ", ".join(("schema_version", *CONFIG_FIELDS))
                + ". Use null for unspecified installation radius or clearances."
            )
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise InputValidationError("Unsupported engineering schema_version.")
        return cls(**{field: data[field] for field in CONFIG_FIELDS})

    @classmethod
    def load(cls, path: str | Path) -> EngineeringGeometryConfig:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, **asdict(self)}

    @property
    def missing_parameters(self) -> tuple[str, ...]:
        return tuple(field for field in PENDING_FIELDS if getattr(self, field) is None)

    def require_complete(self) -> None:
        """Reject unspecified inputs before any future engineering generation."""
        if self.missing_parameters:
            raise InputValidationError(
                "Unspecified engineering parameters: " + ", ".join(self.missing_parameters)
            )

    def inspect(self) -> dict[str, object]:
        """Report declared inputs and bounds without generating or saving layouts."""
        center_distance = None
        if self.nozzle_edge_gap_mm is not None:
            center_distance = self.nozzle_outer_diameter_mm + self.nozzle_edge_gap_mm
        center_radius = None
        if self.installation_radius_mm is not None and self.wall_clearance_mm is not None:
            center_radius = (
                self.installation_radius_mm
                - self.nozzle_outer_diameter_mm / 2
                - self.wall_clearance_mm
            )
        return {
            "configuration": self.to_dict(),
            "parameters_complete": not self.missing_parameters,
            "missing_parameters": list(self.missing_parameters),
            "minimum_center_distance_mm": center_distance,
            "maximum_center_radius_mm": center_radius,
        }
