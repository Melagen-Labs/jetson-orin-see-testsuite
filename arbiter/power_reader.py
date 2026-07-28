#!/usr/bin/env python3
"""power_reader.py -- arbiter-side power firmware serial reader (channel 5).

Importable module. Opens the USB-serial link to the EE's power-monitor firmware,
reads line-delimited JSON per ``docs/POWER_FIRMWARE_INTERFACE.md``, and invokes
callbacks on every sample and specifically on every status transition (the
``NOMINAL -> ABNORMAL -> TRIPPED`` changes the arbiter treats as a candidate
SEL). Handles the USB-serial link dropping by reconnecting.

Sample line from firmware::

    {"ts_fw": <int ms>, "current_mA": <number>,
     "status": "NOMINAL"|"ABNORMAL"|"TRIPPED"}

Each sample is augmented with ``ts_recv`` (arbiter receipt epoch) before the
callback. Status-change events::

    {"event": "STATUS_CHANGE", "from": <prev|null>, "to": <status>,
     "ts": <float>, "sample": {...}}
"""
from __future__ import annotations

import json
import time

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover - checked at call time
    serial = None


def _emit(callback, event):
    if callback is not None:
        callback(event)


def _wait_or_stop(stop_event, delay):
    """Sleep up to ``delay``; return True if a stop was requested."""
    if stop_event is None:
        time.sleep(delay)
        return False
    return stop_event.wait(delay)


def parse_line(raw):
    """Decode one serial line into a sample dict, or None if unparseable."""
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode(errors="replace").strip()
    else:
        text = raw.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def run_power_reader(port, baud=115200, on_sample=None, on_status_change=None,
                     stop_event=None, reconnect_delay=2.0, read_timeout=1.0):
    """Read the firmware stream until ``stop_event`` is set (or forever).

    Reconnects automatically if the serial link drops (USB-serial can disappear
    on a re-enumeration). ``on_sample`` fires for every parsed line;
    ``on_status_change`` fires only when the reported ``status`` field changes.
    """
    if serial is None:
        raise RuntimeError("pyserial is required: pip install pyserial")

    last_status = None
    while stop_event is None or not stop_event.is_set():
        try:
            ser = serial.Serial(port, baud, timeout=read_timeout)
        except serial.SerialException as exc:
            _emit(on_sample, {"event": "SERIAL_ERROR", "error": str(exc),
                              "ts": time.time()})
            if _wait_or_stop(stop_event, reconnect_delay):
                break
            continue

        try:
            with ser:
                while stop_event is None or not stop_event.is_set():
                    try:
                        raw = ser.readline()
                    except serial.SerialException as exc:
                        _emit(on_sample, {"event": "SERIAL_ERROR",
                                          "error": str(exc), "ts": time.time()})
                        break  # drop out of the read loop to reconnect
                    if not raw:
                        continue  # read timeout with no data this tick
                    sample = parse_line(raw)
                    if sample is None:
                        continue
                    sample["ts_recv"] = time.time()
                    _emit(on_sample, sample)

                    status = sample.get("status")
                    if status is not None and status != last_status:
                        _emit(on_status_change, {
                            "event": "STATUS_CHANGE",
                            "from": last_status,
                            "to": status,
                            "ts": sample["ts_recv"],
                            "sample": sample,
                        })
                        last_status = status
        except serial.SerialException:
            pass

        if _wait_or_stop(stop_event, reconnect_delay):
            break


def send_recovery_command(port, baud=115200, command=b"R\n"):
    """Send the deliberate, arbiter-issued recovery command to unlatch cutoff.

    Recovery is never automatic (see ``docs/POWER_FIRMWARE_INTERFACE.md``): call
    this only after your team's cool-down/inspection decision. The exact command
    byte(s) are part of the firmware contract; ``b"R\\n"`` is the default here.
    """
    if serial is None:
        raise RuntimeError("pyserial is required: pip install pyserial")
    with serial.Serial(port, baud, timeout=2.0) as ser:
        ser.write(command)
        ser.flush()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Standalone power reader.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args(argv)
    run_power_reader(
        args.port, args.baud,
        on_sample=lambda s: print(json.dumps(s), flush=True),
        on_status_change=lambda e: print("STATUS_CHANGE", json.dumps(e), flush=True),
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
