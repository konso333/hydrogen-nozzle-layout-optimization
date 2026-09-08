"""M5 literature evidence is queryable and separate from geometry identity."""

from __future__ import annotations

import copy

import pytest

from config import GeometryConfig
from experiments.spec import CaseSpec
from literature import (
    EVIDENCE_LEVELS,
    PROVENANCE_STATUSES,
    get_layout_provenance,
    get_reference,
    layout_provenance,
    literature_references,
    load_literature_registry,
    validate_literature_registry,
)


EXPECTED_LAYOUTS = {
    "rectangular",
    "hexagonal",
    "ring",
    "staggered_ring",
    "nonuniform_ring",
    "sector",
    "radial_spoke",
    "deterministic_irregular",
    "cross_5",
}


def test_registry_has_unique_valid_references_and_complete_layout_mapping():
    data = load_literature_registry()
    references = data["references"]
    layouts = data["layouts"]
    reference_ids = [item["reference_id"] for item in references]
    layout_types = [item["layout_type"] for item in layouts]

    assert len(reference_ids) == len(set(reference_ids)) == 5
    assert set(reference_ids) == {"H1", "H2", "H3", "H4", "H5"}
    assert len(layout_types) == len(set(layout_types))
    assert set(layout_types) == EXPECTED_LAYOUTS
    for reference in references:
        assert reference["doi"].startswith("10.") and "/" in reference["doi"]
        assert reference["url"] == f"https://doi.org/{reference['doi']}"
    for item in layouts:
        assert item["status"] in PROVENANCE_STATUSES
        assert item["evidence_level"] in EVIDENCE_LEVELS
        assert set(item["reference_ids"]) <= set(reference_ids)
        if item["status"] == "literature_backed":
            assert item["reference_ids"]
        elif item["status"] == "literature_inspired":
            assert item["reference_ids"] and item["abstraction_notes"].strip()


def test_registry_queries_return_caller_owned_records():
    references = literature_references()
    layouts = layout_provenance()
    assert get_reference("H1")["doi"] == "10.1016/j.ast.2026.112865"
    assert get_layout_provenance("cross_5")["reference_ids"] == ["H2"]
    references["H1"]["title"] = "changed only in the caller"
    layouts["cross_5"]["reference_ids"].clear()
    assert get_reference("H1")["title"] != references["H1"]["title"]
    assert get_layout_provenance("cross_5")["reference_ids"] == ["H2"]
    with pytest.raises(KeyError, match="Unknown literature reference"):
        get_reference("missing")
    with pytest.raises(KeyError, match="No literature provenance"):
        get_layout_provenance("missing")


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda data: data["references"].append(copy.deepcopy(data["references"][0])),
         "Duplicate reference_id"),
        (lambda data: data["references"][0].update(doi="not-a-doi"), "not a DOI"),
        (lambda data: data["references"][0].update(url=""), "url must be nonempty"),
        (lambda data: data["layouts"][0].update(status="unsupported"), "status is invalid"),
        (lambda data: data["layouts"][0].update(reference_ids=["missing"]),
         "unknown reference"),
        (lambda data: data["layouts"][0].update(reference_ids=[]),
         "Literature-backed layout"),
        (lambda data: data["layouts"][6].update(abstraction_notes=""),
         "Literature-inspired layout"),
    ],
)
def test_registry_validation_rejects_broken_evidence_contracts(mutation, match):
    data = load_literature_registry()
    mutation(data)
    with pytest.raises(ValueError, match=match):
        validate_literature_registry(data)


def test_engineering_derived_layouts_may_have_no_reference():
    for layout_type in (
        "staggered_ring", "nonuniform_ring", "sector", "deterministic_irregular",
    ):
        item = get_layout_provenance(layout_type)
        assert item["status"] == "engineering_derived"
        assert item["reference_ids"] == []


def test_literature_metadata_is_not_part_of_case_identity():
    spec = CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10})
    original_id = spec.case_id
    local_registry = load_literature_registry()
    local_registry["references"][1]["title"] = "A different citation rendering"
    local_registry["layouts"][-1]["supported_claim"] = "Locally edited wording"

    assert spec.case_id == original_id
    assert CaseSpec.create("cross_5", 5, GeometryConfig(), {"pitch": 10}).case_id == original_id
    assert not ({"citation", "doi", "reference_id", "reference_ids"}
                & set(spec.normalized_spec))
    assert not ({"citation", "doi", "reference_id", "reference_ids"}
                & set(spec.normalized_spec["layout_parameters"]))
