"""Tests for mock and TCP transport configuration."""

import unittest

from coordinator.request import (
    StopTestRequest,
    TestRequest,
)
from coordinator.transport import (
    MockTransport,
    TcpTransport,
)


class TestMockTransport(unittest.TestCase):
    def test_start_request_is_accepted(self) -> None:
        request = TestRequest.create(
            beam_energy_mev=100,
            shielding_material="MLC1",
            shielding_thickness_mm=12,
        )

        response = MockTransport().send(request)

        self.assertEqual(
            response["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            response["request_id"],
            request.request_id,
        )
        self.assertEqual(
            response["transport_mode"],
            "mock",
        )

    def test_stop_request_is_accepted(self) -> None:
        request = StopTestRequest.create(
            target_request_id="start-request-123"
        )

        response = MockTransport().send(request)

        self.assertEqual(
            response["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            response["request_id"],
            request.request_id,
        )
        self.assertEqual(
            response["transport_mode"],
            "mock",
        )

    def test_unsupported_request_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            MockTransport().send("START_TEST")


class TestTcpTransportConfiguration(
    unittest.TestCase
):
    def test_valid_configuration(self) -> None:
        transport = TcpTransport(
            host="127.0.0.1",
            port=6000,
            timeout_seconds=5.0,
        )

        self.assertEqual(
            transport.host,
            "127.0.0.1",
        )
        self.assertEqual(
            transport.port,
            6000,
        )
        self.assertEqual(
            transport.timeout_seconds,
            5.0,
        )
        self.assertEqual(
            transport.mode_name,
            "tcp",
        )

    def test_empty_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TcpTransport(
                host="",
                port=6000,
            )

    def test_invalid_port_type_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            TcpTransport(
                host="127.0.0.1",
                port="6000",
            )

    def test_invalid_port_range_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            TcpTransport(
                host="127.0.0.1",
                port=70_000,
            )

    def test_invalid_timeout_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            TcpTransport(
                host="127.0.0.1",
                port=6000,
                timeout_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()