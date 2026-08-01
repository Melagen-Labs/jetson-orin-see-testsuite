"""Tests for Test Coordinator receiver validation and state."""

import unittest

from coordinator.request import (
    StopTestRequest,
    TestRequest,
)
from receiver.test_receiver import (
    ReceiverState,
    RequestValidationError,
    apply_request_to_state,
    create_response,
    validate_request_payload,
)


class TestReceiverValidation(unittest.TestCase):
    def test_valid_start_request_is_accepted(
        self,
    ) -> None:
        request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        validated = validate_request_payload(
            request.to_dict()
        )

        self.assertEqual(
            validated["command"],
            "START_TEST",
        )
        self.assertEqual(
            validated["request_id"],
            request.request_id,
        )

    def test_valid_stop_request_is_accepted(
        self,
    ) -> None:
        request = StopTestRequest.create(
            "start-request-123"
        )

        validated = validate_request_payload(
            request.to_dict()
        )

        self.assertEqual(
            validated["command"],
            "STOP_TEST",
        )
        self.assertEqual(
            validated["target_request_id"],
            "start-request-123",
        )

    def test_missing_command_is_rejected(
        self,
    ) -> None:
        request = TestRequest.create(
            53,
            "Aluminium",
            8,
        )

        payload = request.to_dict()
        del payload["command"]

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_missing_duration_is_rejected(
        self,
    ) -> None:
        request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        payload = request.to_dict()
        del payload["duration_s"]

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_non_positive_duration_is_rejected(
        self,
    ) -> None:
        request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        payload = request.to_dict()
        payload["duration_s"] = 0

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_unexpected_start_field_is_rejected(
        self,
    ) -> None:
        request = TestRequest.create(
            200,
            "MLC2",
            16,
        )

        payload = request.to_dict()
        payload["shell_command"] = "not permitted"

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_unexpected_stop_field_is_rejected(
        self,
    ) -> None:
        request = StopTestRequest.create(
            "start-request-123"
        )

        payload = request.to_dict()
        payload["beam_energy_mev"] = 100

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_invalid_energy_is_rejected(
        self,
    ) -> None:
        request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        payload = request.to_dict()
        payload["beam_energy_mev"] = 75

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_empty_stop_target_is_rejected(
        self,
    ) -> None:
        request = StopTestRequest.create(
            "start-request-123"
        )

        payload = request.to_dict()
        payload["target_request_id"] = ""

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)

    def test_unsupported_command_is_rejected(
        self,
    ) -> None:
        request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        payload = request.to_dict()
        payload["command"] = "REBOOT"

        with self.assertRaises(
            RequestValidationError
        ):
            validate_request_payload(payload)


class TestReceiverState(unittest.TestCase):
    def test_start_sets_active_request_id(
        self,
    ) -> None:
        state = ReceiverState()

        request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        event = apply_request_to_state(
            request.to_dict(),
            state,
        )

        self.assertEqual(
            event,
            "TEST_REQUEST_ACCEPTED",
        )
        self.assertEqual(
            state.active_request_id,
            request.request_id,
        )

    def test_duplicate_start_is_rejected(
        self,
    ) -> None:
        state = ReceiverState()

        first = TestRequest.create(
            100,
            "MLC1",
            12,
        )
        second = TestRequest.create(
            53,
            "Aluminium",
            8,
        )

        apply_request_to_state(
            first.to_dict(),
            state,
        )

        with self.assertRaises(
            RequestValidationError
        ):
            apply_request_to_state(
                second.to_dict(),
                state,
            )

    def test_stop_without_active_test_is_rejected(
        self,
    ) -> None:
        state = ReceiverState()

        stop_request = StopTestRequest.create(
            "start-request-123"
        )

        with self.assertRaises(
            RequestValidationError
        ):
            apply_request_to_state(
                stop_request.to_dict(),
                state,
            )

    def test_stop_target_must_match_active_test(
        self,
    ) -> None:
        state = ReceiverState(
            active_request_id="start-request-123"
        )

        stop_request = StopTestRequest.create(
            "different-request"
        )

        with self.assertRaises(
            RequestValidationError
        ):
            apply_request_to_state(
                stop_request.to_dict(),
                state,
            )

        self.assertEqual(
            state.active_request_id,
            "start-request-123",
        )

    def test_start_then_stop_clears_state(
        self,
    ) -> None:
        state = ReceiverState()

        start_request = TestRequest.create(
            100,
            "MLC1",
            12,
        )

        apply_request_to_state(
            start_request.to_dict(),
            state,
        )

        stop_request = StopTestRequest.create(
            start_request.request_id
        )

        event = apply_request_to_state(
            stop_request.to_dict(),
            state,
        )

        self.assertEqual(
            event,
            "TEST_STOP_REQUEST_ACCEPTED",
        )
        self.assertIsNone(
            state.active_request_id
        )

    def test_response_preserves_request_id(
        self,
    ) -> None:
        response = create_response(
            request_id="request-123",
            status="ACCEPTED",
        )

        self.assertEqual(
            response["request_id"],
            "request-123",
        )
        self.assertEqual(
            response["status"],
            "ACCEPTED",
        )


if __name__ == "__main__":
    unittest.main()