"""Normalize persisted campaign metadata to match the final operator GUI."""

from __future__ import annotations

import csv
from pathlib import Path
from types import MethodType
from typing import Any


_OBSOLETE_FIELDS = {
    "beam_on_seconds": "active_test_runtime_seconds",
    "calculated_fluence_p_cm2": "estimated_fluence_p_cm2",
}


def apply_campaign_storage_cleanup(app: Any) -> None:
    """Remove obsolete beam-control names from JSONL and CSV outputs.

    Run Notes are persisted under the stable field name ``operator_comments``.
    """

    original_record_event = app._record_event
    original_save_result_csv = app._save_result_csv

    def normalized_record_event(self: Any, event: str, **fields: Any) -> Any:
        normalized = dict(fields)
        for old_name, new_name in _OBSOLETE_FIELDS.items():
            if old_name in normalized:
                normalized[new_name] = normalized.pop(old_name)
        normalized.pop("facility_reported_fluence_p_cm2", None)
        return original_record_event(event, **normalized)

    def normalized_save_result_csv(self: Any, summary: dict[str, Any]) -> Any:
        path = original_save_result_csv(summary)
        if path is None:
            return None

        csv_path = Path(path)
        try:
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))

            cleaned_rows: list[list[str]] = []
            for row in rows:
                if not row:
                    cleaned_rows.append(row)
                    continue
                field_name = row[0]
                if field_name == "facility_reported_fluence_p_cm2":
                    continue
                if field_name in _OBSOLETE_FIELDS:
                    row = [_OBSOLETE_FIELDS[field_name], *row[1:]]
                cleaned_rows.append(row)

            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(cleaned_rows)
        except OSError as error:
            self._append_log(f"Could not normalize campaign CSV fields: {error}")

        return path

    app._record_event = MethodType(normalized_record_event, app)
    app._save_result_csv = MethodType(normalized_save_result_csv, app)
    app._append_log(
        "Campaign storage schema normalized: active test runtime, estimated fluence, "
        "and operator comments."
    )
