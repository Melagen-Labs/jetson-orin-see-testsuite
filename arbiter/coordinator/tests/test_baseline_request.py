"""Tests for the BASELINE_TEST request model and its transport acceptance."""

import json
import unittest

from coordinator.constants import (
    DEFAULT_BASELINE_MINUTES,
    MAX_BASELINE_MINUTES,
)
from coordinator.request import BaselineTestRequest
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
