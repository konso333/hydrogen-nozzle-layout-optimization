"""Small JSON-backed registry linking papers, topology claims, and code layouts."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path


REGISTRY_PATH = Path(__file__).with_name("layout_references.json")
PROVENANCE_STATUSES = (
    "literature_backed",
    "literature_inspired",
    "engineering_derived",
)
EVIDENCE_LEVELS = (
    "direct_primary",
    "secondary_review",
    "engineering_abstraction",
    "project_defined",
)
SOURCE_TYPES = ("primary_research", "secondary_review")
EXPECTED_LAYOUT_TYPES = (
    "rectangular",
    "hexagonal",
    "ring",
    "staggered_ring",
    "nonuniform_ring",
    "sector",
    "radial_spoke",
    "deterministic_irregular",
    "cross_5",
)
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def _require_text(value, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")


def validate_literature_registry(data: dict) -> None:
    """Validate references and conservative layout-evidence relationships."""

    if not isinstance(data, dict) or set(data) != {"schema_version", "references", "layouts"}:
        raise ValueError("Literature registry has incomplete or unknown top-level fields.")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported literature registry schema_version.")
    references = data["references"]
    layouts = data["layouts"]
    if not isinstance(references, list) or not references:
        raise ValueError("Literature registry requires at least one reference.")
    if not isinstance(layouts, list) or not layouts:
        raise ValueError("Literature registry requires layout provenance entries.")

    reference_fields = {
        "reference_id", "title", "authors", "year", "venue", "doi", "url",
        "source_type", "notes",
    }
    reference_ids: set[str] = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict) or set(reference) != reference_fields:
            raise ValueError(f"references[{index}] has incomplete or unknown fields.")
        reference_id = reference["reference_id"]
        _require_text(reference_id, f"references[{index}].reference_id")
        if reference_id in reference_ids:
            raise ValueError(f"Duplicate reference_id: {reference_id}")
        reference_ids.add(reference_id)
        for key in ("title", "venue", "doi", "url", "source_type", "notes"):
            _require_text(reference[key], f"{reference_id}.{key}")
        if (not isinstance(reference["authors"], list)
                or any(not isinstance(author, str) or not author.strip()
                       for author in reference["authors"])):
            raise ValueError(f"{reference_id}.authors must be a list of nonempty names.")
        if type(reference["year"]) is not int or reference["year"] < 1900:
            raise ValueError(f"{reference_id}.year must be a plausible integer year.")
        if not _DOI_PATTERN.fullmatch(reference["doi"]):
            raise ValueError(f"{reference_id}.doi is not a DOI.")
        if reference["url"] != f"https://doi.org/{reference['doi']}":
            raise ValueError(f"{reference_id}.url must be its canonical DOI URL.")
        if reference["source_type"] not in SOURCE_TYPES:
            raise ValueError(f"{reference_id}.source_type is invalid.")

    layout_fields = {
        "layout_type", "status", "evidence_level", "reference_ids",
        "supported_claim", "abstraction_notes",
    }
    layout_types: set[str] = set()
    for index, provenance in enumerate(layouts):
        if not isinstance(provenance, dict) or set(provenance) != layout_fields:
            raise ValueError(f"layouts[{index}] has incomplete or unknown fields.")
        layout_type = provenance["layout_type"]
        _require_text(layout_type, f"layouts[{index}].layout_type")
        if layout_type in layout_types:
            raise ValueError(f"Duplicate layout_type provenance: {layout_type}")
        layout_types.add(layout_type)
        if provenance["status"] not in PROVENANCE_STATUSES:
            raise ValueError(f"{layout_type}.status is invalid.")
        if provenance["evidence_level"] not in EVIDENCE_LEVELS:
            raise ValueError(f"{layout_type}.evidence_level is invalid.")
        ids = provenance["reference_ids"]
        if not isinstance(ids, list) or any(item not in reference_ids for item in ids):
            raise ValueError(f"{layout_type}.reference_ids contains an unknown reference.")
        for key in ("supported_claim", "abstraction_notes"):
            if not isinstance(provenance[key], str):
                raise ValueError(f"{layout_type}.{key} must be text.")
        if provenance["status"] == "literature_backed" and not ids:
            raise ValueError(f"Literature-backed layout {layout_type} requires a reference.")
        if provenance["status"] == "literature_inspired" and (
            not ids or not provenance["abstraction_notes"].strip()
        ):
            raise ValueError(
                f"Literature-inspired layout {layout_type} requires a reference and abstraction note."
            )
    if layout_types != set(EXPECTED_LAYOUT_TYPES):
        raise ValueError("Literature registry does not cover the complete M5 layout inventory.")


def load_literature_registry(path: str | Path = REGISTRY_PATH) -> dict:
    """Load and validate a registry document, returning caller-owned data."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_literature_registry(data)
    return data


def literature_references() -> dict[str, dict]:
    """Return reference metadata keyed by stable reference ID."""

    return {
        item["reference_id"]: copy.deepcopy(item)
        for item in load_literature_registry()["references"]
    }


def layout_provenance() -> dict[str, dict]:
    """Return layout evidence metadata keyed by registered layout type."""

    return {
        item["layout_type"]: copy.deepcopy(item)
        for item in load_literature_registry()["layouts"]
    }


def get_reference(reference_id: str) -> dict:
    """Return one reference without exposing mutable registry state."""

    try:
        return literature_references()[reference_id]
    except KeyError as exc:
        raise KeyError(f"Unknown literature reference: {reference_id!r}") from exc


def get_layout_provenance(layout_type: str) -> dict:
    """Return conservative evidence and abstraction notes for one layout."""

    try:
        return layout_provenance()[layout_type]
    except KeyError as exc:
        raise KeyError(f"No literature provenance for layout: {layout_type!r}") from exc
