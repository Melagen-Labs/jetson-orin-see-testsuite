"""Persistent JSONL event logging."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class EventLogger:
    """Append structured events to a JSONL file."""

    def __init__(
        self,
        file_path: str | Path,
    ) -> None:
        path = Path(file_path)

        if path.exists() and path.is_dir():
            raise ValueError(
                "file_path must identify a file, not a directory"
            )

        self.file_path = path

    def append(
        self,
        event: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """Append one structured event and return the record."""

        if not isinstance(event, str):
            raise TypeError("event must be a string")

        normalized_event = event.strip()

        if not normalized_event:
            raise ValueError(
                "event must be a non-empty string"
            )

        record: dict[str, Any] = {
            "event": normalized_event,
            "recorded_at_utc": utc_timestamp(),
        }

        for key, value in fields.items():
            if key in record:
                raise ValueError(
                    f"Reserved log field cannot be replaced: {key}"
                )

            record[key] = value

        serialized_record = json.dumps(
            record,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        self.file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self.file_path.open(
            mode="a",
            encoding="utf-8",
            newline="\n",
        ) as log_file:
            log_file.write(serialized_record)
            log_file.write("\n")
            log_file.flush()
            os.fsync(log_file.fileno())

        return record