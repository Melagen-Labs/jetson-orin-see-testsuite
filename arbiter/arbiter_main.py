#!/usr/bin/env python3
"""arbiter_main.py -- arbiter correlator (build plan sections 0 and 5).

The arbiter is the system of record: it keeps running through a DUT hang,
reboot, or latchup. This process:

  * listens for the DUT's UDP heartbeat (``heartbeat_listener``) on a thread,
  * reads the power firmware's serial stream (``power_reader``) on a thread,
  * on a timer, shells out to ``pull_logs.sh`` to rsync the DUT's ``memory/``,
    ``compute/``, and ``boot_state/`` logs (plus pstore) to the arbiter,
  * appends every event -- heartbeat loss/resume, power status changes, and
    log-pull summaries -- into one timestamped JSONL correlator file keyed by
    the arbiter's wall clock, so post-test you can line up "heartbeat lost at T"
    against "current TRIPPED at T" against "reboot logged at T+2s".

High-rate power samples go to ``power/power_log.jsonl``; only the rarer,
important events (status transitions, heartbeat loss, pulls) go to the
correlator so it stays readable.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

from heartbeat_listener import run_heartbeat_listener
from power_reader import run_power_reader


class JsonlSink:
    """Thread-safe append-only JSONL writer."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def append(self, record, sync=True):
        line = json.dumps(record)
        with self._lock:
            with open(self._path, "a") as handle:
                handle.write(line + "\n")
                if sync:
                    handle.flush()
                    os.fsync(handle.fileno())
        return line


def make_correlator(sink):
    """Return a ``record(source, event)`` that stamps events with receipt time."""
    def record(source, event):
        wrapped = {"recv_ts": time.time(), "source": source, "event": event}
        sink.append(wrapped)
        # Echo to stdout so the operator watching the arbiter sees events live.
        print(json.dumps(wrapped), flush=True)
    return record


def heartbeat_thread(args, correlate, stop):
    try:
        run_heartbeat_listener(
            port=args.heartbeat_port,
            on_event=lambda ev: correlate("heartbeat", ev),
            timeout=args.heartbeat_timeout,
            stop_event=stop,
        )
    except Exception as exc:  # keep the arbiter alive if one channel dies
        correlate("heartbeat", {"event": "LISTENER_DOWN", "error": str(exc)})


def on_power_status_change(ev, correlate):
    """A TRIPPED transition is a candidate SEL; flag it for correlation."""
    correlate("power", ev)
    if ev.get("to") == "TRIPPED":
        correlate("power", {
            "event": "CANDIDATE_SEL",
            "note": "cross-reference same-second heartbeat and compute/memory "
                    "logs: heartbeat also lost => strong SEL/SEFI; heartbeat "
                    "present => likely less severe abnormal-current event",
            "sample": ev.get("sample"),
        })


def power_thread(args, correlate, power_log, stop):
    if not args.power_serial_port:
        correlate("power", {"event": "POWER_DISABLED",
                            "reason": "no --power-serial-port given"})
        return
    try:
        run_power_reader(
            port=args.power_serial_port,
            baud=args.power_baud,
            # every sample -> high-rate power log (no fsync per sample)
            on_sample=lambda s: power_log.append(s, sync=False),
            # status transitions -> correlator (with SEL cross-reference)
            on_status_change=lambda ev: on_power_status_change(ev, correlate),
            stop_event=stop,
            reconnect_delay=args.power_reconnect_delay,
        )
    except Exception as exc:
        correlate("power", {"event": "POWER_READER_DOWN", "error": str(exc)})


def pull_thread(args, correlate, stop):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pull_logs.sh")
    if not args.dut_host:
        correlate("pull_logs", {"event": "PULL_DISABLED",
                                "reason": "no --dut-host given"})
        return
    env = dict(os.environ)
    env.update({
        "DUT_HOST": args.dut_host,
        "DUT_USER": args.dut_user,
        "DUT_LOG_DIR": args.dut_log_dir,
        "LOCAL_LOG_DIR": args.local_log_dir,
    })
    if args.ssh_key:
        env["SSH_KEY"] = args.ssh_key
    while not stop.is_set():
        try:
            result = subprocess.run(
                ["bash", script],
                env=env, capture_output=True, text=True,
                timeout=args.pull_timeout,
            )
            correlate("pull_logs", {
                "event": "PULL",
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-500:],
                "stderr_tail": result.stderr[-500:],
            })
        except subprocess.TimeoutExpired:
            correlate("pull_logs", {"event": "PULL_TIMEOUT",
                                    "timeout_s": args.pull_timeout})
        except Exception as exc:
            correlate("pull_logs", {"event": "PULL_ERROR", "error": str(exc)})
        stop.wait(args.pull_interval)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Arbiter correlator for the Jetson Orin Nano SEE test.")
    parser.add_argument("--dut-host", default=None,
                        help="DUT hostname/IP for log pulls. If unset, pulling is disabled.")
    parser.add_argument("--dut-user", default="radpull",
                        help="Low-privilege SSH user on the DUT (default: %(default)s)")
    parser.add_argument("--dut-log-dir", default="/var/log/radtest",
                        help="DUT log root to pull from (default: %(default)s)")
    parser.add_argument("--local-log-dir", default="./arbiter_logs",
                        help="Arbiter local log tree (default: %(default)s)")
    parser.add_argument("--ssh-key", default=None,
                        help="SSH private key for the pull (default: pull_logs.sh's own default)")
    parser.add_argument("--power-serial-port", default=None,
                        help="Serial device for the power firmware (e.g. /dev/ttyUSB0). "
                             "If unset, the power channel is disabled.")
    parser.add_argument("--power-baud", type=int, default=115200,
                        help="Power serial baud (default: %(default)s)")
    parser.add_argument("--power-reconnect-delay", type=float, default=2.0,
                        help="Seconds between serial reconnect attempts (default: %(default)s)")
    parser.add_argument("--heartbeat-port", type=int, default=5555,
                        help="UDP port to receive heartbeats (default: %(default)s)")
    parser.add_argument("--heartbeat-timeout", type=float, default=3.0,
                        help="Seconds before declaring HEARTBEAT_LOST (default: %(default)s)")
    parser.add_argument("--pull-interval", type=float, default=30.0,
                        help="Seconds between rsync pulls (default: %(default)s)")
    parser.add_argument("--pull-timeout", type=float, default=120.0,
                        help="Max seconds for one pull before giving up (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    correlator_sink = JsonlSink(os.path.join(args.local_log_dir, "correlator.jsonl"))
    power_log = JsonlSink(os.path.join(args.local_log_dir, "power", "power_log.jsonl"))
    correlate = make_correlator(correlator_sink)

    stop = threading.Event()
    correlate("arbiter", {"event": "START", "args": vars(args)})

    threads = [
        threading.Thread(target=heartbeat_thread, args=(args, correlate, stop),
                         daemon=True, name="heartbeat"),
        threading.Thread(target=power_thread, args=(args, correlate, power_log, stop),
                         daemon=True, name="power"),
        threading.Thread(target=pull_thread, args=(args, correlate, stop),
                         daemon=True, name="pull"),
    ]
    for thread in threads:
        thread.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        correlate("arbiter", {"event": "STOP"})
        for thread in threads:
            thread.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
