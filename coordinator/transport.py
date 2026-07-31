"""Transport implementations for Test Coordinator commands."""

from __future__ import annotations

import json
import socket
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from coordinator.request import (
    StopTestRequest,
    TestRequest,
)


MAX_RESPONSE_BYTES = 65_536

RequestType = TestRequest | StopTestRequest


def utc_timestamp() -> str:
    """Return the current UTC timestamp."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def validate_request_type(
    request: RequestType,
) -> None:
    """Confirm that the transport received a supported request."""

    if not isinstance(
        request,
        (TestRequest, StopTestRequest),
    ):
        raise TypeError(
            "request must be a TestRequest "
            "or StopTestRequest"
        )


class TransportError(RuntimeError):
    """Raised when delivery or acknowledgment fails."""


class Transport(ABC):
    """Interface implemented by coordinator transports."""

    mode_name = "unknown"

    @abstractmethod
    def send(
        self,
        request: RequestType,
    ) -> dict[str, Any]:
        """Send one command and return its acknowledgment."""


class MockTransport(Transport):
    """Return a simulated accepted response."""

    mode_name = "mock"

    def send(
        self,
        request: RequestType,
    ) -> dict[str, Any]:
        """Simulate successful command delivery."""

        validate_request_type(request)

        return {
            "protocol_version": request.protocol_version,
            "status": "ACCEPTED",
            "request_id": request.request_id,
            "received_at_utc": utc_timestamp(),
            "transport_mode": self.mode_name,
        }


class TcpTransport(Transport):
    """Send one newline-delimited JSON command over TCP."""

    mode_name = "tcp"

    def __init__(
        self,
        host: str,
        port: int,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(host, str) or not host.strip():
            raise ValueError(
                "host must be a non-empty string"
            )

        if type(port) is not int:
            raise TypeError(
                "port must be an integer"
            )

        if not 1 <= port <= 65_535:
            raise ValueError(
                "port must be from 1 to 65535"
            )

        if not isinstance(
            timeout_seconds,
            (int, float),
        ):
            raise TypeError(
                "timeout_seconds must be numeric"
            )

        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        self.host = host.strip()
        self.port = port
        self.timeout_seconds = float(
            timeout_seconds
        )

    def send(
        self,
        request: RequestType,
    ) -> dict[str, Any]:
        """Send one command and wait for one response."""

        validate_request_type(request)

        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout_seconds,
            ) as connection:
                connection.settimeout(
                    self.timeout_seconds
                )

                with connection.makefile(
                    "rwb"
                ) as stream:
                    request_bytes = (
                        request.to_json() + "\n"
                    ).encode("utf-8")

                    stream.write(request_bytes)
                    stream.flush()

                    response_line = stream.readline(
                        MAX_RESPONSE_BYTES + 1
                    )

        except (
            OSError,
            TimeoutError,
            socket.timeout,
        ) as error:
            raise TransportError(
                "Could not communicate with "
                f"{self.host}:{self.port}: {error}"
            ) from error

        if not response_line:
            raise TransportError(
                "Receiver closed the connection "
                "without returning a response"
            )

        if len(response_line) > MAX_RESPONSE_BYTES:
            raise TransportError(
                "Receiver response exceeded "
                "the maximum allowed size"
            )

        try:
            response_text = response_line.decode(
                "utf-8"
            )
        except UnicodeDecodeError as error:
            raise TransportError(
                "Receiver response was not valid UTF-8"
            ) from error

        try:
            response = json.loads(response_text)
        except json.JSONDecodeError as error:
            raise TransportError(
                "Receiver response was not valid JSON"
            ) from error

        if not isinstance(response, dict):
            raise TransportError(
                "Receiver response must be a JSON object"
            )

        response_request_id = response.get(
            "request_id"
        )

        if response_request_id != request.request_id:
            raise TransportError(
                "Receiver response request_id "
                "does not match the submitted command"
            )

        return response