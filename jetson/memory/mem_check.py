#!/usr/bin/env python3
"""mem_check.py -- RAM pattern tester for Jetson SEE testing (channels 2a/2b).

Purpose-built, schema-v1-native detector for the Orin Nano's shared LPDDR5. It
runs on either memory datapath, selected by config `target`:
  * "cpu" (channel 2a): a numpy uint8 buffer in system RAM -- the CPU-attached
    path into DRAM.
  * "gpu" (channel 2b): a CuPy uint8 buffer in GPU DRAM -- the GPU-attached path.
    On the Orin the DRAM is physically shared, but the GPU path exercises the
    GPU memory controller and L2; the buffer is sized >> L2 so read-back hits
    DRAM, not cache. CuPy is imported lazily so a board without it can still run
    the 2a (CPU) test. See docs/DEPENDENCIES.md for the CuPy install/pins.

It holds a large buffer full of a known bit pattern and repeatedly reads it back;
any byte that no longer matches is a candidate single-event upset, logged with
its address and the expected/actual/xor bytes.

Design (mirrors the moving-inversions method NASA SMRT uses, but emits this
repo's frozen event schema directly rather than wrapping SMRT):
  * Allocate one contiguous uint8 buffer of `buffer_mb` megabytes (numpy in
    system RAM for target "cpu"; CuPy in GPU DRAM for target "gpu").
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

# Verify the buffer in slices this big so the transient compare mask stays small
# (a whole-buffer `buf != val` would allocate a second full buffer and OOM).
VERIFY_CHUNK_BYTES = 64 * 1024 * 1024


def _handle_signal(signum, frame):
    global g_stop
    g_stop = True


DEFAULTS = {
    "target": "cpu",                         # "cpu" (2a, numpy/RAM) or "gpu" (2b, CuPy/GPU DRAM)
    "buffer_mb": 2048,                       # buffer size; leave memory headroom ("auto" also ok)
    "patterns": ["0x00", "0xFF", "0x55", "0xAA"],
    "hold_sweeps": 8,                        # verify passes before repainting
    "sweep_sleep_s": 0.05,                   # dwell between sweeps (exposure)
    "checkpoint_sweeps": 100,                # emit an "ok" checkpoint this often
    "max_report": 64,                        # cap anomaly records per sweep
    "iterations": 0,                         # 0 = run forever (counts sweeps)
    "log_dir": "./logs",
    "log_name": "mem_check.jsonl",           # per-target log file (gpu uses its own)
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


def read_meminfo_mb():
    """Return (MemTotal, MemAvailable) in MB from /proc/meminfo, or (None, None)."""
    total = avail = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
    except OSError:
        pass
    return total, avail


def gpu_mem_mb():
    """Return (total, free) GPU DRAM in MB via CuPy, in the same (total, avail)
    order as read_meminfo_mb. Requires CuPy (target 'gpu' only). On the Orin the
    GPU shares the LPDDR5 with the CPU, so 'total' is the whole board's DRAM and
    'free' reflects whatever the OS + any co-running GPU job (e.g. cuda_particles,
    §1a) currently leave available."""
    import cupy as cp
    free, total = cp.cuda.runtime.memGetInfo()   # CUDA returns (free, total) bytes
    return total // (1024 * 1024), free // (1024 * 1024)


def resolve_buffer_mb(cfg, ram_avail_mb):
    """Resolve buffer_mb: a fixed integer, or "auto" -> bounded fraction of free RAM.

    Orin's DRAM is unified (CPU, GPU, OS, and SoC clients share one LPDDR pool),
    so a fraction alone can overshoot on a lightly-loaded system and starve
    Linux into reclaim/OOM behavior that would masquerade as SEEs under beam.
    "auto" therefore takes the MINIMUM of three bounds (beam-campaign review,
    2026-08-14):

        min( auto_max_mb,                        # absolute ceiling
             auto_fraction  * MemAvailable,      # proportional take
             MemAvailable - auto_reserve_mb )    # hard floor left for the system

    Defaults: ceiling 3481 MB (3.4 GiB), fraction 0.60, reserve 2304 MB
    (2.25 GiB). Raise auto_reserve_mb if the co-running workload's peak grows.
    """
    spec = cfg["buffer_mb"]
    if isinstance(spec, str) and spec.strip().lower() == "auto":
        frac = float(cfg.get("auto_fraction", 0.60))
        ceiling = int(cfg.get("auto_max_mb", 3481))
        reserve = int(cfg.get("auto_reserve_mb", 2304))
        if ram_avail_mb:
            bounded = min(ceiling, int(frac * ram_avail_mb), ram_avail_mb - reserve)
            return max(64, bounded)
        sys.stderr.write("[mem_check] /proc/meminfo unavailable; 'auto' -> 2048 MB fallback\n")
        return 2048
    return int(spec)


def try_mlock(buf):
    """Pin the buffer in physical RAM so it can't be swapped out of DRAM.

    Best-effort: needs a high `ulimit -l` (or CAP_IPC_LOCK). Returns True on
    success, False (with a stderr note) if the OS refuses -- the test still runs,
    just without a hard no-swap guarantee.
    """
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.mlock(ctypes.c_void_p(buf.ctypes.data),
                      ctypes.c_size_t(buf.nbytes)) == 0:
            return True
        sys.stderr.write("[mem_check] mlock failed (errno %d); continuing unlocked\n"
                         % ctypes.get_errno())
    except Exception as e:  # noqa: BLE001 -- best-effort, never fatal
        sys.stderr.write("[mem_check] mlock unavailable: %s\n" % e)
    return False


def main():
    global g_stop
    ap = argparse.ArgumentParser(description="CPU/system RAM SEE pattern tester (channel 2a)")
    ap.add_argument("--config", default="config/mem_check.json")
    ap.add_argument("--target", choices=("cpu", "gpu"),
                    help="override config `target` (cpu=RAM/2a, gpu=GPU DRAM/2b)")
    ap.add_argument("--self-test", action="store_true",
                    help="small buffer + injected bit flip to prove detection")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.target:
        cfg["target"] = args.target
    # Fleet identity: jetson_id "auto" -> the board's hostname, so one config
    # file is correct on every DUT (set each board's hostname to orin-nano-0N).
    if str(cfg.get("jetson_id", "")).strip().lower() == "auto":
        import socket
        cfg["jetson_id"] = socket.gethostname()
    if args.self_test:
        cfg["buffer_mb"] = 64
        cfg["iterations"] = 6
        cfg["hold_sweeps"] = 3
        cfg["fault_inject_sweep"] = 2
        cfg["fault_inject_addr"] = 12345

    # --- select the memory backend from config `target` ---------------------
    # xp is the array module (numpy for CPU, CuPy for GPU); the pattern/verify/
    # scrub loop below is written against xp so it is identical for both paths.
    # sync() flushes the CUDA stream on GPU (a no-op on CPU) so async device work
    # is complete before we act on results or move on.
    target = str(cfg.get("target", "cpu")).strip().lower()
    if target == "gpu":
        import cupy as xp                        # GPU array lib (see docs/DEPENDENCIES.md)
        mem_total_mb, mem_avail_mb = gpu_mem_mb()

        def sync():
            xp.cuda.Stream.null.synchronize()
    elif target == "cpu":
        xp = np
        mem_total_mb, mem_avail_mb = read_meminfo_mb()

        def sync():
            pass
    else:
        sys.stderr.write("[mem_check] unknown target %r; expected 'cpu' or 'gpu'\n" % target)
        return 3

    buffer_mb = resolve_buffer_mb(cfg, mem_avail_mb)
    coverage_pct = round(100.0 * buffer_mb / mem_total_mb, 1) if mem_total_mb else None
    nbytes = buffer_mb * 1024 * 1024
    patterns = [int(p, 16) if isinstance(p, str) else int(p) for p in cfg["patterns"]]
    K_hold = max(1, int(cfg["hold_sweeps"]))
    max_report = max(1, int(cfg["max_report"]))
    ckpt = max(1, int(cfg["checkpoint_sweeps"]))
    limit = int(cfg["iterations"])

    os.makedirs(cfg["log_dir"], exist_ok=True)
    log_path = os.path.join(cfg["log_dir"], cfg["log_name"])
    fp = open(log_path, "a", encoding="utf-8")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Allocate the buffer up front so an OOM shows immediately, not mid-run. For
    # gpu this is a device allocation; sync so an allocation failure surfaces here.
    buf = xp.empty(nbytes, dtype=xp.uint8)
    sync()
    # mlock pins host RAM in place; it has no meaning for a GPU allocation.
    locked = try_mlock(buf) if (target == "cpu" and cfg.get("mlock")) else False

    emit(fp, cfg, "start", "info", {
        "test": "moving_inversions",
        "target": target,
        "buffer_mb": buffer_mb,
        "mem_total_mb": mem_total_mb,
        "mem_avail_mb": mem_avail_mb,
        "coverage_pct": coverage_pct,
        "mlock": locked,
        "patterns": [("0x%02x" % p) for p in patterns],
        "hold_sweeps": K_hold,
        "self_test": bool(args.self_test),
    })

    sweeps = 0
    upsets = 0
    corruption_seen = False

    while not g_stop:
        for val in patterns:
            buf[:] = xp.uint8(val)             # paint the pattern
            for _ in range(K_hold):
                if g_stop:
                    break

                # Optional detection self-test: flip one bit at the target sweep.
                if cfg["fault_inject_sweep"] and (sweeps + 1) == int(cfg["fault_inject_sweep"]):
                    addr = int(cfg["fault_inject_addr"]) % nbytes
                    buf[addr] ^= xp.uint8(0x01)

                # Read-back verify. Scan in fixed-size CHUNKs so the transient
                # comparison mask stays small: `buf != val` over the whole buffer
                # would allocate a full second buffer (2x memory) and OOM at large
                # `buffer_mb` -- this holds for GPU DRAM as well. Peak extra memory
                # here is one CHUNK, not one buffer.
                total_mism = 0
                emitted = 0
                for off in range(0, nbytes, VERIFY_CHUNK_BYTES):
                    seg = buf[off:off + VERIFY_CHUNK_BYTES]   # view, no copy
                    idx = xp.where(seg != xp.uint8(val))[0]
                    n = int(idx.size)
                    if n == 0:
                        continue
                    corruption_seen = True
                    total_mism += n
                    # On gpu, idx/seg live on the device; .tolist()/int() copy only
                    # the capped handful of flagged bytes back to the host.
                    for i in idx.tolist():
                        if emitted >= max_report:
                            break
                        actual = int(seg[i])
                        emit(fp, cfg, "mem_upset", "anomaly", {
                            "test": "moving_inversions",
                            "target": target,
                            "address": ("0x%x" % (off + i)),
                            "pattern": ("0x%02x" % val),
                            "expected": ("0x%02x" % val),
                            "actual": ("0x%02x" % actual),
                            "xor": ("0x%02x" % (val ^ actual)),
                            "sweep": sweeps + 1,
                        })
                        emitted += 1
                    seg[idx] = xp.uint8(val)   # vectorized scrub -> counted once
                sync()                        # ensure the compare + scrub completed
                upsets += total_mism
                if total_mism > emitted:
                    emit(fp, cfg, "mem_upset_overflow", "anomaly", {
                        "test": "moving_inversions",
                        "target": target,
                        "pattern": ("0x%02x" % val),
                        "reported": emitted,
                        "total_mismatches": total_mism,
                        "sweep": sweeps + 1,
                    })

                # Operator-facing one-liner (journal): concise SEE summary for this
                # sweep. The per-byte address/expected/actual/xor records emitted
                # above are this channel's post-processing data (memory has no
                # separate binary dump, unlike cuda_particles' see_dumps/).
                if total_mism > 0:
                    saved = ("%d record(s)" % emitted if total_mism <= emitted
                             else "%d of %d record(s) (capped; +overflow record)"
                                  % (emitted, total_mism))
                    print("[mem_check] SEE Detected: mem_upset x%d "
                          "(pattern 0x%02x, %s, sweep %d) | post-processing data saved: %s"
                          % (total_mism, val, target, sweeps + 1, saved),
                          file=sys.stderr, flush=True)

                sweeps += 1
                write_heartbeat(cfg["heartbeat_path"], sweeps, upsets, "0x%02x" % val)

                if sweeps % ckpt == 0:
                    emit(fp, cfg, "checkpoint", "ok", {
                        "test": "moving_inversions",
                        "target": target,
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
        "target": target,
        "sweeps": sweeps, "upsets": upsets,
        "corruption_seen": corruption_seen,
    })
    fp.close()
    return 2 if corruption_seen else 0


if __name__ == "__main__":
    sys.exit(main())
