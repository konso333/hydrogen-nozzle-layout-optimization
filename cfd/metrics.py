"""Project-defined M6 extraction contract; separate from M4 and legacy CSV."""

from optimization.cfd_metrics import CFD_FIELDS
from cfd.spec import number, text_value


_DEFINITIONS = {
    "hydrogen_conversion": ("1", "1 - outlet H2 mass flow / inlet H2 mass flow; positive inlet H2 flow required", "Sum outward H2 mass flow at outlets and inward H2 mass flow at inlets; no reverse flow"),
    "outlet_temperature_mean": ("K", "Outlet area-weighted static temperature: sum(A_i*T_i)/sum(A_i)", "All declared outlet faces; positive total area"),
    "outlet_temperature_std": ("K", "sqrt(sum(A_i*(T_i-T_area_mean)^2)/sum(A_i)); population standard deviation", "Same outlet faces and sample as outlet_temperature_mean"),
    "pressure_loss": ("Pa", "Inlet area-weighted total pressure minus outlet area-weighted total pressure; signed", "Declared inlet and outlet faces; each with positive total area; common pressure reference"),
    "max_wall_heat_flux": ("W/m^2", "max(abs(q_wall)); magnitude of local wall-normal total heat flux", "All declared combustion-chamber wall faces"),
}


def metric_definitions(required_metrics):
    """The caller explicitly selects required fields for this handoff."""
    required = list(required_metrics)
    if not required or len(set(required)) != len(required) or set(required) - set(CFD_FIELDS):
        raise ValueError("Select a nonempty, unique subset of the five CFD fields.")
    return {key: {"key": key, "description": _DEFINITIONS[key][1],
                  "unit": _DEFINITIONS[key][0], "direction": "descriptive",
                  "definedness": _DEFINITIONS[key][2], "source": _DEFINITIONS[key][2],
                  "required": key in required, "null_policy": "null means not extracted or undefined"}
            for key in CFD_FIELDS}


def validate_metrics(values, contract, status):
    if not isinstance(values, dict) or set(values) - set(CFD_FIELDS):
        raise ValueError("Unknown CFD metric or invalid metrics object.")
    result = dict.fromkeys(CFD_FIELDS)
    for key, entry in values.items():
        if entry is None:
            continue
        if status in {"not_started", "prepared", "exported"}:
            raise ValueError("Unstarted executions cannot have numerical results.")
        if not isinstance(entry, dict) or set(entry) != {"value", "unit", "source"}:
            raise ValueError(f"{key} requires value, unit and source.")
        value = number(entry["value"], key)
        if entry["unit"] != contract[key]["unit"]:
            raise ValueError(f"Wrong unit for {key}.")
        if key == "hydrogen_conversion" and not 0 <= value <= 1:
            raise ValueError("hydrogen_conversion must be in [0, 1].")
        if key in {"outlet_temperature_mean", "outlet_temperature_std", "max_wall_heat_flux"} and value < 0:
            raise ValueError(f"{key} must be nonnegative.")
        result[key] = {"value": value, "unit": entry["unit"],
                       "source": text_value(entry["source"], "metric source")}
    if status == "completed" and any(meta["required"] and result[key] is None for key, meta in contract.items()):
        raise ValueError("completed requires every selected required metric.")
    return result
