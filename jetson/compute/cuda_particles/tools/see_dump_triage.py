#!/usr/bin/env python3
"""see_dump_triage.py -- offline post-processing of SEE epoch state dumps.

When `cuda_particles` flags an epoch (final checksum != golden, NaN/Inf, or
out-of-bounds), it dumps that epoch's buffered per-checkpoint state to
`see_dumps/epoch_<N>_iter_<M>.bin` and logs a `see_event` record carrying the
dump's shape (`dump_checkpoints`, `dump_stride`, `num_particles`,
`floats_per_checkpoint`). The arbiter rsyncs both (the `radpull` channel).

This tool is the first (CPU-only, laptop-runnable) post-processing stage. For
each dumped epoch it:

  1. re-hashes every dumped checkpoint (FNV-1a 64 over pos bytes then vel bytes,
     exactly `checksum.cpp::hashState`) and compares against the board's golden
     table -> the FIRST divergent checkpoint localises the upset to a window of
     `dump_stride` iterations (detection alone only says "somewhere this epoch");
  2. scans each checkpoint for NaN/Inf and max|pos| -> distinguishes bit-flip
     silent corruption from numeric blow-up, and shows how corruption propagates;
  3. reports per-epoch verdicts plus a machine-readable JSON (--json).

What this CANNOT do (documented limitation, matches the README): discriminate 1
vs 2+ upsets inside one epoch ("grouped SEEs"). Once state diverges, every later
hash mismatches by propagation, so a second hit is masked. That needs a REPLAY:
re-run the deterministic sim on a reference (unirradiated) Orin from the epoch's
known initial state, inject/compare checkpoint-by-checkpoint against this dump,
and count fresh divergences after the first. The dump + golden + config carry
everything that replay needs; the replay itself must run on a board with the
same build (bit-exact determinism holds per-binary/per-GPU, not across builds).

Inputs (all pulled by arbiter/pull_logs.sh into arbiter_logs/compute/):
  * cuda_particles.jsonl        -- the run's event log (see_event/sim_fault records)
  * see_dumps/epoch_*.bin       -- raw little-endian float32; per checkpoint:
                                   <count> pos floats then <count> vel floats,
                                   where count = floats_per_checkpoint / 2
  * golden_hashes.txt           -- one 16-hex FNV-1a 64 hash per checkpoint
                                   (PER-BOARD: use the same board's table)

Usage:
    python see_dump_triage.py --logs arbiter_logs/compute
    python see_dump_triage.py --logs arbiter_logs/compute --run-id <uuid> --json out.json

Standard library only. Pure-Python FNV over ~10 MB/dump takes a few seconds per
epoch -- fine for triage (SEEs are rare by design; see the README's epoch-length
tuning rule).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys

FNV1A64_OFFSET = 1469598103934665603
FNV1A64_PRIME = 1099511628211
MASK64 = (1 << 64) - 1

# Bounds check mirrors particles_main.cpp: anomaly if max|pos| > 2.0
MAX_ABS_POS_BOUND = 2.0


def fnv1a64(data: bytes, seed: int = FNV1A64_OFFSET) -> int:
    """FNV-1a 64 over `data`, matching checksum.cpp byte-for-byte."""
    h = seed
    for b in data:
        h = ((h ^ b) * FNV1A64_PRIME) & MASK64
    return h


def hash_state(pos_bytes: bytes, vel_bytes: bytes) -> int:
    """checksum.cpp::hashState -- pos bytes then vel bytes chained."""
    return fnv1a64(vel_bytes, fnv1a64(pos_bytes))


def load_golden(path: str) -> list[int]:
    """Read the golden table: one 16-hex hash per line, one per checkpoint."""
    hashes = []
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                hashes.append(int(line, 16))
    return hashes


def float_stats(buf: bytes) -> tuple[int, float]:
    """(non_finite_count, max_abs) over a little-endian float32 buffer."""
    n = len(buf) // 4
    floats = struct.unpack("<%df" % n, buf[: n * 4])
    bad = 0
    max_abs = 0.0
    for v in floats:
        if math.isfinite(v):
            a = abs(v)
            if a > max_abs:
                max_abs = a
        else:
            bad += 1
    return bad, max_abs


def find_see_records(log_path: str, run_id: str | None) -> list[dict]:
    """Collect see_event/sim_fault records (the dump-bearing ones) from a JSONL."""
    out = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("event") not in ("see_event", "sim_fault"):
                continue
            if run_id and rec.get("run_id") != run_id:
                continue
            out.append(rec)
    return out


def triage_dump(rec: dict, logs_dir: str, golden: list[int]) -> dict:
    """Analyse one dump-bearing record; return a per-epoch report dict."""
    report = {
        "event": rec.get("event"),
        "epoch": rec.get("epoch"),
        "iter": rec.get("iter"),
        "ts": rec.get("ts"),
        "run_id": rec.get("run_id"),
        "jetson_id": rec.get("jetson_id"),
        "dump": rec.get("dump") or None,
    }

    dump_rel = rec.get("dump")
    if not dump_rel:
        report["status"] = "no_dump"
        report["note"] = ("record carries no dump (save_see_epochs off, dump "
                          "write failed, or crash before any checkpoint)")
        return report

    dump_path = os.path.join(logs_dir, dump_rel)
    if not os.path.exists(dump_path):
        report["status"] = "dump_missing"
        report["note"] = "dump file not found under --logs (pull incomplete?)"
        return report

    n_ck = int(rec.get("dump_checkpoints") or 0)
    fpc = int(rec.get("floats_per_checkpoint") or 0)
    stride = int(rec.get("dump_stride") or 0)
    if not n_ck or not fpc:
        report["status"] = "bad_metadata"
        report["note"] = "record lacks dump_checkpoints/floats_per_checkpoint"
        return report

    count = fpc // 2                       # floats per buffer (pos or vel)
    ck_bytes = fpc * 4                     # bytes per checkpoint
    expected = n_ck * ck_bytes
    actual = os.path.getsize(dump_path)
    report["dump_bytes"] = actual
    if actual != expected:
        report["status"] = "size_mismatch"
        report["note"] = ("dump is %d bytes, metadata implies %d "
                          "(truncated pull or corrupted dump)" % (actual, expected))
        return report

    checkpoints = []
    first_divergent = None
    with open(dump_path, "rb") as fp:
        for i in range(n_ck):
            blob = fp.read(ck_bytes)
            pos, vel = blob[: count * 4], blob[count * 4:]
            h = hash_state(pos, vel)
            golden_h = golden[i] if i < len(golden) else None
            match = (golden_h is not None and h == golden_h)
            nonfinite_p, max_abs_p = float_stats(pos)
            nonfinite_v, _ = float_stats(vel)
            ck = {
                "checkpoint": i,
                "step": (i + 1) * stride if stride else None,
                "hash": "%016x" % h,
                "golden": ("%016x" % golden_h) if golden_h is not None else None,
                "match": match,
                "nonfinite": nonfinite_p + nonfinite_v,
                "max_abs_pos": round(max_abs_p, 6),
            }
            if not match and first_divergent is None:
                first_divergent = i
            checkpoints.append(ck)

    report["checkpoints"] = checkpoints
    report["first_divergent_checkpoint"] = first_divergent
    if first_divergent is None:
        report["status"] = "all_match"
        report["note"] = ("every dumped checkpoint matches golden -- upset must "
                          "have hit after the last dumped checkpoint, or this "
                          "was a crash-SEE (sim_fault) with clean state")
    else:
        report["status"] = "divergence_localised"
        lo = first_divergent * stride if stride else None
        hi = (first_divergent + 1) * stride if stride else None
        report["upset_window_steps"] = [lo, hi]
        total_nonfinite = sum(c["nonfinite"] for c in checkpoints)
        worst = max(c["max_abs_pos"] for c in checkpoints)
        if total_nonfinite:
            report["character"] = "numeric_blowup (NaN/Inf present)"
        elif worst > MAX_ABS_POS_BOUND:
            report["character"] = "out_of_bounds (|pos| > %.1f)" % MAX_ABS_POS_BOUND
        else:
            report["character"] = "silent_bit_corruption (finite, in-bounds)"
        report["note"] = ("upset localised to steps [%s..%s) of the epoch; later "
                          "checkpoints diverge by propagation. 1-vs-2+ upsets in "
                          "this epoch needs a reference-board replay (see module "
                          "docstring)." % (lo, hi))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Triage cuda_particles SEE state dumps against the golden table.")
    ap.add_argument("--logs", default="arbiter_logs/compute",
                    help="Pulled compute log dir holding cuda_particles.jsonl and "
                         "see_dumps/ (default: %(default)s)")
    ap.add_argument("--log-file", default=None,
                    help="Event JSONL (default: <logs>/cuda_particles.jsonl)")
    ap.add_argument("--golden", default=None,
                    help="Golden hash table from the SAME board "
                         "(default: <logs>/golden_hashes.txt)")
    ap.add_argument("--run-id", default=None,
                    help="Only triage records from this run_id")
    ap.add_argument("--json", default=None,
                    help="Also write the full report as JSON to this path")
    args = ap.parse_args(argv)

    log_file = args.log_file or os.path.join(args.logs, "cuda_particles.jsonl")
    golden_path = args.golden or os.path.join(args.logs, "golden_hashes.txt")

    for path, what in ((log_file, "event log"), (golden_path, "golden table")):
        if not os.path.exists(path):
            print("ERROR: %s not found: %s" % (what, path), file=sys.stderr)
            if what == "golden table":
                print("  (golden_hashes.txt is per-board and pulled by "
                      "arbiter/pull_logs.sh; re-run a pull, or pass --golden)",
                      file=sys.stderr)
            return 1

    golden = load_golden(golden_path)
    records = find_see_records(log_file, args.run_id)
    if not records:
        print("No see_event/sim_fault records found%s -- nothing to triage."
              % (" for run %s" % args.run_id if args.run_id else ""))
        return 0

    reports = []
    for rec in records:
        rep = triage_dump(rec, args.logs, golden)
        reports.append(rep)
        print("epoch %-8s %-10s %s" % (rep.get("epoch"), rep.get("event"),
                                       rep.get("status")))
        if rep.get("first_divergent_checkpoint") is not None:
            print("  first divergent checkpoint: %s  window: steps %s"
                  % (rep["first_divergent_checkpoint"],
                     rep.get("upset_window_steps")))
            print("  character: %s" % rep.get("character"))
        if rep.get("note"):
            print("  %s" % rep["note"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump({"golden_checkpoints": len(golden),
                       "reports": reports}, fp, indent=2)
        print("JSON report: %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
