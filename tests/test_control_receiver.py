"""Validation tests for the DUT test-control receiver."""

from __future__ import annotations

import copy
import importlib.util
import math
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPOSITORY_ROOT
    / "jetson"
    / "control"
    / "control_receiver.py"
)

SPEC = importlib.util.spec_from_file_location(
    "control_receiver",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load control_receiver.py")

control_receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control_receiver)


class ReceiverValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = copy.deepcopy(control_receiver.DEFAULTS)

    def request(self, **updates: object) -> dict[str, object]:
        message: dict[str, object] = {
            "protocol_version": 1,
            "command": "START_TEST",
            "request_id": "request-123",
            "beam_energy_mev": 100,
            "shielding_material": "MLC1",
            "shielding_thickness_mm": 12,
            "duration_s": 100,
            "sent_at_utc": "2026-08-04T20:00:00.000Z",
        }
        message.update(updates)
        return message

    def assert_valid(self, message: dict[str, object]) -> None:
        command, errors = control_receiver.validate(message, self.cfg)
        self.assertEqual(command, "START_TEST")
        self.assertEqual(errors, [])

    def assert_invalid(
        self,
        message: dict[str, object],
        expected_text: str,
    ) -> None:
        _, errors = control_receiver.validate(message, self.cfg)
        self.assertTrue(
            any(expected_text in error for error in errors),
            errors,
        )

    def test_legacy_preset_request_is_accepted(self) -> None:
        self.assert_valid(self.request())

    def test_bare_zero_is_accepted(self) -> None:
        self.assert_valid(
            self.request(
                shielding_material="Bare",
                shielding_thickness_mm=0,
            )
        )

    def test_mlc2_preset_with_actual_thickness_is_accepted(self) -> None:
        self.assert_valid(
            self.request(
                shielding_mode="preset",
                shielding_material="MLC2",
                shielding_thickness_mm=12,
                shielding_reference_mm=12,
                shielding_actual_thickness_mm=10.83,
                shielding_configuration_id="M2-E12",
            )
        )

    def test_aluminium_preset_with_actual_thickness_is_accepted(self) -> None:
        self.assert_valid(
            self.request(
                shielding_mode="preset",
                shielding_material="Aluminium",
                shielding_thickness_mm=12,
                shielding_reference_mm=12,
                shielding_actual_thickness_mm=5.78,
                shielding_configuration_id="AL-E12",
            )
        )

    def test_custom_known_material_is_accepted(self) -> None:
        self.assert_valid(
            self.request(
                shielding_mode="custom",
                shielding_material="MLC2",
                shielding_thickness_mm=10.5,
                shielding_actual_thickness_mm=10.5,
                shielding_configuration_id="CUSTOM",
            )
        )

    def test_custom_material_is_accepted(self) -> None:
        self.assert_valid(
            self.request(
                shielding_mode="custom",
                shielding_material="Tungsten",
                shielding_thickness_mm=5.5,
                shielding_actual_thickness_mm=5.5,
                shielding_configuration_id="CUSTOM",
                campaign_metadata={
                    "dut_serial": "ORIN-01",
                    "flux_p_cm2_s": 1.0e7,
                },
            )
        )

    def test_blank_custom_material_is_rejected(self) -> None:
        self.assert_invalid(
            self.request(
                shielding_mode="custom",
                shielding_material="   ",
                shielding_thickness_mm=5.5,
            ),
            "must not be blank",
        )

    def test_zero_custom_thickness_is_rejected(self) -> None:
        self.assert_invalid(
            self.request(
                shielding_mode="custom",
                shielding_material="Tungsten",
                shielding_thickness_mm=0,
            ),
            "must be greater than 0",
        )

    def test_nan_custom_thickness_is_rejected(self) -> None:
        self.assert_invalid(
            self.request(
                shielding_mode="custom",
                shielding_material="Tungsten",
                shielding_thickness_mm=math.nan,
            ),
            "must be finite",
        )

    def test_infinite_custom_thickness_is_rejected(self) -> None:
        self.assert_invalid(
            self.request(
                shielding_mode="custom",
                shielding_material="Tungsten",
                shielding_thickness_mm=math.inf,
            ),
            "must be finite",
        )

    def test_invalid_preset_reference_is_rejected(self) -> None:
        self.assert_invalid(
            self.request(
                shielding_mode="preset",
                shielding_material="MLC1",
                shielding_thickness_mm=13,
            ),
            "must be one of",
        )

    def test_non_object_campaign_metadata_is_rejected(self) -> None:
        self.assert_invalid(
            self.request(campaign_metadata="not-an-object"),
            "campaign_metadata must be a JSON object",
        )


if __name__ == "__main__":
    unittest.main()
