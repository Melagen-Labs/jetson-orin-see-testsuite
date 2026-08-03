"""Tests for START_TEST and STOP_TEST request models."""

import json
import unittest

from coordinator.request import (
    StopTestRequest,
    TestRequest,
)


class TestStartTestRequest(unittest.TestCase):
    def test_valid_start_request(self) -> None:
        request = TestRequest.create(
            beam_energy_mev=200,
            shielding_material="MLC1",
            shielding_thickness_mm=12,
        )

        self.assertEqual(request.protocol_version, 1)
        self.assertEqual(request.command, "START_TEST")
        self.assertEqual(request.beam_energy_mev, 200)
        self.assertEqual(
            request.shielding_material,
            "MLC1",
        )
        self.assertEqual(
            request.shielding_thickness_mm,
            12,
        )
        self.assertTrue(request.request_id)
        self.assertTrue(
            request.sent_at_utc.endswith("Z")
        )

    def test_start_json_output(self) -> None:
        request = TestRequest.create(
            50,
            "Aluminium",
            8,
        )

        payload = json.loads(request.to_json())

        self.assertEqual(
            payload["command"],
            "START_TEST",
        )
        self.assertEqual(
            payload["beam_energy_mev"],
            50,
        )
        self.assertEqual(
            payload["shielding_material"],
            "Aluminium",
        )
        self.assertEqual(
            payload["shielding_thickness_mm"],
            8,
        )

    def test_default_duration_is_100(self) -> None:
        request = TestRequest.create(
            200,
            "MLC1",
            12,
        )

        self.assertEqual(request.duration_s, 100)
        self.assertEqual(
            json.loads(request.to_json())["duration_s"],
            100,
        )

    def test_custom_duration_is_carried(self) -> None:
        request = TestRequest.create(
            200,
            "MLC1",
            12,
            duration_s=250,
        )

        self.assertEqual(request.duration_s, 250)

    def test_zero_duration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TestRequest.create(
                200,
                "MLC1",
                12,
                duration_s=0,
            )

    def test_negative_duration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TestRequest.create(
                200,
                "MLC1",
                12,
                duration_s=-5,
            )

    def test_non_numeric_duration_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            TestRequest.create(
                200,
                "MLC1",
                12,
                duration_s="100",
            )

    def test_boolean_duration_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            TestRequest.create(
                200,
                "MLC1",
                12,
                duration_s=True,
            )

    def test_invalid_energy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TestRequest.create(
                55,
                "MLC1",
                12,
            )

    def test_invalid_material_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TestRequest.create(
                200,
                "Copper",
                12,
            )

    def test_invalid_thickness_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TestRequest.create(
                200,
                "MLC1",
                13,
            )

    def test_incorrect_energy_type_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            TestRequest.create(
                "100",
                "MLC1",
                12,
            )


class TestStopTestRequest(unittest.TestCase):
    def test_valid_stop_request(self) -> None:
        request = StopTestRequest.create(
            target_request_id="start-request-123"
        )

        self.assertEqual(request.protocol_version, 1)
        self.assertEqual(request.command, "STOP_TEST")
        self.assertTrue(request.request_id)
        self.assertEqual(
            request.target_request_id,
            "start-request-123",
        )
        self.assertTrue(
            request.sent_at_utc.endswith("Z")
        )

    def test_stop_json_output(self) -> None:
        request = StopTestRequest.create(
            target_request_id="start-request-456"
        )

        payload = json.loads(request.to_json())

        self.assertEqual(
            payload["command"],
            "STOP_TEST",
        )
        self.assertEqual(
            payload["target_request_id"],
            "start-request-456",
        )
        self.assertTrue(payload["request_id"])

    def test_empty_target_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StopTestRequest.create("")

    def test_whitespace_target_id_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            StopTestRequest.create("   ")

    def test_target_id_type_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            StopTestRequest.create(123)

    def test_stop_command_ids_are_unique(self) -> None:
        first = StopTestRequest.create(
            "start-request-123"
        )
        second = StopTestRequest.create(
            "start-request-123"
        )

        self.assertNotEqual(
            first.request_id,
            second.request_id,
        )


if __name__ == "__main__":
    unittest.main()