#!/usr/bin/env python3
"""current_logger.py -- DUT-side VDD_IN current sampler (channel 5, baseline runs).

Samples the Orin Nano module's on-board **INA3221** through the kernel hwmon
interface and writes one CSV row per sample. This is what produces the current
trace for a *baseline run*: the full test stack (cuda_particles + mem_check_gpu)
running with **no beam**, so the resulting current envelope is the reference a
beam-time abnormal-current threshold is judged against.

It is normally started for you by `jetson/control/control_receiver.py` when the
arbiter sends `BASELINE_TEST` (see docs/CONTROL_INTERFACE.md), but it is a
standalone program and can be run by hand:

    sudo python3 current_logger.py --out /var/log/radtest/power/baseline.csv \\
                                   --duration-s 3600 --interval-s 1

**No hardware is added by this.** The INA3221 is already on the module; we only
read its sysfs files. Reading is cheap (a few file reads every `--interval-s`),
so the sampler does not meaningfully perturb the workload it is measuring.

CSV columns (deliberately identical to the 2026-08-01 reference capture,
`docs/baseline_current_noSEE_orin-nano-01_20260801.csv`, so old and new runs can
be compared/concatenated without a converter)::

    sequence, recorded_at_utc, current_ma, voltage_mv, power_mw,
    rolling_average_ma, rolling_window_count, sensor_source,
    data_quality_flags, run_id, jetson_id, boot_id

`power_mw` is not measured -- the INA3221 gives current and bus voltage, so power
is derived (`current_ma * voltage_mv / 1000`) and every such row is flagged
`POWER_DERIVED_FROM_CURRENT_AND_VOLTAGE`. `rolling_average_ma` is the mean of the
last `--rolling-window` good samples and stays blank until that window is full.
A sample whose sysfs read fails is still written -- with empty readings and a
`SENSOR_READ_FAILED` flag -- so a gap in the sensor is visible in the data rather
than silently shortening the run.

On exit (duration reached, or SIGTERM/SIGINT from a manual STOP) it writes a
sidecar `<out>.summary.json` with the run's min/mean/max. The control receiver
reads that file to build the STOP reply the coordinator turns into its popup, so
an early stop still produces a complete, honest summary.

Standard library only. Deliberately no third-party deps: this runs as root on a
frozen campaign image where `pip install` is not an option.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone

# Rail whose current we care about: VDD_IN is the module's total input, i.e. the
# whole board's draw. The other INA3221 channels (VDD_CPU_GPU_CV, VDD_SOC) are
# subsets of it and are not what an SEL shows up in first.
DEFAULT_RAIL = "VDD_IN"
# 1 Hz. The 2026-08-01 reference capture used 5 s; 1 s resolves a current
# excursion five times more sharply for the same negligible cost (two sysfs reads
# per sample), and an hour is still only ~3600 rows.
DEFAULT_INTERVAL_S = 1.0
# Kept at ~50 s of smoothing, as the 5 s x 10 reference did, so the
# rolling_average_ma column still means the same thing across old and new runs.
DEFAULT_ROLLING_WINDOW = 50

# hwmon directories to search, in order. The Tegra BSP has shipped this sensor
# under both the upstream `ina3221` driver name and the older `ina3221x`.
HWMON_GLOBS = (
    "/sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*",
    "/sys/bus/i2c/drivers/ina3221x/*/hwmon/hwmon*",
    "/sys/class/hwmon/hwmon*",
)

FLAG_DERIVED_POWER = "POWER_DERIVED_FROM_CURRENT_AND_VOLTAGE"
FLAG_READ_FAILED = "SENSOR_READ_FAILED"

CSV_COLUMNS = (
    "sequence", "recorded_at_utc", "current_ma", "voltage_mv", "power_mw",
    "rolling_average_ma", "rolling_window_count", "sensor_source",
    "data_quality_flags", "run_id", "jetson_id", "boot_id",
)

g_stop = False


def _handle_signal(signum, frame):        # noqa: ARG001 - signal handler signature
    """SIGTERM/SIGINT -> finish the current sample, write the summary, exit 0."""
    global g_stop
    g_stop = True


def iso_now():
    """ISO-8601 UTC with millisecond precision (matches the event schema's `ts`)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def boot_id():
    """The kernel's boot id -- ties this CSV to one boot, so a reboot mid-run is
    detectable in post-processing (it is also how the arbiter correlates logs)."""
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as fp:
            return fp.read().strip()
    except OSError:
        return ""


def _read_text(path):
    with open(path, "r", encoding="utf-8") as fp:
        return fp.read().strip()


def _read_int(path):
    return int(_read_text(path))


def find_hwmon(explicit=None):
    """Return the INA3221 hwmon directory, or None if the sensor isn't present.

    An explicit --hwmon wins. Otherwise the driver-bound paths are searched first
    (they are unambiguous), then /sys/class/hwmon is filtered by its `name` file
    so we never grab the thermal zones or the PMIC.
    """
    if explicit:
        return explicit if os.path.isdir(explicit) else None

    for pattern in HWMON_GLOBS:
        for path in sorted(glob.glob(pattern)):
            if not os.path.isdir(path):
                continue
            try:
                name = _read_text(os.path.join(path, "name"))
            except OSError:
                # The driver-bound globs are already INA3221-specific, so a
                # missing `name` there is still a match; under /sys/class/hwmon
                # it is not, and the check below rejects it.
                name = "ina3221" if "ina3221" in path else ""
            if name.startswith("ina3221"):
                return path
    return None


def find_channel(hwmon, rail=DEFAULT_RAIL):
    """Return the 1-based INA3221 channel index feeding `rail`.

    The channel labels come from the board's device tree (`in<N>_label`), so this
    keeps working if NVIDIA reorders the rails on a future carrier board. Falls
    back to channel 1, which is VDD_IN on every Orin Nano devkit seen so far.
    """
    want = rail.strip().lower()
    for idx in (1, 2, 3):
        try:
            label = _read_text(os.path.join(hwmon, "in%d_label" % idx))
        except OSError:
            continue
        if label.strip().lower() == want:
            return idx
    return 1


def read_sample(hwmon, channel):
    """Read one (current_ma, voltage_mv) pair. Returns (None, None) on any failure.

    A sensor read is one open+read of a sysfs file; it can fail transiently (I2C
    bus contention) and must never kill a long baseline run, so the caller records
    the failure as a flagged row instead.
    """
    try:
        current_ma = _read_int(os.path.join(hwmon, "curr%d_input" % channel))
        voltage_mv = _read_int(os.path.join(hwmon, "in%d_input" % channel))
    except (OSError, ValueError):
        return None, None
    return current_ma, voltage_mv


def derive_power_mw(current_ma, voltage_mv):
    """mA * mV / 1000 = mW. Rounded to an integer, as the reference capture is."""
    return int(round(current_ma * voltage_mv / 1000.0))


def _stats(values):
    """min/mean/max of a list of numbers, or None when there is nothing to report."""
    if not values:
        return None
    return {
        "min": round(min(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
    }


def build_summary(state):
    """Assemble the sidecar summary from the run state accumulated in run()."""
    started = state["started_at"]
    ended = state["ended_at"]
    return {
        "run_id": state["run_id"],
        "jetson_id": state["jetson_id"],
        "boot_id": state["boot_id"],
        "csv": state["csv_path"],
        "csv_name": os.path.basename(state["csv_path"]),
        "sensor_source": state["sensor_source"],
        "rail": state["rail"],
        "interval_s": state["interval_s"],
        "started_at_utc": state["started_at_utc"],
        "ended_at_utc": state["ended_at_utc"],
        "duration_s": round(ended - started, 3) if ended and started else 0.0,
        "samples": state["samples"],
        "sensor_failures": state["failures"],
        "stopped_early": state["stopped_early"],
        "current_ma": _stats(state["currents"]),
        "voltage_mv": _stats(state["voltages"]),
        "power_mw": _stats(state["powers"]),
        "rolling_average_ma": _stats(state["rolling_averages"]),
    }


def write_summary(path, summary):
    """Write the sidecar summary JSON. Best-effort: the CSV is the real product."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(summary, fp, indent=2)
            fp.write("\n")
        os.replace(tmp, path)               # atomic: a reader never sees a partial file
        return True
    except OSError as exc:
        sys.stderr.write("[current_logger] could not write summary %s: %s\n" % (path, exc))
        return False


def _open_jsonl_emitter(path, run_id, jetson_id):
    """Return (emit_fn, close_fn) writing schema-v1 `power` records, or (None, None).

    Optional by design: the CSV is the deliverable, and the JSONL only exists so
    the arbiter's correlator can line current up against heartbeat/compute records
    on one clock. If shared/event_log.py can't be found we say so and carry on --
    a missing correlation feed must not cost us the baseline capture.
    """
    if not path:
        return None, None

    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.join(here, "..", "..", "shared")):
        if os.path.exists(os.path.join(cand, "event_log.py")):
            sys.path.insert(0, cand)
            break
    try:
        import event_log as el              # noqa: PLC0415 - located at runtime
    except ImportError as exc:
        sys.stderr.write("[current_logger] JSONL disabled (no event_log.py: %s)\n" % exc)
        return None, None

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fp = open(path, "a", encoding="utf-8")
    except OSError as exc:
        sys.stderr.write("[current_logger] JSONL disabled (%s: %s)\n" % (path, exc))
        return None, None

    meta = {"beam_energy": "none", "fluence_source": "none", "shield_config": "none"}

    def emit(event, status, payload):
        rec = el.envelope(run_id, jetson_id, "power", event, status, meta=meta)
        rec.update(payload)
        try:
            el.emit(fp, rec)
        except (OSError, ValueError) as err:
            sys.stderr.write("[current_logger] JSONL write failed: %s\n" % err)

    return emit, fp.close


def run(args):
    """Sample until the duration elapses or a stop signal arrives. Returns an exit code."""
    hwmon = find_hwmon(args.hwmon)
    if hwmon is None:
        sys.stderr.write(
            "[current_logger] no INA3221 hwmon directory found. Looked in:\n  %s\n"
            "Pass --hwmon <dir> if this board exposes it elsewhere.\n"
            % "\n  ".join(HWMON_GLOBS))
        return 3

    channel = find_channel(hwmon, args.rail)
    jetson_id = args.jetson_id
    if not jetson_id or jetson_id.strip().lower() == "auto":
        jetson_id = socket.gethostname()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    state = {
        "run_id": args.run_id,
        "jetson_id": jetson_id,
        "boot_id": boot_id(),
        "csv_path": os.path.abspath(args.out),
        "sensor_source": hwmon,
        "rail": args.rail,
        "interval_s": args.interval_s,
        "started_at": None, "started_at_utc": None,
        "ended_at": None, "ended_at_utc": None,
        "samples": 0, "failures": 0, "stopped_early": False,
        "currents": [], "voltages": [], "powers": [], "rolling_averages": [],
    }

    emit_jsonl, close_jsonl = _open_jsonl_emitter(args.jsonl, args.run_id, jetson_id)
    window = []                              # last N good current readings
    summary_path = args.summary or (args.out + ".summary.json")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    handle = open(args.out, "w", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    writer.writerow(CSV_COLUMNS)
    handle.flush()

    start = time.monotonic()
    state["started_at"] = start
    state["started_at_utc"] = iso_now()
    sys.stderr.write(
        "[current_logger] %s ch%d (%s) -> %s | every %gs%s\n"
        % (hwmon, channel, args.rail, args.out, args.interval_s,
           (" for %gs" % args.duration_s) if args.duration_s else " until stopped"))
    sys.stderr.flush()

    if emit_jsonl:
        # current_mA is required on every `power` record by schema v1, so the
        # start marker carries the first reading rather than a placeholder.
        first_ma, _ = read_sample(hwmon, channel)
        emit_jsonl("start", "info", {
            "current_mA": first_ma if first_ma is not None else 0,
            "tripped": False, "test": "current_baseline",
        })

    sequence = 0
    while not g_stop:
        if args.duration_s and (time.monotonic() - start) >= args.duration_s:
            break

        sequence += 1
        current_ma, voltage_mv = read_sample(hwmon, channel)
        recorded_at = iso_now()

        if current_ma is None:
            state["failures"] += 1
            writer.writerow([sequence, recorded_at, "", "", "", "", len(window),
                             hwmon, FLAG_READ_FAILED, args.run_id, jetson_id,
                             state["boot_id"]])
        else:
            power_mw = derive_power_mw(current_ma, voltage_mv)
            window.append(current_ma)
            if len(window) > args.rolling_window:
                window.pop(0)
            # Blank until the window is full, so an "average" is never quietly
            # computed over fewer samples than it claims.
            rolling = round(sum(window) / len(window), 1) \
                if len(window) == args.rolling_window else ""
            if rolling != "":
                state["rolling_averages"].append(rolling)

            state["samples"] += 1
            state["currents"].append(current_ma)
            state["voltages"].append(voltage_mv)
            state["powers"].append(power_mw)

            writer.writerow([sequence, recorded_at, current_ma, voltage_mv, power_mw,
                             rolling, len(window), hwmon, FLAG_DERIVED_POWER,
                             args.run_id, jetson_id, state["boot_id"]])
            if emit_jsonl:
                emit_jsonl("current_sample", "ok", {
                    "current_mA": current_ma, "voltage_mv": voltage_mv,
                    "power_mw": power_mw, "tripped": False,
                    "test": "current_baseline", "seq": sequence,
                })

        handle.flush()                      # a killed run keeps every row already taken

        # Absolute deadlines (start + n*interval) rather than sleep(interval), so
        # sampling does not drift by the read time over a long run. Sleep in short
        # slices so a STOP lands within ~a quarter second instead of a full period.
        deadline = start + sequence * args.interval_s
        while not g_stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if args.duration_s and (time.monotonic() - start) >= args.duration_s:
                break
            time.sleep(min(0.25, remaining))

    state["ended_at"] = time.monotonic()
    state["ended_at_utc"] = iso_now()
    state["stopped_early"] = bool(
        g_stop and args.duration_s and (state["ended_at"] - start) < args.duration_s)

    handle.close()
    summary = build_summary(state)
    write_summary(summary_path, summary)

    if emit_jsonl:
        emit_jsonl("stop", "info", {
            "current_mA": state["currents"][-1] if state["currents"] else 0,
            "tripped": False, "test": "current_baseline",
            "seq": sequence,
        })
    if close_jsonl:
        close_jsonl()

    stats = summary["current_ma"]
    sys.stderr.write(
        "[current_logger] done: %d sample(s), %d sensor failure(s), %.1fs%s | %s\n"
        % (summary["samples"], summary["sensor_failures"], summary["duration_s"],
           " (stopped early)" if summary["stopped_early"] else "",
           ("current_ma min/mean/max %.1f/%.1f/%.1f"
            % (stats["min"], stats["mean"], stats["max"])) if stats else "no readings"))
    sys.stderr.flush()

    # Exit non-zero only if we produced nothing usable; a few failed reads inside
    # an otherwise good run are data, not an error.
    return 0 if summary["samples"] else 4


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Sample the Orin's INA3221 VDD_IN rail to CSV (channel 5 baseline).")
    ap.add_argument("--out", required=True, help="CSV output path.")
    ap.add_argument("--duration-s", type=float, default=0.0,
                    help="Stop after this many seconds (0 = run until signalled).")
    ap.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S,
                    help="Seconds between samples (default: %(default)s).")
    ap.add_argument("--rolling-window", type=int, default=DEFAULT_ROLLING_WINDOW,
                    help="Samples per rolling average (default: %(default)s).")
    ap.add_argument("--run-id", default="unset", help="Run id recorded in every row.")
    ap.add_argument("--jetson-id", default="auto",
                    help="Board id ('auto' = hostname, the fleet default).")
    ap.add_argument("--rail", default=DEFAULT_RAIL,
                    help="INA3221 channel label to sample (default: %(default)s).")
    ap.add_argument("--hwmon", default=None,
                    help="Explicit hwmon directory (skips auto-detection).")
    ap.add_argument("--jsonl", default=None,
                    help="Also append schema-v1 `power` records to this JSONL file.")
    ap.add_argument("--summary", default=None,
                    help="Summary JSON path (default: <out>.summary.json).")
    args = ap.parse_args(argv)

    if args.interval_s <= 0:
        ap.error("--interval-s must be > 0")
    if args.rolling_window < 1:
        ap.error("--rolling-window must be >= 1")
    if args.duration_s < 0:
        ap.error("--duration-s must be >= 0")

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
