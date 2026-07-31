"""Validated Test Coordinator command models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from coordinator.constants import (
    BEAM_ENERGIES_MEV,
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
    sent_at_utc: str

    @classmethod
    def create(
        cls,
        beam_energy_mev: int,
        shielding_material: str,
        shielding_thickness_mm: int,
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

        return cls(
            protocol_version=PROTOCOL_VERSION,
            command=START_TEST_COMMAND,
            request_id=str(uuid4()),
            beam_energy_mev=beam_energy_mev,
            shielding_material=shielding_material,
            shielding_thickness_mm=shielding_thickness_mm,
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