#!/usr/bin/env python3
"""heartbeat_listener.py -- arbiter-side UDP heartbeat listener (channel 3b).

Importable module. ``run_heartbeat_listener()`` binds a UDP socket and invokes a
callback for every heartbeat datagram and for loss/resume transitions, so
``arbiter_main.py`` can run it on a thread and funnel events into the correlator.
It can also be run standalone for a quick check.

Emitted event dicts (passed to ``on_event``)::

    {"event": "HEARTBEAT",         "ts": <float>, "src": "<ip:port>", "beat": {...}}
    {"event": "HEARTBEAT_LOST",    "ts": <float>, "gap_s": <float>}
    {"event": "HEARTBEAT_RESUMED", "ts": <float>, "beat": {...}}

where ``beat`` is the decoded DUT datagram
``{"boot_id": <str>, "seq": <int>, "ts": <float>}``.
"""
from __future__ import annotations

import json
import socket
import time


def _emit(callback, event):
    if callback is not None:
        callback(event)


def run_heartbeat_listener(port=5555, on_event=None, timeout=3.0,
                           bind_addr="0.0.0.0", stop_event=None):
    """Listen for DUT heartbeats until ``stop_event`` is set (or forever).

    Args:
        port: UDP port to bind.
        on_event: callable(event_dict) invoked per datagram and per transition.
        timeout: seconds with no datagram before declaring HEARTBEAT_LOST.
        bind_addr: interface to bind (default all interfaces).
        stop_event: optional ``threading.Event``; when set, the loop exits.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_addr, port))
    sock.settimeout(timeout)
    last_seen = time.time()
    alive = True
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                # No datagram within `timeout`: declare loss once per outage.
                if alive:
                    alive = False
                    _emit(on_event, {"event": "HEARTBEAT_LOST",
                                     "ts": time.time(),
                                     "gap_s": time.time() - last_seen})
                continue
            except OSError:
                break
            last_seen = time.time()
            try:
                beat = json.loads(data.decode())
            except (ValueError, UnicodeDecodeError):
                beat = {"raw": data[:64].hex()}
            if not alive:
                alive = True
                _emit(on_event, {"event": "HEARTBEAT_RESUMED",
                                 "ts": last_seen, "beat": beat})
            _emit(on_event, {"event": "HEARTBEAT", "ts": last_seen,
                             "src": f"{addr[0]}:{addr[1]}", "beat": beat})
    finally:
        sock.close()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Standalone heartbeat listener.")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    run_heartbeat_listener(
        args.port,
        on_event=lambda e: print(json.dumps(e), flush=True),
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
