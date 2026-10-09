"""Bounded laminar warm start, extracted from the locally verified cold-flow trial.

Imports of PyFluent, h5py and psutil occur only during explicit execution.
"""

import copy
import csv
import hashlib
import json
import math
import os
from pathlib import Path

from coldflow_trial.config import EQUATIONS, sha256, write_json
from coldflow_trial.processes import OwnedProcesses, cleanup_records


def require(condition, message):
    if not condition:
        raise ValueError(message)


def mesh_fingerprint(path):
    import h5py
    digest = hashlib.sha256()
    with h5py.File(path, "r") as handle:
        mesh = handle["meshes/1"]
        require(int(mesh.attrs["dimension"][0]) == 3, "A 3D mesh is required")
        for name in ("nodes/coords/1", "faces/nodes/1/nnodes", "faces/nodes/1/nodes", "faces/c0/1",
                     "faces/c1/1", "faces/zoneTopology/id", "faces/zoneTopology/minId", "faces/zoneTopology/maxId"):
            array = mesh[name][()]
            digest.update(name.encode())
            digest.update(str(array.shape).encode())
            digest.update(array.tobytes())
    return digest.hexdigest()


def saved_iteration(path):
    import h5py
    with h5py.File(path, "r") as handle:
        iterations = handle["results/residuals/phase-1/h2/iterations"]
        return int(iterations[-1]) if len(iterations) else 0


def snapshot(session):
    setup = session.settings.setup
    return {"general": setup.general.get_state(), "models": setup.models.get_state(),
            "materials": setup.materials.get_state(), "cell_zones": setup.cell_zone_conditions.get_state(),
            "boundary_conditions": setup.boundary_conditions.get_state(),
            "methods": session.settings.solution.methods.get_state(),
            "controls": session.settings.solution.controls.get_state()}


def _constant(value, expected, label):
    require(value.get("option") == "value", f"{label}: uniform constant required")
    actual = value.get("value")
    require(isinstance(actual, (int, float)) and not isinstance(actual, bool)
            and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12), f"{label}: expected {expected}")


def _composition(zone, field, oxygen, fuel=False):
    species = zone["species"]
    require(species["specify_species_in_mole_fractions"] is False, "Mass fractions required")
    fractions = species[field]
    # Three explicitly transported species; N2 is the implicit final species.
    require(set(fractions) == {"h2", "o2", "h2o"}, "Expected H2/O2/H2O with implicit N2")
    for name, value in {"h2": 1 if fuel else 0, "o2": 0 if fuel else oxygen, "h2o": 0}.items():
        _constant(fractions[name], value, f"{field}.{name}")


def verify_physics(state, config, *, laminar):
    models = state["models"]
    require(state["general"]["solver"]["type"] == "pressure-based"
            and state["general"]["solver"]["time"] == "steady", "Steady pressure-based source required")
    require(models["energy"]["enabled"] is True, "Energy equation must be enabled")
    require(models["species"]["model"]["option"] == "species-transport", "Species Transport required")
    require(models["species"]["reactions"]["enable_volumetric_reactions"] is False,
            "Source must already have chemical reactions OFF; reacting restarts are unsupported")
    require(models["multiphase"]["model"] == "none" and models["radiation"]["model"] == "none",
            "Single-phase, radiation-OFF cold flow required")
    if laminar:
        require(models["viscous"]["model"] == "laminar", "Laminar model not applied")
    general = state["general"]["operating_conditions"]
    expected = config["expected_conditions"]
    require(general["gravity"]["enable"] is False, "Gravity must remain OFF")
    require(general["operating_pressure"] == expected["operating_pressure_Pa"], "Operating pressure mismatch")
    zones = state["boundary_conditions"]
    names = config["boundaries"]
    for kind, roles in {"velocity_inlet": ["fuel_inlet", "oxidizer_inlet"],
                        "pressure_outlet": ["outlet"], "wall": ["wall"]}.items():
        require(set(zones[kind]) == {names[role] for role in roles}, f"Unexpected {kind} zones")
    for kind, group in zones.items():
        if kind not in {"velocity_inlet", "pressure_outlet", "wall", "interior"} and isinstance(group, dict):
            require(not any(isinstance(zone, dict) and "name" in zone for zone in group.values()),
                    f"Unsupported extra boundary type: {kind}")
    for role, prefix in (("fuel_inlet", "fuel"), ("oxidizer_inlet", "oxidizer")):
        zone = zones["velocity_inlet"][names[role]]
        require(zone["momentum"]["velocity_specification_method"] == "Magnitude, Normal to Boundary",
                "Normal-to-boundary inlet velocity required")
        _constant(zone["momentum"]["velocity_magnitude"], expected[f"{prefix}_velocity_m_s"], role)
        _constant(zone["thermal"]["temperature"], expected[f"{prefix}_temperature_K"], role)
        _composition(zone, "species_mass_fraction", expected["oxidizer_o2_mass_fraction"], prefix == "fuel")
    outlet = zones["pressure_outlet"][names["outlet"]]
    _constant(outlet["momentum"]["gauge_pressure"], expected["outlet_gauge_pressure_Pa"], "outlet pressure")
    _constant(outlet["thermal"]["backflow_total_temperature"], expected["backflow_temperature_K"], "backflow T")
    _composition(outlet, "backflow_species_mass_fraction", expected["oxidizer_o2_mass_fraction"])
    wall = zones["wall"][names["wall"]]
    require(wall["momentum"]["wall_motion"] == "Stationary Wall"
            and wall["momentum"]["shear_condition"] == "No Slip", "Stationary no-slip wall required")
    require(wall["thermal"]["thermal_condition"] == "Heat Flux", "Heat Flux wall condition required")
    _constant(wall["thermal"]["heat_flux"], 0, "Adiabatic wall")
    for zone in state["cell_zones"]["fluid"].values():
        material = state["materials"]["mixture"][zone["general"]["material"]]
        require(set(material["species"]["volumetric_species"]) == {"h2", "h2o", "o2", "n2"},
                "Exactly H2/O2/H2O/N2 mixture required")


def verify_preserved(before, after, config):
    for key in ("general", "materials"):
        require(before[key] == after[key], f"Unexpected change to {key}")
    controls = [copy.deepcopy(state["controls"]) for state in (before, after)]
    # 25.2.0 hides exactly these inactive k-epsilon controls in laminar mode.
    # All controls for equations that remain active must still match.
    inactive = {("under_relaxation",): ("k", "epsilon", "turb-viscosity"),
                ("equations",): ("ke",),
                ("limits",): ("max_turb_visc_ratio", "min_epsilon", "min_tke"),
                ("advanced", "multi_grid", "mg_controls"): ("k", "epsilon")}
    for collection in controls:
        for path, keys in inactive.items():
            group = collection
            for component in path:
                group = group.get(component, {})
            for key in keys:
                group.pop(key, None)
    require(controls[0] == controls[1], "Unexpected change to active controls")
    require({k: v for k, v in before["models"].items() if k != "viscous"}
            == {k: v for k, v in after["models"].items() if k != "viscous"}, "Other physical models changed")
    cells = [copy.deepcopy(state["cell_zones"]) for state in (before, after)]
    for collection in cells:
        for zone in collection.get("fluid", {}).values():
            zone.get("general", {}).pop("laminar", None)
    require(cells[0] == cells[1], "Cell zone settings changed")
    for kind, roles in {"velocity_inlet": ["fuel_inlet", "oxidizer_inlet"],
                        "pressure_outlet": ["outlet"], "wall": ["wall"]}.items():
        for role in roles:
            name = config["boundaries"][role]
            for field in ("momentum", "thermal", "species"):
                require(before["boundary_conditions"][kind][name].get(field)
                        == after["boundary_conditions"][kind][name].get(field), f"Changed {role}.{field}")
    for field in ("mom", "temperature", "species-0", "species-1", "species-2", "pressure"):
        require(before["methods"]["spatial_discretization"]["discretization_scheme"][field]
                == after["methods"]["spatial_discretization"]["discretization_scheme"][field],
                f"Changed numerical scheme: {field}")


def parse_residuals(text, start, end):
    columns = None
    rows = {}
    for line in text.splitlines():
        parts = line.split()
        if parts[:2] == ["iter", "continuity"]:
            columns = parts[1:parts.index("time/iter")] if "time/iter" in parts else None
        elif columns and len(parts) >= len(columns) + 2 and parts[0].isdigit() and ":" in parts[len(columns) + 1]:
            index = int(parts[0])
            if not start < index <= end:
                continue
            try:
                values = dict(zip(columns, map(float, parts[1:len(columns) + 1])))
            except ValueError:
                continue
            require(set(values) == set(EQUATIONS), "Unexpected residual equations")
            require(all(math.isfinite(value) and value >= 0 for value in values.values()), "Invalid residual")
            rows[index] = values
    require(set(rows) == set(range(start + 1, end + 1)), "Incomplete transcript iteration history")
    return rows[end]


def block_reports(session, config, previous):
    integrals = session.settings.results.report.surface_integrals
    names = config["boundaries"]
    flows = {role: float(integrals.get_mass_flow_rate(surface_names=[zone])[zone])
             for role, zone in names.items()}
    incoming = flows["fuel_inlet"] + flows["oxidizer_inlet"]
    require(all(math.isfinite(value) for value in flows.values()) and incoming > 0
            and flows["fuel_inlet"] > 0 and flows["oxidizer_inlet"] > 0
            and flows["outlet"] < 0 and abs(flows["wall"]) < 1e-12, "Invalid boundary mass flow")
    pressure = {role: float(integrals.get_area_weighted_avg(report_of="total-pressure", surface_names=[names[role]])[names[role]])
                for role in ("fuel_inlet", "oxidizer_inlet", "outlet")}
    species = {field: {role: float(integrals.get_flow_rate(report_of=field, surface_names=[names[role]])[names[role]])
                       for role in ("fuel_inlet", "oxidizer_inlet", "outlet")}
               for field in ("h2", "o2", "h2o", "n2")}
    require(all(math.isfinite(value) for value in pressure.values())
            and all(math.isfinite(value) for group in species.values() for value in group.values()), "Invalid report value")
    drops = {role: pressure[role] - pressure["outlet"] for role in ("fuel_inlet", "oxidizer_inlet")}
    change = max(abs(value - previous["area_weighted_total_pressure_drop_Pa"][role]) / max(abs(value), 1.)
                 for role, value in drops.items()) if previous else None
    return {"mass_flow_kg_s": flows, "relative_mass_imbalance": abs(sum(flows.values())) / incoming,
            "species_convective_flow_kg_s": species,
            "h2_relative_convective_imbalance": abs(sum(species["h2"].values())) / flows["fuel_inlet"],
            "area_weighted_total_pressure_drop_Pa": drops, "relative_pressure_drop_change": change}


def passes(point, criteria):
    return (all(point["residuals"][name] <= limit for name, limit in criteria["scaled_residuals"].items())
            and point["relative_mass_imbalance"] < criteria["relative_mass_imbalance"]
            and point["h2_relative_convective_imbalance"] < criteria["h2_relative_convective_imbalance"]
            and point["relative_pressure_drop_change"] is not None
            and point["relative_pressure_drop_change"] < criteria["relative_pressure_drop_change"])


def launch_session(config, directory):
    from importlib import metadata
    require(metadata.version("ansys-fluent-core") == "0.42.1", "Use requirements-fluent-coldflow.txt (PyFluent 0.42.1)")
    import ansys.fluent.core as pyfluent
    fluent = config["fluent"]
    appdata = os.environ.get("APPDATA")
    try:
        os.environ["APPDATA"] = str(directory / "controller-cache")
        pyfluent.config.fluent_server_info_dir = str(directory)
        pyfluent.config.launch_fluent_ip = "127.0.0.1"
        env = {"APPDATA": appdata} if appdata else {}
        if fluent["license_server"] is not None:
            env["ANSYSLMD_LICENSE_FILE"] = fluent["license_server"]
        return pyfluent.launch_fluent(fluent_path=fluent["executable"], product_version=fluent["product_version"],
            dimension=3, precision="double", processor_count=fluent["processes"], mode="solver", ui_mode="no_gui",
            cwd=str(directory), start_timeout=fluent["start_timeout_seconds"], cleanup_on_exit=True,
            start_watchdog=False, start_transcript=False, py=False, additional_arguments="-license=premium", env=env)
    finally:
        if appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = appdata


def run_session(config, directory, *, session_factory=launch_session, tracker_factory=OwnedProcesses):
    directory = Path(directory)
    state = {"status": "preparing", "purpose": "engineering_coldflow", "formal_m8b_ready": False,
             "scientific_eligible": False, "chemical_reactions_enabled": None,
             "initialization_performed": False, "additional_iterations": 0, "history": []}
    tracker = tracker_factory(config["fluent"]["executable"], directory)
    session = None
    cleanup_notes = []
    try:
        for kind, suffix in (("case", "cas.h5"), ("data", "dat.h5")):
            require(sha256(directory / f"input.{suffix}") == config["input"][f"{kind}_sha256"], "Staged input hash mismatch")
        source_mesh = mesh_fingerprint(directory / "input.cas.h5")
        start = saved_iteration(directory / "input.dat.h5")
        state.update(source_solver_iteration=start, mesh_fingerprint=source_mesh)
        write_json(directory / "solver_status.json", state)
        session = session_factory(config, directory)
        tracker.capture(session)
        session.settings.file.start_transcript(file_name=str(directory / "fluent.trn"))
        session.settings.file.read_case_data(file_name=str(directory / "input.cas.h5"))
        before = snapshot(session)
        write_json(directory / "source_state.json", before)
        verify_physics(before, config, laminar=False)
        state["chemical_reactions_enabled"] = False
        session.settings.setup.models.viscous.model = "laminar"
        after = snapshot(session)
        verify_physics(after, config, laminar=True)
        verify_preserved(before, after, config)
        write_json(directory / "configured_state.json", after)
        monitor = session.settings.solution.monitor.residual
        require(set(monitor.equations.get_object_names()) == set(EQUATIONS), "Unexpected active residual equations")
        monitor.options.criterion_type = "absolute"
        monitor.options.normalize = False
        monitor.options.residual_values.scale_residuals = True
        monitor.options.residual_values.compute_local_scale = False
        for name, limit in config["convergence"]["scaled_residuals"].items():
            # Fluent hides absolute_criteria until convergence checking is enabled.
            monitor.equations[name].check_convergence = True
            monitor.equations[name].monitor = True
            monitor.equations[name].absolute_criteria = limit
        monitor.options.print = True
        monitor.options.plot = False
        for name in EQUATIONS:
            monitor.equations[name].check_convergence = False
        session.settings.mesh.check()
        session.settings.mesh.quality()
        session.settings.file.write_case_data(file_name=str(directory / "configured.cas.h5"))
        require(mesh_fingerprint(directory / "configured.cas.h5") == source_mesh, "Configured mesh changed")
        require(saved_iteration(directory / "configured.dat.h5") == start, "Warm start iteration changed")
        state["status"] = "running"
        write_json(directory / "solver_status.json", state)
        iterations = config["iterations"]
        consecutive = 0
        while state["additional_iterations"] < iterations["maximum"]:
            old = state["additional_iterations"]
            requested = min(iterations["block_size"], iterations["maximum"] - old)
            state["pending_requested_iterations"] = requested
            write_json(directory / "solver_status.json", state)
            session.settings.solution.run_calculation.iterate(iter_count=requested)
            checkpoint = directory / f"checkpoint_{old + requested:06d}.cas.h5"
            # Always save the actual flow BEFORE reading/validating diagnostics.
            session.settings.file.write_case_data(file_name=str(checkpoint))
            end = saved_iteration(checkpoint.with_name(checkpoint.name.replace(".cas.h5", ".dat.h5")))
            state.update(additional_iterations=end - start, solver_iteration=end, latest_checkpoint=str(checkpoint),
                         pending_requested_iterations=0)
            write_json(directory / "solver_status.json", state)
            require(end - start - old == requested, "Unexpected iteration count; stopping to protect budget")
            residuals = parse_residuals((directory / "fluent.trn").read_text(encoding="utf-8", errors="replace"), start + old, end)
            point = block_reports(session, config, state["history"][-1] if state["history"] else None)
            point.update(additional_iterations=end - start, solver_iteration=end, residuals=residuals)
            point["criteria_met"] = passes(point, config["convergence"])
            state["history"].append(point)
            consecutive = consecutive + 1 if point["criteria_met"] else 0
            tracker.capture(session)
            write_json(directory / "solver_status.json", state)
            print(json.dumps({"additional_iterations": end - start, "h2_residual": residuals["h2"],
                              "relative_mass_imbalance": point["relative_mass_imbalance"]}), flush=True)
            if end - start >= iterations["minimum"] and consecutive >= iterations["consecutive_passing_blocks"]:
                state["status"] = "numerical_criteria_met"
                break
        else:
            state["status"] = "iteration_budget_reached"
        session.settings.file.write_case_data(file_name=str(directory / "final.cas.h5"))
        require(mesh_fingerprint(directory / "final.cas.h5") == source_mesh, "Final mesh changed")
        verify_physics(snapshot(session), config, laminar=True)
        state["final_files"] = [{"file": name, "sha256": sha256(directory / name),
                                  "size_bytes": (directory / name).stat().st_size}
                                 for name in ("final.cas.h5", "final.dat.h5")]
        state["numerical_criteria_met"] = state["status"] == "numerical_criteria_met"
        with (directory / "history.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["additional_iterations", "solver_iteration", *EQUATIONS,
                "relative_mass_imbalance", "h2_relative_convective_imbalance", "relative_pressure_drop_change"])
            writer.writeheader()
            for point in state["history"]:
                writer.writerow({key: point[key] for key in writer.fieldnames if key not in EQUATIONS}
                                | point["residuals"])
    except BaseException as error:
        state.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                     error=f"{type(error).__name__}: {error}", numerical_criteria_met=False)
    finally:
        try:
            tracker.capture(session)
        except Exception as error:
            cleanup_notes.append(f"Capture: {error}")
        if session is not None:
            try:
                session.settings.file.stop_transcript()
            except Exception as error:
                cleanup_notes.append(f"Transcript close: {error}")
            try:
                session.exit(timeout=10, wait=10)
            except Exception as error:
                cleanup_notes.append(f"Session close: {error}")
        cleanup = cleanup_records(list(tracker.owned.values()))
        cleanup["cleanup_messages"].extend(cleanup_notes)
        state["cleanup"] = cleanup
        if not cleanup["all_tracked_processes_exited"]:
            state.update(status="failed", numerical_criteria_met=False,
                         cleanup_error="Some tracked Fluent processes could not be verified exited")
        write_json(directory / "solver_status.json", state)
    return state
