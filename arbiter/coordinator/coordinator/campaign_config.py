"""2026 proton-campaign beam, DUT, flux, and shielding configuration."""

from __future__ import annotations

import math
from dataclasses import dataclass


CAMPAIGN_BEAM_ENERGIES_MEV = (50, 63, 125, 200)
CAMPAIGN_DUT_TYPES = ("Jetson Orin Nano",)
CUSTOM_SHIELD_MATERIAL = "Custom"
CAMPAIGN_SHIELDING_MATERIALS = (
    "Bare",
    "MLC1",
    "MLC2",
    "Aluminium",
    CUSTOM_SHIELD_MATERIAL,
)
MLC1_REFERENCE_LEVELS_MM = (8, 12, 16)
MLC2_REFERENCE_TO_ACTUAL_RATIO = 0.9025
MIN_FLUX_P_CM2_S = 1.0e3
MAX_FLUX_P_CM2_S = 1.0e8
DEFAULT_FLUX_P_CM2_S = 1.0e7


@dataclass(frozen=True)
class ShieldConfiguration:
    """One operator-selectable campaign shielding configuration."""

    material: str
    reference_mm: float
    actual_thickness_mm: float
    configuration_id: str


SHIELD_CONFIGURATIONS: dict[str, dict[int, ShieldConfiguration]] = {
    "Bare": {
        0: ShieldConfiguration("Bare", 0.0, 0.0, "B00"),
    },
    "MLC1": {
        8: ShieldConfiguration("MLC1", 8.0, 8.00, "M1-08"),
        12: ShieldConfiguration("MLC1", 12.0, 12.00, "M1-12"),
        16: ShieldConfiguration("MLC1", 16.0, 16.00, "M1-16"),
    },
    "MLC2": {
        8: ShieldConfiguration("MLC2", 8.0, 7.22, "M2-E08"),
        12: ShieldConfiguration("MLC2", 12.0, 10.83, "M2-E12"),
        16: ShieldConfiguration("MLC2", 16.0, 14.44, "M2-E16"),
    },
    "Aluminium": {
        8: ShieldConfiguration("Aluminium", 8.0, 3.85, "AL-E08"),
        12: ShieldConfiguration("Aluminium", 12.0, 5.78, "AL-E12"),
        16: ShieldConfiguration("Aluminium", 16.0, 7.71, "AL-E16"),
    },
}


def reference_levels_for(material: str) -> tuple[int, ...]:
    """Return preset reference selections for one predefined material."""

    if material == CUSTOM_SHIELD_MATERIAL:
        return ()

    try:
        return tuple(SHIELD_CONFIGURATIONS[material])
    except KeyError as error:
        raise ValueError(f"Unsupported shielding material: {material}") from error


def get_shield_configuration(
    material: str,
    reference_mm: int,
) -> ShieldConfiguration:
    """Resolve a predefined material/reference choice to the physical coupon."""

    if not isinstance(material, str):
        raise TypeError("material must be a string")

    if type(reference_mm) is not int:
        raise TypeError("reference_mm must be an integer")

    if material == CUSTOM_SHIELD_MATERIAL:
        raise ValueError("Custom shields require a name and physical thickness")

    try:
        return SHIELD_CONFIGURATIONS[material][reference_mm]
    except KeyError as error:
        allowed = reference_levels_for(material)
        raise ValueError(
            f"Unsupported reference level {reference_mm} for {material}. "
            f"Allowed preset values: {allowed}"
        ) from error


def _validate_positive_number(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")

    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return numeric


def _format_reference_token(reference_mm: float) -> str:
    """Format a reference value for a human-readable configuration ID."""

    if reference_mm.is_integer():
        return f"{int(reference_mm):02d}"
    return f"{reference_mm:g}"


def create_reference_shield_configuration(
    material: str,
    reference_mm: float,
) -> ShieldConfiguration:
    """Resolve MLC1/MLC2 presets or calculate a typed custom reference level.

    Existing 8/12/16 mappings are returned verbatim. Other positive values are
    coordinator-side preview configurations until the deployed DUT protocol accepts
    arbitrary reference levels.
    """

    if material not in ("MLC1", "MLC2"):
        raise ValueError("custom reference levels are supported only for MLC1/MLC2")

    reference = _validate_positive_number(reference_mm, "reference_mm")

    for preset in MLC1_REFERENCE_LEVELS_MM:
        if math.isclose(reference, float(preset), rel_tol=0.0, abs_tol=1e-9):
            return SHIELD_CONFIGURATIONS[material][preset]

    token = _format_reference_token(reference)
    if material == "MLC1":
        actual = reference
        configuration_id = f"M1-R{token}"
    else:
        actual = round(reference * MLC2_REFERENCE_TO_ACTUAL_RATIO, 2)
        configuration_id = f"M2-E{token}"

    return ShieldConfiguration(
        material=material,
        reference_mm=reference,
        actual_thickness_mm=actual,
        configuration_id=configuration_id,
    )


def create_custom_shield_configuration(
    name: str,
    thickness_mm: float,
) -> ShieldConfiguration:
    """Validate operator-entered custom shield details for GUI metadata."""

    if not isinstance(name, str):
        raise TypeError("custom shield name must be a string")

    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("custom shield name is required")

    numeric_thickness = _validate_positive_number(
        thickness_mm,
        "custom shield thickness",
    )

    return ShieldConfiguration(
        material=normalized_name,
        reference_mm=0.0,
        actual_thickness_mm=numeric_thickness,
        configuration_id="CUSTOM",
    )


def calculate_fluence(
    flux_p_cm2_s: float,
    elapsed_seconds: float,
) -> float:
    """Calculate accumulated fluence as flux multiplied by elapsed time."""

    for value, field_name in (
        (flux_p_cm2_s, "flux_p_cm2_s"),
        (elapsed_seconds, "elapsed_seconds"),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field_name} must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite")

    if flux_p_cm2_s < 0:
        raise ValueError("flux_p_cm2_s must not be negative")
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must not be negative")

    return float(flux_p_cm2_s) * float(elapsed_seconds)


def validate_flux(flux_p_cm2_s: float) -> float:
    """Validate a facility flux against the GUI's supported operating range."""

    numeric = _validate_positive_number(flux_p_cm2_s, "flux_p_cm2_s")
    if not MIN_FLUX_P_CM2_S <= numeric <= MAX_FLUX_P_CM2_S:
        raise ValueError(
            "flux_p_cm2_s must be between "
            f"{MIN_FLUX_P_CM2_S:.0e} and {MAX_FLUX_P_CM2_S:.0e}"
        )
    return numeric


def format_scientific(value: float) -> str:
    """Format a run quantity using compact scientific notation."""

    return f"{float(value):.3e}"


def format_campaign_summary(
    beam_energy_mev: int,
    configuration: ShieldConfiguration,
) -> str:
    """Build the compact operator-facing configuration summary."""

    if configuration.material == "Bare":
        return f"{beam_energy_mev} MeV | Bare control | {configuration.configuration_id}"

    if configuration.configuration_id == "CUSTOM":
        return (
            f"{beam_energy_mev} MeV | Custom: {configuration.material} | "
            f"actual {configuration.actual_thickness_mm:.2f} mm | CUSTOM"
        )

    return (
        f"{beam_energy_mev} MeV | {configuration.material} | "
        f"ref {configuration.reference_mm:g} mm -> "
        f"actual {configuration.actual_thickness_mm:.2f} mm | "
        f"{configuration.configuration_id}"
    )
