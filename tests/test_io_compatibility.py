from __future__ import annotations

from io_utils.export_csv import export_coordinates


def test_legacy_coordinate_export_keeps_original_header(tmp_path) -> None:
    path = export_coordinates(
        [(1.0, 2.0), (3.0, 4.0)],
        tmp_path / "coordinates.csv",
        legacy_xy_only=True,
    )
    assert path.read_text(encoding="utf-8-sig").splitlines()[0] == "x_mm,y_mm"
