"""Confirmed engineering inputs, translated through the existing M2/M6 APIs."""

import json
from pathlib import Path

from cfd import CFDSpec, export_cfd_package
from config import GeometryConfig
from experiments.archive import CaseRequest, archive_run
from experiments.spec import CaseSpec
from fluent import export_automation


def load_baseline():
    return json.loads(Path(__file__).with_name("single_nozzle.json").read_text(encoding="utf-8"))


def baseline_spec(case_id):
    """Only confirmed physical values enter M6. No research ranges or local data."""
    baseline = load_baseline()["confirmed_baseline"]
    return CFDSpec.create(case_id, baseline["operating_condition"], baseline["simulation_config"])


def prepare_example(output_root):
    """A 2D center reference, NOT an invented CFD domain; no attempts."""
    root = Path(output_root)
    geometry = CaseSpec.create("rectangular", 1, GeometryConfig(R=7, d=14, s_min=14),
                               {"rows": 1, "columns": 1, "spacing": 14})
    run = archive_run([CaseRequest(geometry)], name="M8B single nozzle engineering preparation",
                      description="R=D/2 is the nozzle footprint only, not a computational domain",
                      output_root=root / "runs")
    package = export_cfd_package(run, baseline_spec(geometry.case_id), output_root=root / "cfd",
                                 required_metrics=["pressure_loss"])
    automation = export_automation(package, root / "prepared" / package.parent.name,
        numerical_settings={"max_iterations": {"value": 500, "unit": "1"}})
    return package, automation
