"""Tabular result export."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _csv_safe(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def export_summary(rows, csv_path: str | Path) -> Path:
    """Write records to a UTF-8 CSV, preserving structured fields as JSON."""

    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_rows = [
        {key: _csv_safe(value) for key, value in dict(row).items()} for row in rows
    ]
    pd.DataFrame(safe_rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path
