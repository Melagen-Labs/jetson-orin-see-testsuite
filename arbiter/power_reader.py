#!/usr/bin/env python3
"""power_reader.py -- arbiter-side current/power stream reader (channel 5).

**Status: awaiting retarget.** This was written against a dedicated power-monitor
firmware board on USB-serial. That approach is retired -- the campaign adds no new
hardware, so current sensing is the DUT-side INA3221 collector instead, whose
records reach the arbiter through ``pull_logs.sh`` rather than a serial port. The
parser and status-transition logic below are still the intended ingest path; only
the transport changes. Keep the wire format below when wiring the collector up and
``arbiter_main.py``'s CANDIDATE_SEL escalation keeps working unchanged.

Note what is NOT replaced: the retired firmware also provided a **latching power
cutoff**. Software detection cannot de-power a latched part, so that protection
does not currently exist in any form. The ``send_recovery_command()`` helper that
unlatched such a trip was removed on 2026-08-03 -- there is no trip to unlatch.

Importable module. Reads line-delimited JSON and invokes callbacks on every sample
and specifically on every status transition (the ``NOMINAL -> ABNORMAL ->
TRIPPED`` changes the arbiter treats as a candidate SEL). Handles the link
dropping by reconnecting.

Wire format -- one complete JSON object per line, newline-terminated::

    {"ts_fw": <int ms since source boot>, "current_mA": <number>,
     "status": "NOMINAL"|"ABNORMAL"|"TRIPPED"}

Status semantics:
  * ``NOMINAL``  -- current within bounds.
  * ``ABNORMAL`` -- over the configured limit, no cutoff engaged; a candidate
    "persistent abnormal current".
  * ``TRIPPED``  -- cutoff engaged and latched (the firmware-era meaning). With a
    software-only monitor this degrades to "limit exceeded", with no cutoff.

The upper-current limit is deliberately **not** hardcoded here. It is set from a
measured no-SEE baseline and approved before use; until then detection stays
disabled rather than shipping a provisional number.

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
