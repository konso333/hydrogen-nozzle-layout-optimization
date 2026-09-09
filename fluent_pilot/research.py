"""Readiness attestations and missing-input ownership, not physical values.

Evidence references are researcher declarations, not independently authenticated
approvals. Physical values remain in M6; numerical preparation remains in M7.
"""

from dataclasses import asdict, dataclass, field, replace
from pathlib import PurePosixPath, PureWindowsPath
import re


SOURCES = frozenset({"project_existing", "group_1_single_nozzle", "group_3_experiment",
                     "researcher_decision", "literature", "pending"})
STATUSES = frozenset({"confirmed", "unresolved", "not_required", "pending_contract"})
# category, blocking stage, expected provider, required keys
_GROUPS = (
    ("geometry", "geometry", "researcher_decision",
     "combustor_axial_length real_3d_chamber_construction actual_boundary_face_mapping array_to_chamber_integration"),
    ("geometry", "geometry", "group_1_single_nozzle",
     "nozzle_physical_length nozzle_internal_geometry_or_equivalent_inlet"),
    ("boundary_conditions", "physics", "researcher_decision",
     "h2_inlet_definition oxidizer_inlet_definition individual_stream_mass_flow inlet_temperature inlet_pressure outlet_definition wall_thermal_condition pressure_reference"),
    ("physics", "physics", "group_1_single_nozzle",
     "turbulence_model combustion_model hydrogen_chemical_mechanism species_material_database stable_operating_envelope"),
    ("physics", "physics", "researcher_decision", "radiation_model_decision pilot_baseline_scope"),
    ("boundary_conditions", "physics", "group_3_experiment",
     "stage_definition pilot_main_stage_assignment stage_flow_split stage_equivalence_ratio stage_activation_logic"),
    ("mesh", "mesh", "researcher_decision",
     "global_mesh_strategy nozzle_refinement boundary_layer_strategy grid_independence_plan"),
    ("mesh", "mesh", "group_1_single_nozzle", "mesh_reference"),
    ("numerical_solver", "solver", "researcher_decision",
     "pressure_velocity_coupling discretization equation_specific_residual_criteria initialization iteration_or_time_step_strategy convergence_assessment"),
    ("numerical_solver", "solver", "group_1_single_nozzle", "convergence_reference"),
    ("post_processing", "postprocessing", "researcher_decision",
     "inlet_outlet_surfaces wall_surfaces steady_sample_definition transient_time_window"),
    ("experiment_alignment", "pilot", "group_3_experiment",
     "benchmark_operating_condition h2_flow air_flow equivalence_ratio thermal_power_or_load staging_ratio flame_measurement_locations nox_measurement_definition nox_unit dry_wet_basis oxygen_correction sampling_location instrument_information"),
    ("physics", "physics", "researcher_decision", "physical_model_configuration operating_mode"),
    ("boundary_conditions", "physics", "group_1_single_nozzle", "inlet_turbulence_specification"),
    ("mesh", "mesh", "researcher_decision", "mesh_quality_acceptance_criteria"),
    ("post_processing", "postprocessing", "researcher_decision", "required_result_subset"),
)
CATALOG = {key: (category, stage, source)
           for category, stage, source, keys in _GROUPS for key in keys.split()}
STAGING = frozenset("stage_definition pilot_main_stage_assignment stage_flow_split stage_equivalence_ratio stage_activation_logic staging_ratio".split())
NOX = frozenset("nox_measurement_definition nox_unit dry_wet_basis oxygen_correction sampling_location instrument_information".split())
STAGES = ("geometry", "mesh", "physics", "solver", "postprocessing", "pilot")
EXEMPTIONS = {
    "nonstaged_baseline": STAGING,
    "steady_sampling": frozenset({"transient_time_window"}),
    "transient_sampling": frozenset({"steady_sample_definition"}),
    "laminar_inlet": frozenset({"inlet_turbulence_specification"}),
}
CONDITIONAL = frozenset().union(*EXEMPTIONS.values())
ACCEPTANCE = {
    "physical_model_configuration": "Explicit energy equation, species/reaction enablement, density/compressibility treatment; gravity decision",
    "operating_mode": "Physical dimensionality and steady/transient choice in M6",
    "inlet_turbulence_specification": "Model-dependent inlet turbulence quantities or documented laminar applicability",
    "outlet_definition": "Actual boundary type and applicable backflow temperature, composition and turbulence quantities",
    "mesh_quality_acceptance_criteria": "Mesh quality acceptance distinct from grid-independence study",
    "required_result_subset": "Research approval of the exact required M6 metric subset",
    "pressure_reference": "Operating pressure and absolute/gauge handling",
    "wall_thermal_condition": "Thermal boundary and wall material when solid heat transfer applies",
    "initialization": "Initialization and ignition approach",
    "convergence_assessment": "Conservation/physical monitors and convergence assessment",
}


def portable_reference(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Evidence reference must be nonempty text")
    clean = value.strip()
    if (PureWindowsPath(clean).drive or PureWindowsPath(clean).root
            or PurePosixPath(clean).is_absolute()
            or re.search(r"[A-Za-z]:[\\/]|\\\\|(?<![\w:])/(?!/)", clean)
            or any(ord(c) < 32 for c in clean)):
        raise ValueError("Evidence reference must be portable, not an absolute local path")
    return value


@dataclass(frozen=True)
class ApplicabilityDecision:
    """Scope provenance only; physical values still come exclusively from M6."""
    kind: str
    reason: str
    evidence_reference: str

    def __post_init__(self):
        if self.kind not in EXEMPTIONS:
            raise ValueError("Unknown applicability decision")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Explicit applicability reason required")
        portable_reference(self.evidence_reference)


@dataclass(frozen=True)
class GateItem:
    key: str
    status: str
    source: str
    reason: str
    blocking_stage: str
    category: str
    evidence_reference: str | None = None
    required_for_pilot: bool = field(init=False)
    applicability_policy: str = field(init=False)

    def __post_init__(self):
        if self.key not in CATALOG or self.status not in STATUSES or self.source not in SOURCES:
            raise ValueError("Unknown research key, status or source")
        # NOx is outside the five M6 metrics and is deferred contract work,
        # not a requirement for this first solve. It must remain visible.
        object.__setattr__(self, "required_for_pilot", self.key not in NOX)
        object.__setattr__(self, "applicability_policy", "pending_contract" if self.key in NOX else
                           "conditionally_not_required" if self.key in CONDITIONAL else "always_required")
        category, stage, _ = CATALOG[self.key]
        if (self.category, self.blocking_stage) != (category, stage):
            raise ValueError("Cannot change a required item's category or blocking stage")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("A reason is required")
        if self.evidence_reference is not None:
            portable_reference(self.evidence_reference)
        if self.status in {"confirmed", "not_required"} and (
                not self.evidence_reference or self.source == "pending"):
            raise ValueError("Readiness requires an explicit evidence reference and source")
        if self.key in NOX and self.status != "pending_contract":
            raise ValueError("M8A NOx remains pending_contract; contract work is required")
        if self.status == "not_required" and self.key not in CONDITIONAL:
            raise ValueError("Always-required research inputs cannot be waived")
        if self.key in STAGING and self.status == "not_required" and (
                self.source != "researcher_decision" or not self.reason.startswith("not_required_for_baseline:")):
            raise ValueError("Staging exemption requires explicit baseline scope and researcher provenance")


@dataclass(frozen=True)
class ResearchInputGate:
    items: tuple[GateItem, ...]
    decisions: tuple[ApplicabilityDecision, ...] = ()

    def __post_init__(self):
        items = tuple(self.items)
        if any(not isinstance(i, GateItem) for i in items):
            raise ValueError("Expected GateItem records")
        if len(items) != len(CATALOG) or {i.key for i in items} != set(CATALOG):
            raise ValueError("All required research keys must appear exactly once")
        object.__setattr__(self, "items", items)
        decisions = tuple(self.decisions)
        if (any(not isinstance(d, ApplicabilityDecision) for d in decisions)
                or len({d.kind for d in decisions}) != len(decisions)):
            raise ValueError("Expected unique applicability decisions")
        kinds = {d.kind for d in decisions}
        if {"steady_sampling", "transient_sampling"} <= kinds:
            raise ValueError("Conflicting sampling decisions")
        for decision in decisions:
            expected_reason = ("not_required_for_baseline: " + decision.reason
                               if decision.kind == "nonstaged_baseline" else decision.reason)
            for key in EXEMPTIONS[decision.kind]:
                item = next(i for i in items if i.key == key)
                if (item.status, item.source, item.reason, item.evidence_reference) != (
                        "not_required", "researcher_decision", expected_reason, decision.evidence_reference):
                    raise ValueError("Exemption must match its applicability decision")
            if decision.kind == "nonstaged_baseline":
                scope = next(i for i in items if i.key == "pilot_baseline_scope")
                if (scope.status, scope.source, scope.reason, scope.evidence_reference) != (
                        "confirmed", "researcher_decision", decision.reason, decision.evidence_reference):
                    raise ValueError("Non-staged baseline requires matching confirmed scope")
        if any(i.status == "not_required" and not any(i.key in EXEMPTIONS[d.kind] for d in decisions)
               for i in items):
            raise ValueError("Exemption requires a linked applicability decision")
        object.__setattr__(self, "decisions", decisions)

    @classmethod
    def initial(cls):
        return cls(tuple(GateItem(key, "pending_contract" if key in NOX else "unresolved",
                                 source, "Formal input or researcher decision not yet received",
                                 stage, category)
                         for key, (category, stage, source) in CATALOG.items()))

    def record(self, key, *, status, source, reason, evidence_reference):
        """Record a documented review; never infer values or silently omit inputs."""
        if key not in CATALOG:
            raise ValueError("Unknown research key")
        if status == "not_required":
            raise ValueError("Use an explicit applicability decision; record cannot waive inputs")
        return ResearchInputGate(tuple(
            replace(item, status=status, source=source, reason=reason,
                    evidence_reference=evidence_reference) if item.key == key else item
            for item in self.items), self.decisions)

    def nonstaged_baseline(self, *, reason, evidence_reference):
        return self.declare_applicability("nonstaged_baseline", reason=reason,
                                          evidence_reference=evidence_reference)

    def declare_applicability(self, kind, *, reason, evidence_reference):
        decision = ApplicabilityDecision(kind, reason, evidence_reference)
        items = []
        for item in self.items:
            if item.key in EXEMPTIONS[kind]:
                item = replace(item, status="not_required", source="researcher_decision",
                               reason="not_required_for_baseline: " + reason if kind == "nonstaged_baseline" else reason,
                               evidence_reference=evidence_reference)
            elif kind == "nonstaged_baseline" and item.key == "pilot_baseline_scope":
                item = replace(item, status="confirmed", source="researcher_decision",
                               reason=reason, evidence_reference=evidence_reference)
            items.append(item)
        return ResearchInputGate(tuple(items), tuple(d for d in self.decisions if d.kind != kind) + (decision,))

    @property
    def missing_inputs(self):
        return tuple(i for i in self.items if i.status not in {"confirmed", "not_required"})

    @property
    def blocking_inputs(self):
        return tuple(i for i in self.missing_inputs if i.required_for_pilot)

    def readiness(self):
        result = {stage + "_ready": not any(i.blocking_stage == stage for i in self.blocking_inputs)
                  for stage in STAGES}
        result["pilot_ready"] = not self.blocking_inputs
        return result

    def group_dependency_report(self):
        return {source: [asdict(i) for i in self.missing_inputs if i.source == source]
                for source in sorted(SOURCES)}

    def to_dict(self):
        return {"items": [{**asdict(i), "acceptance_requirement": ACCEPTANCE.get(i.key)} for i in self.items],
                "applicability_decisions": [asdict(d) for d in self.decisions], **self.readiness()}
