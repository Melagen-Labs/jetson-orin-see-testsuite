"""Shared event-log helper for the Jetson SEE test suite (schema v1).

Reference emitter/validator for the frozen JSONL event schema defined in
docs/EVENT_SCHEMA.md and docs/event_schema.json. Standard library only, so it
runs on the DUT, the arbiter, and a dev laptop with no extra dependencies.

Every channel (compute, memory, heartbeat, boot, power) should build records
through `envelope()` + a channel payload and write them with `emit()`, so the
arbiter and dashboard can rely on one uniform format.

This helper is intentionally dependency-free: `validate()` hand-checks the
envelope rather than pulling in `jsonschema`. `docs/event_schema.json` is the
authoritative machine-readable schema for anyone who does want jsonschema.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

SCHEMA_VERSION = 1

CHANNELS = ("compute", "memory", "heartbeat", "boot", "power")
STATUSES = ("ok", "anomaly", "stall", "crash", "tripped", "info")

ENVELOPE_REQUIRED = (
    "schema_version", "ts", "run_id", "jetson_id", "channel", "event", "status",
)
META_FIELDS = ("beam_energy", "fluence_source", "shield_config")


def iso_now() -> str:
    """ISO-8601 UTC timestamp with millisecond precision, e.g. ...T18:22:04.531Z."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def envelope(run_id, jetson_id, channel, event, status="ok", ts=None, meta=None):
    """Build the common envelope. `meta` supplies the beam/run metadata fields."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {CHANNELS}")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    rec = {
        "schema_version": SCHEMA_VERSION,
        "ts": ts or iso_now(),
        "run_id": run_id,
        "jetson_id": jetson_id,
        "channel": channel,
        "event": event,
        "status": status,
    }
    meta = meta or {}
    for f in META_FIELDS:
        rec[f] = meta.get(f, "unset")
    return rec


def validate(record):
    """Return a list of problems with `record` (empty list == valid v1 record)."""
    errors = []
    if not isinstance(record, dict):
        return ["record is not an object"]
    for key in ENVELOPE_REQUIRED:
        if key not in record:
            errors.append(f"missing required field {key!r}")
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if record.get("channel") not in CHANNELS:
        errors.append(f"channel must be one of {CHANNELS}")
    if record.get("status") not in STATUSES:
        errors.append(f"status must be one of {STATUSES}")
    if not isinstance(record.get("ts"), str):
        errors.append("ts must be a string")
    # channel-specific minimum payloads
    if record.get("channel") == "power" and "current_mA" not in record:
        errors.append("power records require current_mA")
    if record.get("channel") == "boot" and "boot_id" not in record:
        errors.append("boot records require boot_id")
    return errors


def emit(fp, record):
    """Validate then write one compact JSONL line + newline. Raises on invalid."""
    errs = validate(record)
    if errs:
        raise ValueError("invalid event record: " + "; ".join(errs))
    fp.write(json.dumps(record, separators=(",", ":")) + "\n")
    fp.flush()


def read_events(path):
    """Yield parsed records from a JSONL file, skipping blank lines."""
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)
