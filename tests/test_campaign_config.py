"""Tests for the 2026 campaign configuration helpers."""

import unittest

from coordinator.campaign_config import (
    CAMPAIGN_BEAM_ENERGIES_MEV,
    CAMPAIGN_DUT_TYPES,
    DEFAULT_FLUX_P_CM2_S,
    MAX_FLUX_P_CM2_S,
    MIN_FLUX_P_CM2_S,
    calculate_fluence,
    create_custom_shield_configuration,
    create_reference_shield_configuration,
    format_campaign_summary,
    get_shield_configuration,
    reference_levels_for,
    validate_flux,
)


class TestCampaignConfig(unittest.TestCase):
    def test_campaign_energies(self) -> None:
        self.assertEqual(CAMPAIGN_BEAM_ENERGIES_MEV, (50, 63, 125, 200))

    def test_initial_dut_type(self) -> None:
        self.assertEqual(CAMPAIGN_DUT_TYPES, ("Jetson Orin Nano",))

    def test_mlc2_12_equivalence(self) -> None:
        config = get_shield_configuration("MLC2", 12)
        self.assertEqual(config.actual_thickness_mm, 10.83)
        self.assertEqual(config.configuration_id, "M2-E12")

    def test_typed_preset_keeps_exact_existing_mapping(self) -> None:
        config = create_reference_shield_configuration("MLC2", 12.0)
        self.assertEqual(config.actual_thickness_mm, 10.83)
        self.assertEqual(config.configuration_id, "M2-E12")

    def test_custom_mlc1_reference(self) -> None:
        config = create_reference_shield_configuration("MLC1", 10.5)
        self.assertEqual(config.reference_mm, 10.5)
        self.assertEqual(config.actual_thickness_mm, 10.5)
        self.assertEqual(config.configuration_id, "M1-R10.5")

    def test_custom_mlc2_reference_uses_existing_ratio(self) -> None:
        config = create_reference_shield_configuration("MLC2", 10.0)
        self.assertEqual(config.reference_mm, 10.0)
        self.assertEqual(config.actual_thickness_mm, 9.03)
        self.assertEqual(config.configuration_id, "M2-E10")

    def test_non_positive_custom_reference_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_reference_shield_configuration("MLC1", 0)

    def test_aluminium_16_equivalence(self) -> None:
        config = get_shield_configuration("Aluminium", 16)
        self.assertEqual(config.actual_thickness_mm, 7.71)
        self.assertEqual(config.configuration_id, "AL-E16")

    def test_bare_control(self) -> None:
        config = get_shield_configuration("Bare", 0)
        self.assertEqual(config.actual_thickness_mm, 0.0)
        self.assertEqual(config.configuration_id, "B00")
        self.assertEqual(reference_levels_for("Bare"), (0,))

    def test_summary_contains_actual_and_id(self) -> None:
        config = get_shield_configuration("MLC2", 8)
        summary = format_campaign_summary(125, config)
        self.assertIn("actual 7.22 mm", summary)
        self.assertIn("M2-E08", summary)

    def test_invalid_predefined_reference_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_shield_configuration("MLC1", 10)

    def test_custom_shield(self) -> None:
        config = create_custom_shield_configuration("Polymer X", 4.25)
        self.assertEqual(config.material, "Polymer X")
        self.assertEqual(config.actual_thickness_mm, 4.25)
        self.assertEqual(config.configuration_id, "CUSTOM")

    def test_empty_custom_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_custom_shield_configuration(" ", 4.25)

    def test_flux_bounds(self) -> None:
        self.assertEqual(MIN_FLUX_P_CM2_S, 1.0e3)
        self.assertEqual(DEFAULT_FLUX_P_CM2_S, 1.0e7)
        self.assertEqual(MAX_FLUX_P_CM2_S, 1.0e8)

    def test_validate_flux_accepts_scientific_value(self) -> None:
        self.assertEqual(validate_flux(2.5e6), 2.5e6)

    def test_validate_flux_rejects_out_of_range_value(self) -> None:
        with self.assertRaises(ValueError):
            validate_flux(1.0e2)

    def test_fluence_is_flux_times_time(self) -> None:
        self.assertEqual(calculate_fluence(1.0e7, 12.5), 1.25e8)

    def test_negative_elapsed_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_fluence(1.0e7, -1.0)


if __name__ == "__main__":
    unittest.main()
