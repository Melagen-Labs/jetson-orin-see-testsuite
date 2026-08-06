"""Validated Test Coordinator command models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from coordinator.constants import (
    BASELINE_TEST_COMMAND,
    BEAM_ENERGIES_MEV,
    DEFAULT_BASELINE_MINUTES,
    DEFAULT_DURATION_S,
    MAX_BASELINE_MINUTES,
    MAX_DURATION_S,
    PROTOCOL_VERSION,
    SHIELDING_MATERIALS,
    SHIELDING_THICKNESSES_MM,
    START_TEST_COMMAND,
    STOP_TEST_COMMAND,
)


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class TestRequest:
    """Represent one validated START_TEST request."""

    protocol_version: int
    command: str
    request_id: str
    beam_energy_mev: int
    shielding_material: str
    shielding_thickness_mm: int
    duration_s: int
    sent_at_utc: str

    @classmethod
    def create(
        cls,
        beam_energy_mev: int,
        shielding_material: str,
        shielding_thickness_mm: int,
        duration_s: int = DEFAULT_DURATION_S,
    ) -> "TestRequest":
        """Validate selections and create a START_TEST request."""

        if type(beam_energy_mev) is not int:
            raise TypeError(
                "beam_energy_mev must be an integer"
            )

        if beam_energy_mev not in BEAM_ENERGIES_MEV:
            raise ValueError(
                f"Unsupported beam energy: {beam_energy_mev}. "
                f"Allowed values: {BEAM_ENERGIES_MEV}"
            )

        if not isinstance(shielding_material, str):
            raise TypeError(
                "shielding_material must be a string"
            )

        if shielding_material not in SHIELDING_MATERIALS:
            raise ValueError(
                "Unsupported shielding material: "
                f"{shielding_material}. "
                f"Allowed values: {SHIELDING_MATERIALS}"
            )

        if type(shielding_thickness_mm) is not int:
            raise TypeError(
                "shielding_thickness_mm must be an integer"
            )

        if (
            shielding_thickness_mm
            not in SHIELDING_THICKNESSES_MM
        ):
            raise ValueError(
                "Unsupported shielding thickness: "
                f"{shielding_thickness_mm}. "
                f"Allowed values: "
                f"{SHIELDING_THICKNESSES_MM}"
            )

        # duration_s: bool is an int subclass, so reject it explicitly before the
        # numeric range check (the DUT applies the same rule).
        if isinstance(duration_s, bool) or not isinstance(
            duration_s, (int, float)
        ):
            raise TypeError(
                "duration_s must be a positive number"
            )

        if not 0 < duration_s <= MAX_DURATION_S:
            raise ValueError(
                "duration_s must be greater than 0 and at most "
                f"{MAX_DURATION_S}"
            )

        return cls(
            protocol_version=PROTOCOL_VERSION,
            command=START_TEST_COMMAND,
            request_id=str(uuid4()),
            beam_energy_mev=beam_energy_mev,
            shielding_material=shielding_material,
            shielding_thickness_mm=shielding_thickness_mm,
            duration_s=duration_s,
            sent_at_utc=utc_timestamp(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a dictionary for logging or transmission."""

        return asdict(self)

    def to_json(self) -> str:
        """Return a compact JSON representation."""

        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class BaselineTestRequest:
    """Represent one validated BASELINE_TEST request.

    A baseline is a no-beam reference run: the DUT starts the same workloads a
    beam test does and logs its input current to a CSV. There are deliberately no
    beam-energy or shielding fields -- the beam is off, so recording one would put
    a fiction into the run metadata. The operator supplies only how long to run.
    """

    protocol_version: int
    command: str
    request_id: str
    duration_s: int
    duration_minutes: int
    sent_at_utc: str

    @classmethod
    def create(
        cls,
        duration_minutes: int = DEFAULT_BASELINE_MINUTES,
    ) -> "BaselineTestRequest":
        """Validate the requested length (in minutes) and create the request."""

        # bool is an int subclass; reject it before the numeric range check, as
        # TestRequest.create does for duration_s.
        if isinstance(duration_minutes, bool) or not isinstance(
            duration_minutes, (int, float)
        ):
            raise TypeError(
                "duration_minutes must be a positive number"
            )

        if not 0 < duration_minutes <= MAX_BASELINE_MINUTES:
            raise ValueError(
                "duration_minutes must be greater than 0 and at most "
                f"{MAX_BASELINE_MINUTES}"
            )

        # The wire contract is in seconds (shared with START_TEST); minutes are
        # purely the operator-facing unit.
        duration_s = int(round(duration_minutes * 60))

        return cls(
            protocol_version=PROTOCOL_VERSION,
            command=BASELINE_TEST_COMMAND,
            request_id=str(uuid4()),
            duration_s=duration_s,
            duration_minutes=duration_minutes,
            sent_at_utc=utc_timestamp(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a dictionary for logging or transmission."""

        return asdict(self)

    def to_json(self) -> str:
        """Return a compact JSON representation."""

        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class StopTestRequest:
    """Represent one validated STOP_TEST request."""

    protocol_version: int
    command: str
    request_id: str
    target_request_id: str
    sent_at_utc: str

    @classmethod
    def create(
        cls,
        target_request_id: str,
    ) -> "StopTestRequest":
        """Create a request to stop an active test."""

        if not isinstance(target_request_id, str):
            raise TypeError(
                "target_request_id must be a string"
            )

        normalized_target_id = target_request_id.strip()

        if not normalized_target_id:
            raise ValueError(
                "target_request_id must be a non-empty string"
            )

        return cls(
            protocol_version=PROTOCOL_VERSION,
            command=STOP_TEST_COMMAND,
            request_id=str(uuid4()),
            target_request_id=normalized_target_id,
            sent_at_utc=utc_timestamp(),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a dictionary for logging or transmission."""

        return asdict(self)

    def to_json(self) -> str:
        """Return a compact JSON representation."""

        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
        )