"""TCP receiver for Jetson proton-test control requests."""

from __future__ import annotations

import argparse
import json
import math
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coordinator.constants import (
    BEAM_ENERGIES_MEV,
    MAX_DURATION_S,
    PROTOCOL_VERSION,
    SHIELDING_MATERIALS,
    SHIELDING_THICKNESSES_MM,
    START_TEST_COMMAND,
    STOP_TEST_COMMAND,
)
from coordinator.event_logger import EventLogger
from coordinator.event_logger import utc_timestamp


MAX_MESSAGE_BYTES = 65_536

START_REQUIRED_FIELDS = {
    "protocol_version",
    "command",
    "request_id",
    "beam_energy_mev",
    "shielding_material",
    "shielding_thickness_mm",
    "duration_s",
    "sent_at_utc",
}

START_OPTIONAL_FIELDS = {
    "shielding_mode",
    "shielding_reference_mm",
    "shielding_actual_thickness_mm",
    "shielding_configuration_id",
    "campaign_metadata",
}

STOP_REQUIRED_FIELDS = {
    "protocol_version",
    "command",
    "request_id",
    "target_request_id",
    "sent_at_utc",
}


class RequestValidationError(ValueError):
    """Raised when a request violates the coordinator protocol."""


@dataclass
class ReceiverState:
    """Track the test currently registered as active."""

    active_request_id: str | None = None


def default_receiver_log_path() -> Path:
    """Return the receiver JSONL log location."""

    project_root = (
        Path(__file__)
        .resolve()
        .parent
        .parent
    )

    return (
        project_root
        / "logs"
        / "receiver_events.jsonl"
    )


def validate_exact_fields(
    payload: dict[str, Any],
    required_fields: set[str],
    optional_fields: set[str] | None = None,
) -> None:
    """Reject missing fields and fields outside the protocol."""

    optional = optional_fields or set()
    actual_fields = set(payload)

    missing_fields = required_fields - actual_fields

    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise RequestValidationError(
            f"Missing fields: {missing}"
        )

    allowed_fields = required_fields | optional
    unexpected_fields = actual_fields - allowed_fields

    if unexpected_fields:
        unexpected = ", ".join(
            sorted(unexpected_fields)
        )
        raise RequestValidationError(
            f"Unexpected fields: {unexpected}"
        )


def validate_common_fields(
    payload: dict[str, Any],
) -> None:
    """Validate fields shared by start and stop commands."""

    if payload["protocol_version"] != PROTOCOL_VERSION:
        raise RequestValidationError(
            "Unsupported protocol_version"
        )

    request_id = payload["request_id"]

    if (
        not isinstance(request_id, str)
        or not request_id.strip()
    ):
        raise RequestValidationError(
            "request_id must be a non-empty string"
        )

    sent_at_utc = payload["sent_at_utc"]

    if (
        not isinstance(sent_at_utc, str)
        or not sent_at_utc.strip()
    ):
        raise RequestValidationError(
            "sent_at_utc must be a non-empty string"
        )


def _validate_finite_number(
    value: object,
    field_name: str,
    *,
    allow_zero: bool,
) -> float:
    """Validate and return one finite numeric field."""

    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise RequestValidationError(
            f"{field_name} must be numeric"
        )

    numeric = float(value)

    if not math.isfinite(numeric):
        raise RequestValidationError(
            f"{field_name} must be finite"
        )

    if allow_zero:
        if numeric < 0:
            raise RequestValidationError(
                f"{field_name} must not be negative"
            )
    elif numeric <= 0:
        raise RequestValidationError(
            f"{field_name} must be greater than 0"
        )

    return numeric


def validate_start_request(
    payload: dict[str, Any],
) -> None:
    """Validate fields specific to START_TEST."""

    beam_energy = payload["beam_energy_mev"]

    if type(beam_energy) is not int:
        raise RequestValidationError(
            "beam_energy_mev must be an integer"
        )

    if beam_energy not in BEAM_ENERGIES_MEV:
        raise RequestValidationError(
            f"Unsupported beam energy: {beam_energy}"
        )

    mode = payload.get(
        "shielding_mode",
        "preset",
    )

    if mode not in {"preset", "custom"}:
        raise RequestValidationError(
            "shielding_mode must be preset or custom"
        )

    material = payload["shielding_material"]

    if not isinstance(material, str):
        raise RequestValidationError(
            "shielding_material must be a string"
        )

    normalized_material = material.strip()

    if not normalized_material:
        raise RequestValidationError(
            "shielding_material must not be blank"
        )

    thickness = payload["shielding_thickness_mm"]

    if mode == "preset":
        if normalized_material not in SHIELDING_MATERIALS:
            raise RequestValidationError(
                "Unsupported shielding material: "
                f"{normalized_material}"
            )

        if type(thickness) is not int:
            raise RequestValidationError(
                "preset shielding_thickness_mm "
                "must be an integer"
            )

        if thickness not in SHIELDING_THICKNESSES_MM:
            raise RequestValidationError(
                "Unsupported shielding thickness: "
                f"{thickness}"
            )

        if normalized_material == "Bare" and thickness != 0:
            raise RequestValidationError(
                "Bare shielding must use reference 0"
            )

        if normalized_material != "Bare" and thickness == 0:
            raise RequestValidationError(
                "Only Bare shielding may use reference 0"
            )

        reference = payload.get(
            "shielding_reference_mm"
        )

        if (
            reference is not None
            and reference != thickness
        ):
            raise RequestValidationError(
                "preset shielding_reference_mm must "
                "match shielding_thickness_mm"
            )

        actual = payload.get(
            "shielding_actual_thickness_mm",
            thickness,
        )

        _validate_finite_number(
            actual,
            "shielding_actual_thickness_mm",
            allow_zero=normalized_material == "Bare",
        )

    else:
        custom_thickness = _validate_finite_number(
            thickness,
            "shielding_thickness_mm",
            allow_zero=False,
        )

        actual = _validate_finite_number(
            payload.get(
                "shielding_actual_thickness_mm",
                thickness,
            ),
            "shielding_actual_thickness_mm",
            allow_zero=False,
        )

        if not math.isclose(
            custom_thickness,
            actual,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise RequestValidationError(
                "custom thickness fields do not match"
            )

        reference = payload.get(
            "shielding_reference_mm"
        )

        if reference is not None:
            _validate_finite_number(
                reference,
                "shielding_reference_mm",
                allow_zero=True,
            )

    configuration_id = payload.get(
        "shielding_configuration_id"
    )

    if (
        configuration_id is not None
        and not isinstance(configuration_id, str)
    ):
        raise RequestValidationError(
            "shielding_configuration_id must be a string"
        )

    campaign_metadata = payload.get(
        "campaign_metadata"
    )

    if (
        campaign_metadata is not None
        and not isinstance(campaign_metadata, dict)
    ):
        raise RequestValidationError(
            "campaign_metadata must be a JSON object"
        )

    duration_s = payload["duration_s"]

    if isinstance(duration_s, bool) or not isinstance(
        duration_s,
        (int, float),
    ):
        raise RequestValidationError(
            "duration_s must be a positive number"
        )

    if not 0 < duration_s <= MAX_DURATION_S:
        raise RequestValidationError(
            "duration_s must be greater than 0 and at most "
            f"{MAX_DURATION_S}"
        )


def validate_stop_request(
    payload: dict[str, Any],
) -> None:
    """Validate fields specific to STOP_TEST."""

    target_request_id = payload["target_request_id"]

    if (
        not isinstance(target_request_id, str)
        or not target_request_id.strip()
    ):
        raise RequestValidationError(
            "target_request_id must be a non-empty string"
        )


def validate_request_payload(
    payload: Any,
) -> dict[str, Any]:
    """Validate one START_TEST or STOP_TEST request."""

    if not isinstance(payload, dict):
        raise RequestValidationError(
            "Request must be a JSON object"
        )

    if "command" not in payload:
        raise RequestValidationError(
            "Missing fields: command"
        )

    command = payload["command"]

    if command == START_TEST_COMMAND:
        validate_exact_fields(
            payload,
            START_REQUIRED_FIELDS,
            START_OPTIONAL_FIELDS,
        )
        validate_common_fields(payload)
        validate_start_request(payload)

    elif command == STOP_TEST_COMMAND:
        validate_exact_fields(
            payload,
            STOP_REQUIRED_FIELDS,
        )
        validate_common_fields(payload)
        validate_stop_request(payload)

    else:
        raise RequestValidationError(
            f"Unsupported command: {command}"
        )

    return payload


def apply_request_to_state(
    payload: dict[str, Any],
    state: ReceiverState,
) -> str:
    """Validate and apply one receiver state transition."""

    command = payload["command"]

    if command == START_TEST_COMMAND:
        if state.active_request_id is not None:
            raise RequestValidationError(
                "A test is already active: "
                f"{state.active_request_id}"
            )

        state.active_request_id = payload["request_id"]

        return "TEST_REQUEST_ACCEPTED"

    if command == STOP_TEST_COMMAND:
        if state.active_request_id is None:
            raise RequestValidationError(
                "No active test is registered"
            )

        target_request_id = payload[
            "target_request_id"
        ]

        if target_request_id != state.active_request_id:
            raise RequestValidationError(
                "target_request_id does not match "
                "the active test"
            )

        state.active_request_id = None

        return "TEST_STOP_REQUEST_ACCEPTED"

    raise RequestValidationError(
        f"Unsupported command: {command}"
    )


def create_response(
    request_id: str,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Create a structured receiver acknowledgment."""

    response: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "status": status,
        "request_id": request_id,
        "received_at_utc": utc_timestamp(),
    }

    if error is not None:
        response["error"] = error

    return response


def encode_response(
    response: dict[str, Any],
) -> bytes:
    """Encode one newline-delimited JSON response."""

    return (
        json.dumps(
            response,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def save_accepted_event(
    event_logger: EventLogger,
    event_name: str,
    source: str,
    request: dict[str, Any],
    state: ReceiverState,
    previous_active_request_id: str | None,
) -> None:
    """Save an accepted event or restore the previous state."""

    try:
        event_logger.append(
            event_name,
            source=source,
            request=request,
            active_request_id=(
                state.active_request_id
            ),
        )

    except (
        OSError,
        TypeError,
        ValueError,
    ) as error:
        state.active_request_id = (
            previous_active_request_id
        )

        raise RequestValidationError(
            "Persistent receiver logging failed: "
            f"{error}"
        ) from error


def save_rejected_event(
    event_logger: EventLogger,
    source: str,
    request_id: str,
    state: ReceiverState,
    error_message: str,
) -> None:
    """Attempt to save a rejected receiver event."""

    try:
        event_logger.append(
            "TEST_REQUEST_REJECTED",
            source=source,
            request_id=request_id,
            active_request_id=(
                state.active_request_id
            ),
            error=error_message,
        )

    except (
        OSError,
        TypeError,
        ValueError,
    ) as logging_error:
        print(
            json.dumps(
                {
                    "event": (
                        "RECEIVER_LOGGING_FAILED"
                    ),
                    "source": source,
                    "error": str(logging_error),
                },
                indent=2,
            ),
            flush=True,
        )


def handle_connection(
    connection: socket.socket,
    address: tuple[str, int],
    timeout_seconds: float,
    state: ReceiverState,
    event_logger: EventLogger,
) -> None:
    """Receive, validate, save and acknowledge one command."""

    connection.settimeout(timeout_seconds)

    source = f"{address[0]}:{address[1]}"
    request_id = "unknown"

    try:
        with connection.makefile("rwb") as stream:
            message = stream.readline(
                MAX_MESSAGE_BYTES + 1
            )

            if not message:
                raise RequestValidationError(
                    "Connection closed before "
                    "a request was received"
                )

            if len(message) > MAX_MESSAGE_BYTES:
                raise RequestValidationError(
                    "Request exceeds maximum message size"
                )

            try:
                message_text = message.decode(
                    "utf-8"
                )

            except UnicodeDecodeError as error:
                raise RequestValidationError(
                    "Request must use UTF-8 encoding"
                ) from error

            try:
                payload = json.loads(message_text)

            except json.JSONDecodeError as error:
                raise RequestValidationError(
                    "Request contains invalid JSON"
                ) from error

            if isinstance(payload, dict):
                candidate_id = payload.get(
                    "request_id"
                )

                if (
                    isinstance(candidate_id, str)
                    and candidate_id
                ):
                    request_id = candidate_id

            validated = validate_request_payload(
                payload
            )

            request_id = validated["request_id"]

            previous_active_request_id = (
                state.active_request_id
            )

            event_name = apply_request_to_state(
                validated,
                state,
            )

            save_accepted_event(
                event_logger=event_logger,
                event_name=event_name,
                source=source,
                request=validated,
                state=state,
                previous_active_request_id=(
                    previous_active_request_id
                ),
            )

            response = create_response(
                request_id=request_id,
                status="ACCEPTED",
            )

            stream.write(
                encode_response(response)
            )
            stream.flush()

            print(
                json.dumps(
                    {
                        "event": event_name,
                        "source": source,
                        "active_request_id": (
                            state.active_request_id
                        ),
                        "request": validated,
                    },
                    indent=2,
                ),
                flush=True,
            )

    except (
        RequestValidationError,
        TimeoutError,
        socket.timeout,
    ) as error:
        error_message = str(error)

        response = create_response(
            request_id=request_id,
            status="REJECTED",
            error=error_message,
        )

        save_rejected_event(
            event_logger=event_logger,
            source=source,
            request_id=request_id,
            state=state,
            error_message=error_message,
        )

        print(
            json.dumps(
                {
                    "event": "TEST_REQUEST_REJECTED",
                    "source": source,
                    "active_request_id": (
                        state.active_request_id
                    ),
                    "error": error_message,
                },
                indent=2,
            ),
            flush=True,
        )

        try:
            connection.sendall(
                encode_response(response)
            )

        except OSError:
            pass


def serve(
    host: str,
    port: int,
    timeout_seconds: float,
    event_logger: EventLogger | None = None,
) -> None:
    """Run the TCP receiver until interrupted."""

    logger = (
        event_logger
        or EventLogger(
            default_receiver_log_path()
        )
    )

    state = ReceiverState()

    logger.append(
        "RECEIVER_STARTED",
        host=host,
        port=port,
        timeout_seconds=timeout_seconds,
        active_request_id=(
            state.active_request_id
        ),
    )

    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as server:
        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        server.bind((host, port))
        server.listen(5)

        print(
            json.dumps(
                {
                    "event": "RECEIVER_STARTED",
                    "host": host,
                    "port": port,
                    "timeout_seconds": (
                        timeout_seconds
                    ),
                    "active_request_id": (
                        state.active_request_id
                    ),
                }
            ),
            flush=True,
        )

        while True:
            connection, address = server.accept()

            with connection:
                handle_connection(
                    connection=connection,
                    address=address,
                    timeout_seconds=timeout_seconds,
                    state=state,
                    event_logger=logger,
                )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Receive Jetson proton-test "
            "control requests."
        )
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Address on which to listen.",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=6000,
        help="TCP port on which to listen.",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Client read timeout in seconds.",
    )

    return parser.parse_args()


def main() -> None:
    """Run the receiver from the command line."""

    args = parse_args()

    logger = EventLogger(
        default_receiver_log_path()
    )

    try:
        serve(
            host=args.host,
            port=args.port,
            timeout_seconds=args.timeout,
            event_logger=logger,
        )

    except KeyboardInterrupt:
        logger.append(
            "RECEIVER_STOPPED",
            reason="keyboard_interrupt",
        )

        print("\nReceiver stopped.")


if __name__ == "__main__":
    main()