"""Project-wide geometry settings and output locations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "outputs"
FIGURES_DIR = OUTPUT_ROOT / "figures"
COORDINATES_DIR = OUTPUT_ROOT / "coordinates"
SUMMARIES_DIR = OUTPUT_ROOT / "summaries"


@dataclass(frozen=True)
class GeometryConfig:
    """Geometry definition for a circular burner face, in millimetres."""

    R: float = 55.0
    d: float = 4.0
    s_min: float = 8.0
    tolerance: float = 1e-9

    def __post_init__(self) -> None:
        if self.R <= 0:
            raise ValueError("R must be positive.")
        if self.d <= 0:
            raise ValueError("d must be positive.")
        if self.s_min < 0:
            raise ValueError("s_min cannot be negative.")
        if self.d > 2 * self.R:
            raise ValueError("The nozzle diameter cannot exceed the burner diameter.")
        if self.tolerance < 0:
            raise ValueError("tolerance cannot be negative.")

    @property
    def allowed_center_radius(self) -> float:
        return self.R - self.d / 2.0

    @property
    def required_center_distance(self) -> float:
        """Distance needed to satisfy both non-overlap and s_min."""

        return max(self.d, self.s_min)


DEFAULT_CONFIG = GeometryConfig()


def ensure_output_directories() -> None:
    """Create the output tree without writing results into the project root."""

    for directory in (FIGURES_DIR, COORDINATES_DIR, SUMMARIES_DIR):
        directory.mkdir(parents=True, exist_ok=True)
