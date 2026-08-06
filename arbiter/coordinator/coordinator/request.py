"""Validated Test Coordinator command models."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
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


def _validated_number(
    value: object,
    field_name: str,
    *,
    allow_zero: bool,
) -> float:
    """Return a finite numeric value after applying its lower bound."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")

    numeric = float(value)

    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite")

    if allow_zero:
        if numeric < 0:
            raise ValueError(f"{field_name} must not be negative")
    elif numeric <= 0:
        raise ValueError(f"{field_name} must be greater than 0")

    return numeric


@dataclass(frozen=True)
class TestRequest:
    """Represent one validated START_TEST request."""

    protocol_version: int
    command: str
    request_id: str
    beam_energy_mev: int
    shielding_material: str
    shielding_thickness_mm: int | float
    duration_s: int | float
    sent_at_utc: str
    shielding_mode: str = "preset"
    shielding_reference_mm: int | float | None = None
    shielding_actual_thickness_mm: int | float | None = None
    shielding_configuration_id: str = ""
    campaign_metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        beam_energy_mev: int,
        shielding_material: str,
        shielding_thickness_mm: int | float,
        duration_s: int | float = DEFAULT_DURATION_S,
        *,
        shielding_mode: str = "preset",
        shielding_reference_mm: int | float | None = None,
        shielding_actual_thickness_mm: int | float | None = None,
        shielding_configuration_id: str = "",
        campaign_metadata: dict[str, object] | None = None,
    ) -> "TestRequest":
        """Validate selections and create a START_TEST request."""

        if type(beam_energy_mev) is not int:
            raise TypeError("beam_energy_mev must be an integer")

        if beam_energy_mev not in BEAM_ENERGIES_MEV:
            raise ValueError(
                f"Unsupported beam energy: {beam_energy_mev}. "
                f"Allowed values: {BEAM_ENERGIES_MEV}"
            )

        if not isinstance(shielding_mode, str):
            raise TypeError("shielding_mode must be a string")

        normalized_mode = shielding_mode.strip().lower()
        if normalized_mode not in {"preset", "custom"}:
            raise ValueError("shielding_mode must be preset or custom")

        if not isinstance(shielding_material, str):
            raise TypeError("shielding_material must be a string")

        normalized_material = shielding_material.strip()
        if not normalized_material:
            raise ValueError("shielding_material must not be blank")

        if normalized_mode == "preset":
            if normalized_material not in SHIELDING_MATERIALS:
                raise ValueError(
                    "Unsupported shielding material: "
                    f"{normalized_material}. "
                    f"Allowed values: {SHIELDING_MATERIALS}"
                )

            if type(shielding_thickness_mm) is not int:
                raise TypeError(
                    "preset shielding_thickness_mm must be an integer"
                )

            if shielding_thickness_mm not in SHIELDING_THICKNESSES_MM:
                raise ValueError(
                    "Unsupported shielding thickness: "
                    f"{shielding_thickness_mm}. "
                    f"Allowed values: {SHIELDING_THICKNESSES_MM}"
                )

            if normalized_material == "Bare" and shielding_thickness_mm != 0:
                raise ValueError("Bare shielding must use reference 0")

            if normalized_material != "Bare" and shielding_thickness_mm == 0:
                raise ValueError("Only Bare shielding may use reference 0")

            if shielding_reference_mm is None:
                shielding_reference_mm = shielding_thickness_mm

            if shielding_reference_mm != shielding_thickness_mm:
                raise ValueError(
                    "preset shielding_reference_mm must match "
                    "shielding_thickness_mm"
                )

            if shielding_actual_thickness_mm is None:
                shielding_actual_thickness_mm = shielding_thickness_mm

            _validated_number(
                shielding_actual_thickness_mm,
                "shielding_actual_thickness_mm",
                allow_zero=normalized_material == "Bare",
            )

        else:
            custom_thickness = _validated_number(
                shielding_thickness_mm,
                "shielding_thickness_mm",
                allow_zero=False,
            )

            if shielding_actual_thickness_mm is None:
                shielding_actual_thickness_mm = custom_thickness

            actual_thickness = _validated_number(
                shielding_actual_thickness_mm,
                "shielding_actual_thickness_mm",
                allow_zero=False,
            )

            if not math.isclose(
                custom_thickness,
                actual_thickness,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    "custom shielding thickness fields do not match"
                )

            if shielding_reference_mm is not None:
                _validated_number(
                    shielding_reference_mm,
                    "shielding_reference_mm",
                    allow_zero=True,
                )

            if not shielding_configuration_id:
                shielding_configuration_id = "CUSTOM"

        if not isinstance(shielding_configuration_id, str):
            raise TypeError("shielding_configuration_id must be a string")

        if campaign_metadata is None:
            campaign_metadata = {}

        if not isinstance(campaign_metadata, dict):
            raise TypeError("campaign_metadata must be a dictionary")

        if isinstance(duration_s, bool) or not isinstance(
            duration_s,
            (int, float),
        ):
            raise TypeError("duration_s must be a positive number")

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
            shielding_material=normalized_material,
            shielding_thickness_mm=shielding_thickness_mm,
            duration_s=duration_s,
            sent_at_utc=utc_timestamp(),
            shielding_mode=normalized_mode,
            shielding_reference_mm=shielding_reference_mm,
            shielding_actual_thickness_mm=shielding_actual_thickness_mm,
            shielding_configuration_id=shielding_configuration_id.strip(),
            campaign_metadata=dict(campaign_metadata),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a dictionary for logging or transmission."""

        return asdict(self)

    def to_json(self) -> str:
        """Return a compact JSON representation."""

        return json.dumps(self.to_dict(), separators=(",", ":"))


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
    def create(cls, target_request_id: str) -> "StopTestRequest":
        """Create a request to stop an active test."""

        if not isinstance(target_request_id, str):
            raise TypeError("target_request_id must be a string")

        normalized_target_id = target_request_id.strip()
        if not normalized_target_id:
            raise ValueError("target_request_id must be a non-empty string")

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

        return json.dumps(self.to_dict(), separators=(",", ":"))
