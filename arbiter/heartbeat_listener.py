#!/usr/bin/env python3
"""Arbiter-side UDP heartbeat listener with durable JSONL logging."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional


DEFAULT_PORT = 5555
DEFAULT_TIMEOUT = 3.0
DEFAULT_LOG_FILE = os.path.join(
    "arbiter_logs",
    "heartbeat",
    "heartbeat_log.jsonl",
)


def utc_now() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class JsonlEventLogger:
    """Append heartbeat events to a JSON Lines file and force them to disk."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, separators=(",", ":"), sort_keys=True)

        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _emit(
    callback: Optional[Callable[[dict[str, Any]], None]],
    event: dict[str, Any],
) -> None:
    if callback is not None:
        callback(event)


def _decode_heartbeat(data: bytes) -> dict[str, Any]:
    """Decode and validate one Jetson heartbeat datagram."""
    try:
        beat = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("packet is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("packet is not valid JSON") from exc

    if not isinstance(beat, dict):
        raise ValueError("heartbeat must be a JSON object")

    boot_id = beat.get("boot_id")
    sequence = beat.get("seq")
    dut_timestamp = beat.get("ts")

    if not isinstance(boot_id, str) or not boot_id.strip():
        raise ValueError("boot_id must be a non-empty string")

    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise ValueError("seq must be an integer")

    if sequence < 0:
        raise ValueError("seq must be zero or greater")

    if isinstance(dut_timestamp, bool) or not isinstance(
        dut_timestamp,
        (int, float),
    ):
        raise ValueError("ts must be numeric")

    if not math.isfinite(float(dut_timestamp)):
        raise ValueError("ts must be finite")

    return {
        "boot_id": boot_id,
        "seq": sequence,
        "ts": float(dut_timestamp),
    }


def run_heartbeat_listener(
    port: int = DEFAULT_PORT,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    bind_addr: str = "0.0.0.0",
    stop_event: Any = None,
    run_id: str = "unassigned",
    jetson_id: str = "unknown",
) -> None:
    """Listen for Jetson heartbeats and emit heartbeat state events."""

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_addr, port))
    sock.settimeout(timeout)

    last_seen: Optional[float] = None
    last_boot_id: Optional[str] = None
    last_sequence: Optional[int] = None
    lost_at: Optional[float] = None
    alive = False

    try:
        while stop_event is None or not stop_event.is_set():
            try:
                data, address = sock.recvfrom(4096)

            except socket.timeout:
                if alive and last_seen is not None:
                    detected_at = time.time()
                    alive = False
                    lost_at = detected_at

                    _emit(
                        on_event,
                        {
                            "event": "HEARTBEAT_LOST",
                            "ts": detected_at,
                            "recorded_at_utc": utc_now(),
                            "run_id": run_id,
                            "jetson_id": jetson_id,
                            "last_heartbeat_ts": last_seen,
                            "gap_since_last_heartbeat_s": (
                                detected_at - last_seen
                            ),
                            "boot_id": last_boot_id,
                            "last_sequence": last_sequence,
                        },
                    )
                continue

            except OSError:
                break

            received_at = time.time()
            source = f"{address[0]}:{address[1]}"

            try:
                beat = _decode_heartbeat(data)
            except ValueError as exc:
                _emit(
                    on_event,
                    {
                        "event": "HEARTBEAT_INVALID",
                        "ts": received_at,
                        "recorded_at_utc": utc_now(),
                        "run_id": run_id,
                        "jetson_id": jetson_id,
                        "src": source,
                        "error": str(exc),
                        "raw_hex": data[:64].hex(),
                    },
                )
                continue

            boot_id_changed = (
                last_boot_id is not None
                and beat["boot_id"] != last_boot_id
            )

            if not alive and last_seen is not None:
                communication_unavailable_seconds = received_at - last_seen
                outage_after_detection_seconds = (
                    received_at - lost_at
                    if lost_at is not None
                    else None
                )

                _emit(
                    on_event,
                    {
                        "event": "HEARTBEAT_RESUMED",
                        "ts": received_at,
                        "recorded_at_utc": utc_now(),
                        "run_id": run_id,
                        "jetson_id": jetson_id,
                        "src": source,
                        "communication_unavailable_seconds": (
                            communication_unavailable_seconds
                        ),
                        "outage_after_detection_seconds": (
                            outage_after_detection_seconds
                        ),
                        "gap_since_last_heartbeat_s": (
                            received_at - last_seen
                        ),
                        "previous_boot_id": last_boot_id,
                        "boot_id": beat["boot_id"],
                        "boot_id_changed": boot_id_changed,
                        "previous_sequence": last_sequence,
                        "sequence": beat["seq"],
                    },
                )

            alive = True
            lost_at = None

            sequence_gap = None
            if (
                last_sequence is not None
                and not boot_id_changed
                and beat["seq"] > last_sequence
            ):
                sequence_gap = max(
                    beat["seq"] - last_sequence - 1,
                    0,
                )

            _emit(
                on_event,
                {
                    "event": "HEARTBEAT",
                    "ts": received_at,
                    "recorded_at_utc": utc_now(),
                    "run_id": run_id,
                    "jetson_id": jetson_id,
                    "src": source,
                    "boot_id": beat["boot_id"],
                    "seq": beat["seq"],
                    "dut_ts": beat["ts"],
                    "boot_id_changed": boot_id_changed,
                    "sequence_gap": sequence_gap,
                },
            )

            last_seen = received_at
            last_boot_id = beat["boot_id"]
            last_sequence = beat["seq"]

    finally:
        sock.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Receive Jetson UDP heartbeats and store them on the arbiter."
        )
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="UDP heartbeat port (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Seconds before declaring heartbeat loss (default: %(default)s)",
    )
    parser.add_argument(
        "--bind-address",
        default="0.0.0.0",
        help="Local interface to bind (default: %(default)s)",
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_LOG_FILE,
        help="Arbiter JSONL log path (default: %(default)s)",
    )
    parser.add_argument(
        "--run-id",
        default="unassigned",
        help="Current test run identifier",
    )
    parser.add_argument(
        "--jetson-id",
        default="unknown",
        help="Jetson board identifier",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logger = JsonlEventLogger(args.log_file)

    def record(event: dict[str, Any]) -> None:
        logger.write(event)
        print(json.dumps(event), flush=True)

    print(
        json.dumps(
            {
                "event": "HEARTBEAT_LISTENER_STARTED",
                "port": args.port,
                "timeout_s": args.timeout,
                "run_id": args.run_id,
                "jetson_id": args.jetson_id,
                "log_file": logger.path,
            }
        ),
        flush=True,
    )

    try:
        run_heartbeat_listener(
            port=args.port,
            on_event=record,
            timeout=args.timeout,
            bind_addr=args.bind_address,
            run_id=args.run_id,
            jetson_id=args.jetson_id,
        )
    except KeyboardInterrupt:
        print(
            json.dumps({"event": "HEARTBEAT_LISTENER_STOPPED"}),
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
