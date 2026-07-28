#!/usr/bin/env python3
"""heartbeat_sender.py -- DUT-side 1 Hz UDP heartbeat (channel 3b).

Sends a small JSON datagram to the arbiter once per interval so the arbiter can
detect loss of application/OS responsiveness. Each datagram carries the current
boot_id (so a silent reboot is visible as a boot_id change) and a monotonically
increasing sequence number (so dropped datagrams are distinguishable from a true
hang).

This is deliberately minimal and connectionless: UDP means a DUT hang, reboot,
or link drop simply stops the datagrams, and the arbiter's listener flags the
gap (see arbiter/heartbeat_listener.py). Transient send failures (e.g. the NIC
briefly going down during a latchup/reboot) are caught and logged rather than
killing the process, so the sender resumes automatically once the link is back.

Wire format (one JSON object per datagram)::

    {"boot_id": <str>, "seq": <int>, "ts": <float epoch>}

Run under systemd (see heartbeat_sender.service) so it restarts across reboots.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time

DEFAULT_ARBITER_IP = "192.168.1.10"
DEFAULT_PORT = 5555
DEFAULT_INTERVAL = 1.0
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


def read_boot_id() -> str:
    """Return this boot's kernel-assigned boot_id, or 'unknown' off-Linux."""
    try:
        with open(BOOT_ID_PATH) as handle:
            return handle.read().strip()
    except OSError:
        return "unknown"


def run(arbiter_ip: str, port: int, interval: float,
        boot_id: str = None, max_iterations: int = None) -> int:
    """Send heartbeat datagrams until interrupted.

    ``max_iterations`` bounds the loop for testing; ``None`` means run forever.
    """
    boot_id = boot_id or read_boot_id()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    sent = 0
    try:
        while max_iterations is None or sent < max_iterations:
            message = json.dumps({
                "boot_id": boot_id,
                "seq": seq,
                "ts": time.time(),
            })
            try:
                sock.sendto(message.encode(), (arbiter_ip, port))
            except OSError as exc:
                # Link may be down mid reboot/latchup. Do not die: the arbiter
                # sees the gap as HEARTBEAT_LOST and we resume when it recovers.
                print(f"heartbeat_sender: send failed (seq={seq}): {exc}",
                      file=sys.stderr, flush=True)
            seq += 1
            sent += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DUT-side 1 Hz UDP heartbeat sender for the Jetson SEE test.")
    parser.add_argument("--arbiter-ip", default=DEFAULT_ARBITER_IP,
                        help="Arbiter IP address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="Arbiter UDP port (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="Seconds between heartbeats (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(args.arbiter_ip, args.port, args.interval)


if __name__ == "__main__":
    sys.exit(main())
