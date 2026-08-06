"""Tests for the BASELINE_TEST request model and its transport acceptance."""

import json
import unittest

from coordinator.constants import (
    DEFAULT_BASELINE_MINUTES,
    MAX_BASELINE_MINUTES,
)
from coordinator.request import (
    BaselineTestRequest,
    TestRequest,
)
from coordinator.transport import (
    MockTransport,
    validate_request_type,
)


class TestBaselineTestRequest(unittest.TestCase):
    def test_valid_baseline_request(self) -> None:
        request = BaselineTestRequest.create(
            duration_minutes=60
        )

        self.assertEqual(request.protocol_version, 1)
        self.assertEqual(
            request.command,
            "BASELINE_TEST",
        )
        self.assertEqual(request.duration_minutes, 60)
        self.assertEqual(request.duration_s, 3600)
        self.assertTrue(request.request_id)
        self.assertTrue(
            request.sent_at_utc.endswith("Z")
        )

    def test_defaults_to_the_reference_hour(self) -> None:
        request = BaselineTestRequest.create()

        self.assertEqual(
            request.duration_minutes,
            DEFAULT_BASELINE_MINUTES,
        )
        self.assertEqual(
            request.duration_s,
            DEFAULT_BASELINE_MINUTES * 60,
        )

    def test_carries_no_beam_parameters(self) -> None:
        # A baseline is measured with the beam off; sending an energy or a
        # shielding config would record a condition that never existed.
        payload = BaselineTestRequest.create(
            duration_minutes=5
        ).to_dict()

        self.assertNotIn("beam_energy_mev", payload)
        self.assertNotIn("shielding_material", payload)
        self.assertNotIn(
            "shielding_thickness_mm",
            payload,
        )

    def test_baseline_json_output(self) -> None:
        request = BaselineTestRequest.create(30)
        payload = json.loads(request.to_json())

        self.assertEqual(
            payload["command"],
            "BASELINE_TEST",
        )
        self.assertEqual(payload["duration_s"], 1800)
        self.assertEqual(
            payload["duration_minutes"],
            30,
        )

    def test_rejects_non_positive_duration(self) -> None:
        for minutes in (0, -1):
            with self.subTest(minutes=minutes):
                with self.assertRaises(ValueError):
                    BaselineTestRequest.create(minutes)

    def test_rejects_duration_above_the_cap(self) -> None:
        with self.assertRaises(ValueError):
            BaselineTestRequest.create(
                MAX_BASELINE_MINUTES + 1
            )

    def test_rejects_non_numeric_duration(self) -> None:
        for minutes in ("60", None, True):
            with self.subTest(minutes=minutes):
                with self.assertRaises(TypeError):
                    BaselineTestRequest.create(minutes)

    def test_request_ids_are_unique(self) -> None:
        first = BaselineTestRequest.create(1)
        second = BaselineTestRequest.create(1)

        self.assertNotEqual(
            first.request_id,
            second.request_id,
        )


class TestCustomBeamEnergy(unittest.TestCase):
    """Beam energy mirrors shielding: a campaign preset, or a custom value."""

    def test_campaign_presets_are_accepted(self) -> None:
        for energy in (50, 63, 100, 200):
            with self.subTest(energy=energy):
                request = TestRequest.create(energy, "MLC1", 12)
                self.assertEqual(
                    request.beam_energy_mev,
                    energy,
                )
                self.assertEqual(
                    request.beam_energy_mode,
                    "preset",
                )

    def test_125_is_no_longer_a_preset(self) -> None:
        # Replaced by 100 MeV on 2026-08-06.
        with self.assertRaises(ValueError):
            TestRequest.create(125, "MLC1", 12)

    def test_custom_energy_is_accepted(self) -> None:
        request = TestRequest.create(
            74.5,
            "MLC1",
            12,
            beam_energy_mode="custom",
        )

        self.assertEqual(request.beam_energy_mev, 74.5)
        self.assertEqual(
            request.beam_energy_mode,
            "custom",
        )
        self.assertEqual(
            json.loads(request.to_json())["beam_energy_mode"],
            "custom",
        )

    def test_custom_energy_off_the_preset_list_is_allowed(self) -> None:
        # The whole point: an energy nobody planned for is recorded honestly.
        request = TestRequest.create(
            125,
            "MLC1",
            12,
            beam_energy_mode="custom",
        )
        self.assertEqual(request.beam_energy_mev, 125)

    def test_custom_energy_must_be_positive_and_finite(self) -> None:
        for bad in (0, -10):
            with self.subTest(energy=bad):
                with self.assertRaises(ValueError):
                    TestRequest.create(
                        bad,
                        "MLC1",
                        12,
                        beam_energy_mode="custom",
                    )

        for bad in ("80", None, True):
            with self.subTest(energy=bad):
                with self.assertRaises(TypeError):
                    TestRequest.create(
                        bad,
                        "MLC1",
                        12,
                        beam_energy_mode="custom",
                    )

    def test_unknown_energy_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TestRequest.create(
                100,
                "MLC1",
                12,
                beam_energy_mode="whatever",
            )


class TestBaselineTransportAcceptance(unittest.TestCase):
    def test_transport_accepts_baseline_requests(self) -> None:
        request = BaselineTestRequest.create(1)

        validate_request_type(request)

        response = MockTransport().send(request)

        self.assertEqual(
            response["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            response["request_id"],
            request.request_id,
        )

    def test_transport_still_rejects_foreign_objects(self) -> None:
        with self.assertRaises(TypeError):
            validate_request_type(
                {"command": "BASELINE_TEST"}
            )


if __name__ == "__main__":
    unittest.main()
