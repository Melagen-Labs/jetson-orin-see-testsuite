#!/usr/bin/env python3
"""mem_check.py -- CPU/system RAM pattern tester for Jetson SEE testing (channel 2a).

Purpose-built, schema-v1-native detector for the CPU-attached path into the
Orin Nano's shared LPDDR5. It holds a large buffer full of a known bit pattern
and repeatedly reads it back; any byte that no longer matches is a candidate
single-event upset, logged with its address and the expected/actual/xor bytes.

Design (mirrors the moving-inversions method NASA SMRT uses, but emits this
repo's frozen event schema directly rather than wrapping SMRT):
  * Allocate one contiguous numpy uint8 buffer of `buffer_mb` megabytes.
  * For each pattern in `patterns` (0x00, 0xFF, 0x55, 0xAA -- all-zeros,
    all-ones, and both checkerboards, so a bit stuck/flipped in EITHER state is
    caught), fill the buffer, then do `hold_sweeps` read-back verification passes
    with a short `sweep_sleep_s` dwell between them. The dwell is exposure time:
    a flip that lands mid-hold is caught on the next sweep.
  * On a mismatch, emit one `memory` anomaly record per flipped byte (capped per
    sweep by `max_report`) and scrub the byte back to the pattern so the same
    upset is not re-counted every subsequent sweep.
  * Heartbeat file each sweep (liveness); periodic `checkpoint` info record every
    `checkpoint_sweeps`; `start`/`stop` records bracket the run. SIGTERM/SIGINT
    exit cleanly (exit 2 if any upset was seen, like cuda_particles).

All JSONL records go through shared/event_log.py so they cannot drift from the
frozen schema v1 (docs/EVENT_SCHEMA.md).
"""

import argparse
import json
import os
import signal
import sys
import time

import numpy as np


# --- locate the shared schema-v1 helper (repo: ../../shared; DUT: same dir) ---
def _load_event_log():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.join(here, "..", "..", "shared")):
        if os.path.exists(os.path.join(cand, "event_log.py")):
            sys.path.insert(0, cand)
            break
    import event_log  # noqa: E402
    return event_log


EL = _load_event_log()

g_stop = False


def _handle_signal(signum, frame):
    global g_stop
    g_stop = True


DEFAULTS = {
    "buffer_mb": 2048,                       # buffer size; leave RAM headroom
    "patterns": ["0x00", "0xFF", "0x55", "0xAA"],
    "hold_sweeps": 8,                        # verify passes before repainting
    "sweep_sleep_s": 0.05,                   # dwell between sweeps (exposure)
    "checkpoint_sweeps": 100,                # emit an "ok" checkpoint this often
    "max_report": 64,                        # cap anomaly records per sweep
    "iterations": 0,                         # 0 = run forever (counts sweeps)
    "log_dir": "./logs",
    "heartbeat_path": "./logs/heartbeat.txt",
    "run_id": "unset",
    "jetson_id": "orin-nano-01",
    "beam_energy": "unset",
    "fluence_source": "unset",
    "shield_config": "unset",
    # Self-test hook: at sweep N flip one bit at byte `fault_inject_addr` to
    # prove the detector fires. 0 = disabled (normal operation).
    "fault_inject_sweep": 0,
    "fault_inject_addr": 0,
}


def load_config(path):
    cfg = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        sys.stderr.write("[mem_check] config '%s' not found; using defaults\n" % path)
    return cfg


def write_heartbeat(path, sweep, upsets, pattern):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"sweep":%d,"upsets":%d,"pattern":"%s","unixtime":%d}\n'
                    % (sweep, upsets, pattern, int(time.time())))
    except OSError:
        pass


def meta_of(cfg):
    return {k: cfg[k] for k in ("beam_energy", "fluence_source", "shield_config")}


def emit(fp, cfg, event, status, payload):
    if fp is None:
        return
    rec = EL.envelope(cfg["run_id"], cfg["jetson_id"], "memory", event, status,
                      meta=meta_of(cfg))
    rec.update(payload)
    EL.emit(fp, rec)


def main():
    global g_stop
    ap = argparse.ArgumentParser(description="CPU/system RAM SEE pattern tester (channel 2a)")
    ap.add_argument("--config", default="config/mem_check.json")
    ap.add_argument("--self-test", action="store_true",
                    help="small buffer + injected bit flip to prove detection")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.self_test:
        cfg["buffer_mb"] = min(cfg["buffer_mb"], 64)
        cfg["iterations"] = 6
        cfg["hold_sweeps"] = 3
        cfg["fault_inject_sweep"] = 2
        cfg["fault_inject_addr"] = 12345

    nbytes = int(cfg["buffer_mb"]) * 1024 * 1024
    patterns = [int(p, 16) if isinstance(p, str) else int(p) for p in cfg["patterns"]]
    K_hold = max(1, int(cfg["hold_sweeps"]))
    max_report = max(1, int(cfg["max_report"]))
    ckpt = max(1, int(cfg["checkpoint_sweeps"]))
    limit = int(cfg["iterations"])

    os.makedirs(cfg["log_dir"], exist_ok=True)
    log_path = os.path.join(cfg["log_dir"], "mem_check.jsonl")
    fp = open(log_path, "a", encoding="utf-8")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Allocate the buffer up front so an OOM shows immediately, not mid-run.
    buf = np.empty(nbytes, dtype=np.uint8)

    emit(fp, cfg, "start", "info", {
        "test": "moving_inversions",
        "buffer_mb": cfg["buffer_mb"],
        "patterns": [("0x%02x" % p) for p in patterns],
        "hold_sweeps": K_hold,
        "self_test": bool(args.self_test),
    })

    sweeps = 0
    upsets = 0
    corruption_seen = False

    while not g_stop:
        for val in patterns:
            buf[:] = np.uint8(val)             # paint the pattern
            for _ in range(K_hold):
                if g_stop:
                    break

                # Optional detection self-test: flip one bit at the target sweep.
                if cfg["fault_inject_sweep"] and (sweeps + 1) == int(cfg["fault_inject_sweep"]):
                    addr = int(cfg["fault_inject_addr"]) % nbytes
                    buf[addr] ^= np.uint8(0x01)

                # Read-back verify: indices whose byte no longer equals `val`.
                mism = np.where(buf != np.uint8(val))[0]
                if mism.size:
                    corruption_seen = True
                    for idx in mism[:max_report]:
                        idx = int(idx)
                        actual = int(buf[idx])
                        emit(fp, cfg, "mem_upset", "anomaly", {
                            "test": "moving_inversions",
                            "address": ("0x%x" % idx),
                            "pattern": ("0x%02x" % val),
                            "expected": ("0x%02x" % val),
                            "actual": ("0x%02x" % actual),
                            "xor": ("0x%02x" % (val ^ actual)),
                            "sweep": sweeps + 1,
                        })
                        buf[idx] = np.uint8(val)   # scrub so it is counted once
                        upsets += 1
                    if mism.size > max_report:
                        emit(fp, cfg, "mem_upset_overflow", "anomaly", {
                            "test": "moving_inversions",
                            "pattern": ("0x%02x" % val),
                            "reported": max_report,
                            "total_mismatches": int(mism.size),
                            "sweep": sweeps + 1,
                        })

                sweeps += 1
                write_heartbeat(cfg["heartbeat_path"], sweeps, upsets, "0x%02x" % val)

                if sweeps % ckpt == 0:
                    emit(fp, cfg, "checkpoint", "ok", {
                        "test": "moving_inversions",
                        "sweep": sweeps, "upsets": upsets,
                        "pattern": ("0x%02x" % val),
                    })

                if limit and sweeps >= limit:
                    g_stop = True
                    break
                time.sleep(float(cfg["sweep_sleep_s"]))
            if g_stop:
                break

    emit(fp, cfg, "stop", "info", {
        "test": "moving_inversions",
        "sweeps": sweeps, "upsets": upsets,
        "corruption_seen": corruption_seen,
    })
    fp.close()
    return 2 if corruption_seen else 0


if __name__ == "__main__":
    sys.exit(main())
