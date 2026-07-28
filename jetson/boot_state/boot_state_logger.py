#!/usr/bin/env python3
"""boot_state_logger.py -- boot-state logging (channel 4).

Records evidence of autonomous reboots and preceding OS failures on the DUT.
Runs in two modes:

* ``--mode boot``  : write a single boot-event line and exit. Wire this to a
  ``Type=oneshot`` systemd unit ordered late in boot, so every power-on/reboot
  appends exactly one record to ``boot_log.jsonl``. A new boot_id appearing here
  that the arbiter did not command is an autonomous reboot.
* ``--mode loop``  : append the current uptime to ``uptime_log.jsonl`` every
  ``--interval`` seconds, forever. A frozen uptime series followed by a reset is
  another view of a reboot; the last logged uptime bounds when the DUT died.

The real "why did it reboot" evidence is the kernel's pstore/ramoops dump under
``/sys/fs/pstore/`` (see docs/PSTORE_SETUP.md); this logger provides the boot_id
and uptime timeline the arbiter correlates that dump against.

Durability matters: each record is appended with its own ``open()``, then
flushed and ``fsync``-ed, so the line is on stable storage before a subsequent
hang/latchup can lose it. We never hold the file handle open across the loop.

Log schemas::

    boot_log.jsonl   : {"event": "boot", "boot_id": <str>, "ts": <float epoch>}
    uptime_log.jsonl : {"boot_id": <str>, "uptime": <float|null>, "ts": <float epoch>}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

DEFAULT_LOG_DIR = "/var/log/radtest/boot_state"
DEFAULT_INTERVAL = 5.0
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
UPTIME_PATH = "/proc/uptime"


def read_boot_id() -> str:
    """Return the kernel boot_id, or 'unknown' if /proc is unavailable."""
    try:
        with open(BOOT_ID_PATH) as handle:
            return handle.read().strip()
    except OSError:
        return "unknown"


def read_uptime():
    """Return seconds of uptime from /proc/uptime, or None off-Linux."""
    try:
        with open(UPTIME_PATH) as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def append_jsonl(path: str, record: dict) -> None:
    """Append one JSON object as a line, fsync-ed for crash durability.

    Opened and closed per call on purpose so a hang cannot strand buffered data.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def log_boot_event(log_dir: str, boot_id: str = None) -> dict:
    """Write the one-shot boot record and return it."""
    boot_id = boot_id or read_boot_id()
    record = {"event": "boot", "boot_id": boot_id, "ts": time.time()}
    append_jsonl(os.path.join(log_dir, "boot_log.jsonl"), record)
    return record


def run_uptime_loop(log_dir: str, interval: float,
                    boot_id: str = None, max_iterations: int = None) -> None:
    """Append the uptime series until interrupted.

    ``max_iterations`` bounds the loop for testing; ``None`` runs forever.
    """
    boot_id = boot_id or read_boot_id()
    path = os.path.join(log_dir, "uptime_log.jsonl")
    count = 0
    try:
        while max_iterations is None or count < max_iterations:
            record = {"boot_id": boot_id, "uptime": read_uptime(), "ts": time.time()}
            append_jsonl(path, record)
            count += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Boot-state logger for the Jetson SEE test.")
    parser.add_argument("--mode", choices=("boot", "loop"), default="loop",
                        help="'boot' writes one boot record and exits; "
                             "'loop' appends uptime forever (default: %(default)s)")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR,
                        help="Directory for the JSONL logs (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="Uptime poll interval in loop mode (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode == "boot":
        log_boot_event(args.log_dir)
    else:
        run_uptime_loop(args.log_dir, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
