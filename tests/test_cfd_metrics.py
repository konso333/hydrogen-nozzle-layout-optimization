from __future__ import annotations

from optimization.cfd_metrics import load_cfd_results


def test_cfd_loader_preserves_missing_values(tmp_path) -> None:
    csv_path = tmp_path / "fluent_results.csv"
    csv_path.write_text(
        "candidate_id,hydrogen_conversion,outlet_temperature_mean,"
        "outlet_temperature_std,pressure_loss,max_wall_heat_flux\n"
        "case_001,0.98,1200,35,,\n",
        encoding="utf-8",
    )
    result = load_cfd_results(csv_path)[0]
    assert result.candidate_id == "case_001"
    assert result.hydrogen_conversion == 0.98
    assert result.pressure_loss is None
    assert result.max_wall_heat_flux is None
