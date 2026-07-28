#!/usr/bin/env python3
"""cpu_sort_check.py -- CPU checksummed sort workload (channel 1b).

Part of the Jetson Orin Nano proton-beam SEE test. This is the CPU half of the
"GPU/CPU workload" row: a compute workload whose output is checksummed against a
golden value so a bit flip anywhere in the working set or the sort computation
shows up as a silent-data-corruption (SDC) event.

How it works
------------
At startup we build a fixed, seeded random dataset and sort it once to get a
golden SHA-256 checksum. Every iteration re-sorts the *same* input and compares
the fresh checksum against the golden one. Under no radiation the two always
match; a mismatch is logged as ``SDC_DETECTED``.

Two files are written, on purpose:

* the **log file** (``--logfile``) gets one JSON object per interesting event
  (START / SDC_DETECTED / STOP, and OK for a ``--once`` self-test). This is what
  the arbiter pulls after an Ethernet outage, so it must survive on local disk.
* the **counter file** (``--counter-file``) is rewritten every single pass with
  ``{"iteration": N, "ts": ...}``. It lets the arbiter tell three states apart:
    - *stalled iteration*  -> process alive, counter file mtime frozen
    - *crashed*            -> process gone, ``systemctl status`` shows failed
    - *corruption*         -> SDC_DETECTED line present in the log

Log schema (shared with jetson/compute/gpu_burn_patch so the arbiter parses both
the same way)::

    {"ts": <float epoch>, "iteration": <int>, "kernel": "cpu_sort",
     "event": "START"|"OK"|"SDC_DETECTED"|"STOP",
     "expected": <hex sha256>, "actual": <hex sha256>}

Run standalone for a quick self-test::

    python3 cpu_sort_check.py --once --n 100000 --logfile /tmp/cpu_sort.log

Run for real under systemd (see cpu_sort_check.service).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import sys
import time

KERNEL_NAME = "cpu_sort"

# Defaults mirror docs/BUILD_PLAN.md section 1b.
DEFAULT_SEED = 12345
DEFAULT_N = 2_000_000
DEFAULT_LOGFILE = "/var/log/radtest/compute/cpu_sort.log"
DEFAULT_CYCLE_SLEEP = 0.1


def make_reference(seed: int, n: int):
    """Build the fixed input dataset and its golden checksum.

    Returns ``(data, expected_hash)`` where ``data`` is the unsorted input list
    (re-sorted every iteration) and ``expected_hash`` is the SHA-256 of the
    sorted result. Kept as a pure function so tests can call it with a small n.
    """
    rng = random.Random(seed)
    data = [rng.randint(0, 2 ** 31) for _ in range(n)]
    expected_hash = _hash_sorted(sorted(data))
    return data, expected_hash


def _hash_sorted(seq) -> str:
    """SHA-256 of a sequence's textual form. Matches BUILD_PLAN's approach."""
    return hashlib.sha256(str(seq).encode()).hexdigest()


def check_once(data, expected_hash: str):
    """Re-sort ``data`` and compare against ``expected_hash``.

    Returns ``(ok: bool, details: dict)`` with ``details`` holding the expected
    and actual hashes. Pure and side-effect free, so it is directly unit
    testable::

        data, h = make_reference(1, 1000)
        ok, d = check_once(data, h)
        assert ok and d["actual"] == h
    """
    result = sorted(data)
    actual_hash = _hash_sorted(result)
    ok = actual_hash == expected_hash
    return ok, {"expected": expected_hash, "actual": actual_hash}


def make_logger(logfile: str) -> logging.Logger:
    """A logger that writes one raw JSON object per line to ``logfile``."""
    logger = logging.getLogger("cpu_sort_check")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Drop any pre-existing handlers so repeated calls (e.g. in tests) do not
    # stack up duplicate file handlers.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    os.makedirs(os.path.dirname(os.path.abspath(logfile)), exist_ok=True)
    file_handler = logging.FileHandler(logfile)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    return logger


def log_event(logger: logging.Logger, event: str, iteration: int,
              expected: str, actual: str) -> None:
    """Emit one structured JSON log line matching the shared schema."""
    logger.info(json.dumps({
        "ts": time.time(),
        "iteration": iteration,
        "kernel": KERNEL_NAME,
        "event": event,
        "expected": expected,
        "actual": actual,
    }))


def write_counter(counter_file: str, iteration: int) -> None:
    """Rewrite the small iteration-counter file atomically.

    Written every pass. The atomic temp-file-then-rename avoids the arbiter ever
    reading a half-written counter during a poll.
    """
    payload = json.dumps({"iteration": iteration, "ts": time.time()})
    tmp = counter_file + ".tmp"
    with open(tmp, "w") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, counter_file)


def default_counter_file(logfile: str) -> str:
    """Place the counter file alongside the log unless overridden."""
    directory = os.path.dirname(os.path.abspath(logfile))
    return os.path.join(directory, "cpu_sort.counter.json")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CPU checksummed sort workload for the Jetson SEE test.")
    parser.add_argument("--logfile", default=DEFAULT_LOGFILE,
                        help="JSON-lines event log path (default: %(default)s)")
    parser.add_argument("--counter-file", default=None,
                        help="Iteration counter path (default: alongside logfile)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="RNG seed for the fixed dataset (default: %(default)s)")
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help="Number of elements to sort (default: %(default)s)")
    parser.add_argument("--cycle-sleep", type=float, default=DEFAULT_CYCLE_SLEEP,
                        help="Seconds to sleep between passes (default: %(default)s)")
    parser.add_argument("--once", action="store_true",
                        help="Run a single pass and exit (bench self-test). "
                             "Exit code 0 if the checksum matched, 1 if not.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    counter_file = args.counter_file or default_counter_file(args.logfile)
    logger = make_logger(args.logfile)

    data, expected_hash = make_reference(args.seed, args.n)
    log_event(logger, "START", 0, expected_hash, expected_hash)

    iteration = 0
    last_ok = True
    try:
        while True:
            iteration += 1
            last_ok, details = check_once(data, expected_hash)
            if not last_ok:
                log_event(logger, "SDC_DETECTED", iteration,
                          details["expected"], details["actual"])
            write_counter(counter_file, iteration)

            if args.once:
                if last_ok:
                    log_event(logger, "OK", iteration, expected_hash, expected_hash)
                break

            time.sleep(args.cycle_sleep)
    except KeyboardInterrupt:
        # Clean shutdown on Ctrl-C / systemd stop; not an error.
        pass
    finally:
        log_event(logger, "STOP", iteration, expected_hash, expected_hash)

    if args.once:
        return 0 if last_ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
