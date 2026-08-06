#!/usr/bin/env python3
"""control_receiver.py -- DUT-side test-control receiver (arbiter command channel).

The arbiter has a start/stop-test button; its command arrives over Ethernet as a
JSON message. The arbiter (sender) lives in a teammate's repo -- this implements
ONLY the DUT-side receiver, to the agreed contract.

Contract (arbiter -> DUT), one JSON object per TCP connection:
    {
      "protocol_version": 1,
      "command": "START_TEST",
      "request_id": "unique-request-id",
      "beam_energy_mev": 100,
      "shielding_material": "MLC1",
      "shielding_thickness_mm": 12,
      "duration_s": 100,
      "sent_at_utc": "2026-07-31T15:00:00.000Z"
    }

`duration_s` is optional (default `default_duration_s`, 100). The DUT owns the
run timer -- robust to network blips: on START it (re)starts the channels, then a
local threading.Timer auto-stops + summarizes them after `duration_s`, doing
exactly what a manual STOP would (disarm, `systemctl stop`, summarize, log). A
manual STOP_TEST still works and cancels the pending timer (whichever fires
first). A new START cancels any timer still pending from a previous run.

Reply (DUT -> arbiter), one JSON object + newline. The coordinator's GUI accepts
a reply only when status == "ACCEPTED" (coordinator/ui.py::_validate_response) and
shows the "error" field otherwise, so we answer in that vocabulary:
    {"protocol_version":1,"request_id":<echo>,"status":"ACCEPTED"|"REJECTED",
     "detail":<str>,"error":<str, only when REJECTED>,"jetson_id":<hostname>,
     "channels":[...],"handled_at_utc":<iso>}

Behaviour:
  * START_TEST -> validate; write the beam/shield metadata into each test
    channel's JSON config (so every emitted event record carries it); `touch`
    each channel's ARMED flag; `systemctl restart` each channel service. Restart
    (not just start) so a START with new beam params re-applies them even if a
    test is already running.
  * BASELINE_TEST -> the same start, with the beam metadata set to "none" and the
    INA3221 current sampler (jetson/power/current_logger.py) started alongside for
    the same duration. It runs the SAME workloads a beam run does, so the captured
    current envelope describes the real test-day load; the STOP/auto-stop reply
    carries a `baseline` block naming the CSV and its min/mean/max. No beam params
    are accepted -- a baseline is measured with the beam off.
  * STOP_TEST  -> remove each ARMED flag; `systemctl stop` each channel service.
    The coordinator (melagen-test-coordinator) sends STOP_TEST with an extra
    `target_request_id` (the START it cancels; its own `request_id` is a fresh
    uuid). We stop all channels regardless and just log `target_request_id`; the
    unknown field is accepted, not rejected.

Runs as root (systemd unit) so it can control the services and write the flags.
Standard library only -- no third-party deps. Transport is TCP; if the arbiter
uses HTTP/UDP instead, only the socket-server plumbing at the bottom changes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone


# --- config -----------------------------------------------------------------

DEFAULTS = {
    "listen_host": "0.0.0.0",         # bind all interfaces (direct arbiter link)
    "listen_port": 6000,              # TCP port the arbiter connects to (coordinator's jetson_port)
    "allowed_peers": [],              # [] = accept any source IP; else allow-list
    "read_timeout_s": 5.0,            # per-connection read timeout
    "max_msg_bytes": 65536,           # reject absurdly large payloads
    "systemctl_timeout_s": 30.0,      # per `systemctl` call

    # --- contract validation (must match the arbiter/teammate's sender) ------
    "protocol_version": 1,
    "supported_commands": ["START_TEST", "STOP_TEST", "BASELINE_TEST"],
    "default_duration_s": 100,         # DUT-owned run timer when START omits duration_s
    "default_baseline_duration_s": 3600,  # BASELINE_TEST default (1 h, the reference run)
    "max_duration_s": 86400,           # sanity cap (24 h) on an operator-supplied duration
    "beam_energies_mev": [50, 63, 125, 200],
    "shielding_modes": ["preset", "custom"],
    "shielding_materials": ["Bare", "Aluminium", "MLC1", "MLC2"],
    "shielding_thicknesses_mm": [0, 8, 12, 16],

    "control_log": "./logs/test_control.jsonl",

    # Each test channel this receiver arms/starts. `config` gets the run metadata
    # written into it; `armed_flag` is touched/removed; `service` is (re)started
    # or stopped. Order is the start order.
    "channels": [
        {
            "name": "compute",
            "config": "/home/melagen/see-testsuite/jetson/compute/cuda_particles/config/particles.json",
            "armed_flag": "/home/melagen/see-testsuite/jetson/compute/cuda_particles/ARMED",
            "service": "cuda_particles.service",
            "log": "/var/log/radtest/compute/cuda_particles.jsonl",
        },
        {
            "name": "memory_gpu",
            "config": "/home/melagen/see-testsuite/jetson/memory/config/mem_check_gpu.json",
            "armed_flag": "/home/melagen/see-testsuite/jetson/memory/ARMED",
            "service": "mem_check_gpu.service",
            "log": "/var/log/radtest/memory/mem_check_gpu.jsonl",
        },
    ],

    # Channel 5 (current) is NOT a systemd service: it is a plain subprocess this
    # receiver owns for the length of one run, so a baseline capture starts and
    # ends exactly with the workloads it is measuring. `on_start_test` is false so
    # a beam-day START_TEST behaves exactly as it does today -- flip it only if the
    # campaign decides to log current during beam runs too.
    "current_logger": {
        "enabled": True,
        "script": "/home/melagen/see-testsuite/jetson/power/current_logger.py",
        "python": "python3",
        "csv_dir": "/var/log/radtest/power",
        "jsonl": "/var/log/radtest/power/current.jsonl",
        "interval_s": 5.0,
        "rolling_window": 10,
        "rail": "VDD_IN",
        "hwmon": None,                 # None = auto-detect; set to pin the sysfs dir
        "on_start_test": False,
        # Stop budget. Both are small on purpose: the whole STOP reply has to beat
        # the coordinator's ~5 s command timeout.
        "stop_settle_s": 1.0,          # reap a sampler that is already finishing
        "stop_grace_s": 3.0,           # after SIGTERM: flush + write the summary
    },
}

# START_TEST must carry all of these (the arbiter's REQUIRED_FIELDS).
REQUIRED_START_FIELDS = (
    "protocol_version", "command", "request_id", "beam_energy_mev",
    "shielding_material", "shielding_thickness_mm", "sent_at_utc",
)
# STOP_TEST needs only enough to identify the request (no beam params to stop).
REQUIRED_STOP_FIELDS = ("protocol_version", "command", "request_id", "sent_at_utc")
# BASELINE_TEST carries no beam params -- a baseline is measured with the beam OFF,
# so accepting an energy/shield here would put a fiction into the run metadata.
REQUIRED_BASELINE_FIELDS = ("protocol_version", "command", "request_id", "sent_at_utc")

# The coordinator's GUI accepts a reply ONLY if status == "ACCEPTED"
# (coordinator/ui.py::_validate_response); any other value is treated as a
# rejection and it displays the reply's "error" field. So we speak that
# vocabulary: "ACCEPTED" on success, "REJECTED" + an "error" string on failure.
STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"


def load_config(path):
    """Merge the JSON config over DEFAULTS (missing keys fall back to defaults)."""
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fp:
            cfg.update(json.load(fp))
    return cfg


def iso_now():
    """ISO-8601 UTC timestamp with millisecond precision."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def hostname():
    return socket.gethostname()


# --- validation -------------------------------------------------------------

def _validated_numeric(value, field_name, *, allow_zero=False):
    """Return (numeric, error) for finite numeric request fields."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "%s must be numeric" % field_name

    numeric = float(value)
    if not math.isfinite(numeric):
        return None, "%s must be finite" % field_name

    if allow_zero:
        if numeric < 0:
            return None, "%s must not be negative" % field_name
    elif numeric <= 0:
        return None, "%s must be greater than 0" % field_name

    return numeric, None


def validate(msg, cfg):
    """Return (command, errors). `errors` empty == valid."""

    errors = []
    if not isinstance(msg, dict):
        return None, ["payload is not a JSON object"]

    cmd = msg.get("command")
    if cmd not in cfg["supported_commands"]:
        errors.append(
            "unsupported command %r (expected one of %s)"
            % (cmd, cfg["supported_commands"])
        )
        return cmd, errors

    required = {
        "START_TEST": REQUIRED_START_FIELDS,
        "BASELINE_TEST": REQUIRED_BASELINE_FIELDS,
    }.get(cmd, REQUIRED_STOP_FIELDS)
    for field_name in required:
        if field_name not in msg:
            errors.append("missing required field %r" % field_name)

    if msg.get("protocol_version") != cfg["protocol_version"]:
        errors.append(
            "protocol_version must be %s"
            % cfg["protocol_version"]
        )

    # duration_s is optional on both run commands (each has its own default) but
    # must be a positive number within the sanity cap when present. bool is an int
    # subclass -> reject it explicitly.
    if cmd in ("START_TEST", "BASELINE_TEST") and "duration_s" in msg:
        d = msg.get("duration_s")
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            errors.append("duration_s must be a positive number")
        elif not (0 < d <= cfg["max_duration_s"]):
            errors.append("duration_s must be > 0 and <= %s" % cfg["max_duration_s"])

    if cmd == "START_TEST":
        if msg.get("beam_energy_mev") not in cfg["beam_energies_mev"]:
            errors.append(
                "beam_energy_mev must be one of %s"
                % cfg["beam_energies_mev"]
            )

        allowed_modes = cfg.get(
            "shielding_modes",
            ["preset", "custom"],
        )
        mode = msg.get("shielding_mode", "preset")

        if mode not in allowed_modes:
            errors.append(
                "shielding_mode must be one of %s"
                % allowed_modes
            )
        elif mode == "preset":
            material = msg.get("shielding_material")
            thickness = msg.get("shielding_thickness_mm")

            if material not in cfg["shielding_materials"]:
                errors.append(
                    "shielding_material must be one of %s"
                    % cfg["shielding_materials"]
                )

            if thickness not in cfg["shielding_thicknesses_mm"]:
                errors.append(
                    "shielding_thickness_mm must be one of %s"
                    % cfg["shielding_thicknesses_mm"]
                )

            if material == "Bare" and thickness != 0:
                errors.append("Bare shielding must use reference 0")

            if material != "Bare" and thickness == 0:
                errors.append(
                    "only Bare shielding may use reference 0"
                )

            reference = msg.get("shielding_reference_mm")
            if reference is not None and reference != thickness:
                errors.append(
                    "preset shielding_reference_mm must match "
                    "shielding_thickness_mm"
                )

            actual = msg.get("shielding_actual_thickness_mm")
            if actual is not None:
                _, error = _validated_numeric(
                    actual,
                    "shielding_actual_thickness_mm",
                    allow_zero=material == "Bare",
                )
                if error:
                    errors.append(error)
        else:
            material = msg.get("shielding_material")
            if not isinstance(material, str):
                errors.append(
                    "custom shielding_material must be a string"
                )
            elif not material.strip():
                errors.append(
                    "custom shielding_material must not be blank"
                )

            thickness = msg.get("shielding_thickness_mm")
            actual = msg.get(
                "shielding_actual_thickness_mm",
                thickness,
            )
            thickness_numeric, thickness_error = _validated_numeric(
                thickness,
                "shielding_thickness_mm",
                allow_zero=False,
            )
            if thickness_error:
                errors.append(thickness_error)

            actual_numeric, actual_error = _validated_numeric(
                actual,
                "shielding_actual_thickness_mm",
                allow_zero=False,
            )
            if actual_error:
                errors.append(actual_error)

            if (
                thickness_numeric is not None
                and actual_numeric is not None
                and not math.isclose(
                    thickness_numeric,
                    actual_numeric,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                errors.append(
                    "custom thickness fields do not match"
                )

        configuration_id = msg.get("shielding_configuration_id")
        if (
            configuration_id is not None
            and not isinstance(configuration_id, str)
        ):
            errors.append(
                "shielding_configuration_id must be a string"
            )

        if (
            "campaign_metadata" in msg
            and not isinstance(msg["campaign_metadata"], dict)
        ):
            errors.append(
                "campaign_metadata must be a JSON object"
            )

        # (duration_s is validated above, for START_TEST and BASELINE_TEST alike.)

    return cmd, errors


# --- actions ----------------------------------------------------------------

def apply_metadata(channel, meta):
    """Write run metadata into a channel's JSON config so its event log carries
    the beam/shield context. Leaves all other config keys untouched."""
    path = channel["config"]
    st = os.stat(path)                   # capture the original owner/group/mode
    with open(path, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    cfg.update(meta)                     # run_id / beam_energy / shield_config / fluence_source
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, indent=2)
        fp.write("\n")
    # This receiver runs as root, so the freshly-created tmp would land root:root and,
    # after the swap, lock out melagen's no-sudo edits of the config (e.g. the chaos
    # toggle -> PermissionError). Restore the config's original owner/group/mode before
    # replacing it. Best-effort (we are root, so it should always succeed).
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o777)
    except OSError as exc:               # noqa: BLE001
        sys.stderr.write("[test_control] could not preserve %s ownership: %s\n" % (path, exc))
    os.replace(tmp, path)                # atomic swap so a service never reads a half-written file


def systemctl(action, service, cfg):
    """Run `systemctl <action> <service>`; return (ok, detail)."""
    try:
        r = subprocess.run(["systemctl", action, service],
                           capture_output=True, text=True,
                           timeout=cfg["systemctl_timeout_s"])
        if r.returncode == 0:
            return True, "%s ok" % action
        return False, "%s failed: %s" % (action, (r.stderr or r.stdout).strip())
    except Exception as exc:                        # noqa: BLE001 - report any failure to the arbiter
        return False, "%s error: %s" % (action, exc)


def run_metadata(msg, baseline=False):
    """Build the run metadata written into each channel config.

    A baseline runs with the beam OFF, so its records say so explicitly ("none")
    rather than inheriting whatever energy/shield the last beam run left behind --
    that is what keeps baseline rows from ever being mistaken for beam data. Every
    shielding key a beam run writes is overwritten too, so nothing stale survives
    in the channel configs from the previous run.
    """
    if baseline:
        return {
            "run_id": msg["request_id"],
            "beam_energy": "none",
            "shield_config": "none",
            "shielding_mode": "none",
            "shielding_material": "none",
            "shielding_reference_mm": None,
            "shielding_actual_thickness_mm": None,
            "shielding_configuration_id": "",
        }

    mode = msg.get("shielding_mode", "preset")
    material = msg["shielding_material"]
    transmitted_thickness = msg["shielding_thickness_mm"]
    actual_thickness = msg.get(
        "shielding_actual_thickness_mm",
        transmitted_thickness,
    )
    reference_mm = msg.get("shielding_reference_mm")
    configuration_id = msg.get(
        "shielding_configuration_id",
        "",
    )

    if mode == "preset":
        shield_config = "%s_%smm" % (
            material,
            transmitted_thickness,
        )
    else:
        shield_config = "%s_%smm" % (
            material,
            actual_thickness,
        )

    return {
        "run_id": msg["request_id"],
        "beam_energy": "%sMeV" % msg["beam_energy_mev"],
        "shield_config": shield_config,
        "shielding_mode": mode,
        "shielding_material": material,
        "shielding_reference_mm": reference_mm,
        "shielding_actual_thickness_mm": actual_thickness,
        "shielding_configuration_id": configuration_id,
    }



def do_start(msg, cfg, baseline=False):
    """Apply metadata, arm, and (re)start every channel.

    Identical for a beam run and a baseline run -- that is the point: a baseline
    must exercise the same stack (cuda_particles + mem_check_gpu) that beam day
    will, or its current envelope describes a machine we never actually test.
    """
    channel_meta = run_metadata(msg, baseline=baseline)

    applied = dict(channel_meta)
    if isinstance(msg.get("campaign_metadata"), dict):
        applied["campaign_metadata"] = dict(
            msg["campaign_metadata"]
        )

    results = []
    for channel in cfg["channels"]:
        result = {
            "name": channel["name"],
            "service": channel["service"],
        }
        try:
            apply_metadata(channel, channel_meta)
            open(channel["armed_flag"], "a").close()
            ok, detail = systemctl(
                "restart",
                channel["service"],
                cfg,
            )
            result["ok"], result["detail"] = ok, detail
        except Exception as exc:
            result["ok"] = False
            result["detail"] = "arm/config error: %s" % exc
        results.append(result)

    return applied, results


def do_stop(cfg):
    """Disarm and stop every channel. Returns per-channel results."""
    results = []
    for ch in cfg["channels"]:
        r = {"name": ch["name"], "service": ch["service"]}
        try:
            if os.path.exists(ch["armed_flag"]):
                os.remove(ch["armed_flag"])           # rm ARMED so a reboot won't restart it
            # A STOP arriving right after START can race the unit's own (re)start
            # transition, so the first `systemctl stop` occasionally returns non-zero
            # ("failed to stop") while the unit settles (observed 2026-08-02; a manual
            # stop ~12 s later succeeded). Retry with a short backoff instead.
            ok, detail = systemctl("stop", ch["service"], cfg)
            attempts = 1
            while not ok and attempts < 3:
                time.sleep(2.0)
                ok, detail = systemctl("stop", ch["service"], cfg)
                attempts += 1
            if attempts > 1:
                detail = "%s (after %d attempts)" % (detail, attempts)
            r["ok"], r["detail"] = ok, detail
        except Exception as exc:                      # noqa: BLE001
            r["ok"], r["detail"] = False, "disarm/stop error: %s" % exc
        results.append(r)
    return results


# --- channel 5: the current sampler (baseline runs) -------------------------
#
# Unlike the workload channels this is not a systemd unit. It is a subprocess the
# receiver starts and stops with the run, because a current trace is only useful
# when its start/end line up exactly with the workloads it measured. The sampler
# also self-terminates at `--duration-s`, so the capture still completes if this
# receiver is restarted mid-run.

def _read_json(path):
    """Read a JSON file, or return None if it is missing/unreadable/corrupt."""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return None


def _prepare_csv_dir(path):
    """Create the CSV directory, inheriting /var/log/radtest's owner+mode.

    We run as root, so a plain makedirs would leave root:root 0755 and the
    arbiter's low-privilege `radpull` user could not fetch the CSV (the other log
    dirs are melagen:radlog 2750). Best-effort -- a failure here is not worth
    losing a baseline run over, it just means pulling as root instead.
    """
    os.makedirs(path, exist_ok=True)
    if not hasattr(os, "chown"):                      # non-POSIX dev box; DUT is Linux
        return
    parent = os.path.dirname(path.rstrip("/")) or "/"
    try:
        st = os.stat(parent)
        os.chown(path, st.st_uid, st.st_gid)
        os.chmod(path, st.st_mode & 0o7777)
    except OSError as exc:                            # noqa: BLE001
        sys.stderr.write("[test_control] could not match %s perms to %s: %s\n"
                         % (path, parent, exc))


def start_current_logger(cfg, run_id, duration_s, state, lock):
    """Start the INA3221 sampler for this run.

    Returns (channel_result, baseline_info). `channel_result` joins the reply's
    `channels` list so a sampler that fails to start REJECTS the command -- on a
    baseline run the CSV is the entire deliverable, so a silent partial success
    would be worse than a clear failure.
    """
    lg = cfg.get("current_logger") or {}
    name = "current"
    if not lg.get("enabled", True):
        return ({"name": name, "service": "current_logger.py", "ok": True,
                 "detail": "disabled in config"}, None)

    script = lg.get("script")
    csv_dir = lg.get("csv_dir", "/var/log/radtest/power")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_name = "baseline_current_%s_%s.csv" % (hostname(), stamp)
    csv_path = os.path.join(csv_dir, csv_name)
    summary_path = csv_path + ".summary.json"

    cmd = [
        lg.get("python", "python3"), script,
        "--out", csv_path,
        "--duration-s", str(duration_s),
        "--interval-s", str(lg.get("interval_s", 5.0)),
        "--rolling-window", str(lg.get("rolling_window", 10)),
        "--rail", lg.get("rail", "VDD_IN"),
        "--run-id", run_id,
        "--jetson-id", "auto",
        "--summary", summary_path,
    ]
    if lg.get("jsonl"):
        cmd += ["--jsonl", lg["jsonl"]]
    if lg.get("hwmon"):
        cmd += ["--hwmon", lg["hwmon"]]

    # The sampler takes its first reading at t=0, so a duration that divides evenly
    # yields duration/interval samples. The epsilon keeps binary-float division
    # (0.3/0.05 == 5.999...) from under-reporting by one.
    interval_s = float(lg.get("interval_s", 5.0) or 5.0)
    info = {
        "csv": csv_path,
        "csv_name": csv_name,
        "summary_path": summary_path,
        "interval_s": interval_s,
        "expected_samples": int(duration_s / interval_s + 1e-6),
    }

    try:
        if not script or not os.path.exists(script):
            raise FileNotFoundError("current_logger.py not found at %r" % script)
        _prepare_csv_dir(csv_dir)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as exc:                          # noqa: BLE001 - report to the arbiter
        return ({"name": name, "service": "current_logger.py", "ok": False,
                 "detail": "current logger failed to start: %s" % exc}, None)

    with lock:
        state["current_logger"] = dict(info, proc=proc, run_id=run_id)
    return ({"name": name, "service": "current_logger.py", "ok": True,
             "detail": "sampling to %s" % csv_name}, info)


def stop_current_logger(cfg, state, lock):
    """Stop the sampler (if running) and return its summary dict, or None.

    Called from both the manual STOP and the DUT-owned auto-stop, and it must stay
    FAST: this runs inside the request handler, and the coordinator gives up on a
    command after ~5 s (observed 2026-08-06 -- an early Stop Test timed out in the
    GUI while the DUT was still waiting on the sampler, leaving the operator with a
    "test remains active" error for a run that had in fact stopped).

    So: a short settle for the auto-stop case, where the sampler is already
    finishing its own duration, then SIGTERM. The sampler flushes every row as it
    goes and writes its summary on SIGTERM, noticing the signal within a quarter
    second, so an operator's early stop still yields complete stats for the samples
    actually taken -- it just does it in under a second instead of fifteen.
    """
    lg = cfg.get("current_logger") or {}
    settle = float(lg.get("stop_settle_s", 1.0))
    grace = float(lg.get("stop_grace_s", 3.0))

    with lock:
        entry = state.pop("current_logger", None)
    if entry is None:
        return state.get("last_baseline")

    proc = entry["proc"]
    try:
        proc.wait(timeout=settle)                     # already finishing? reap it
    except subprocess.TimeoutExpired:
        proc.terminate()                              # SIGTERM -> clean finalize
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()                               # last resort; CSV rows survive
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass

    summary = _read_json(entry["summary_path"]) or {
        "run_id": entry["run_id"],
        "error": "sampler wrote no summary (killed before it could finalize?)",
    }
    summary.setdefault("csv", entry["csv"])
    summary.setdefault("csv_name", entry["csv_name"])
    summary["exit_code"] = proc.returncode
    with lock:
        state["last_baseline"] = summary
    return summary


def log_control(cfg, record):
    """Append one JSONL line to the local control log and echo to the journal."""
    record = dict(record, ts=iso_now(), jetson_id=hostname())
    try:
        os.makedirs(os.path.dirname(cfg["control_log"]) or ".", exist_ok=True)
        with open(cfg["control_log"], "a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception as exc:                          # noqa: BLE001 - never let logging kill the listener
        sys.stderr.write("[test_control] control-log write failed: %s\n" % exc)
    sys.stdout.write("[test_control] %s\n" % json.dumps(record, separators=(",", ":")))
    sys.stdout.flush()


# --- run summary (post-test popup / CSV data for the coordinator) -----------

# Each SEE is attributed to exactly ONE type, so the per-type counts partition the
# total (they sum to total_sees). Keys are stable ids; the coordinator maps them to
# operator-facing labels.
SEE_TYPES = (
    "cuda_golden_mismatch",   # compute checksum != golden hash (bit-level divergence)
    "cuda_nonfinite",         # compute produced NaN/Inf
    "cuda_anomaly",           # compute values outside physical bounds
    "cuda_shutdown",          # compute service crashed and was restarted mid-run
    "gpu_mem_upset",          # GPU DRAM moving-inversions flipped byte
    "mem_tester_restart",     # memory service crashed and was restarted mid-run
    "fatal_error",            # any record with status == "error"
)


def _parse_ts(ts):
    """Parse an event-log ISO-8601 'Z' timestamp to epoch seconds, or None."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None


def summarize_run(cfg, run_id):
    """Scan each channel's JSONL log for records tagged with this run_id and return
    a summary dict: duration, total SEEs, SEEs/sec, and a per-type breakdown. The
    counting keys on record FIELDS (mismatch/finite/anomaly/upsets), so it does not
    depend on exact event names. Best-effort: any read/parse failure is skipped, so
    STOP is never blocked by reporting."""
    counts = {k: 0 for k in SEE_TYPES}
    start_records = {}          # channel -> number of "start" records seen for run
    beam_energy = shield_config = None
    ts_min = ts_max = None
    matched = 0

    for ch in cfg.get("channels", []):
        path = ch.get("log")
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("run_id") != run_id:
                        continue
                    matched += 1
                    channel = rec.get("channel")
                    event = rec.get("event")
                    status = rec.get("status")

                    if not beam_energy and rec.get("beam_energy"):
                        beam_energy = rec.get("beam_energy")
                    if not shield_config and rec.get("shield_config"):
                        shield_config = rec.get("shield_config")

                    t = _parse_ts(rec.get("ts"))
                    if t is not None:
                        ts_min = t if ts_min is None else min(ts_min, t)
                        ts_max = t if ts_max is None else max(ts_max, t)

                    if event == "start":
                        start_records[channel] = start_records.get(channel, 0) + 1
                        continue

                    # Attribute each anomalous record to exactly one SEE type.
                    if status == "error":
                        counts["fatal_error"] += 1
                    elif channel == "compute" and event == "checksum":
                        if rec.get("mismatch") is True:
                            counts["cuda_golden_mismatch"] += 1
                        elif rec.get("finite") is False:
                            counts["cuda_nonfinite"] += 1
                        elif rec.get("anomaly") is True:
                            counts["cuda_anomaly"] += 1
                    elif channel == "memory" and event == "mem_upset":
                        counts["gpu_mem_upset"] += 1        # one record per flipped byte
        except OSError:
            continue

    # A service that crashes mid-run is restarted by systemd (Restart=always), which
    # writes another "start" record with the same run_id. START itself accounts for
    # the first start, so any extra starts are unexpected shutdowns.
    counts["cuda_shutdown"] = max(0, start_records.get("compute", 0) - 1)
    counts["mem_tester_restart"] = max(0, start_records.get("memory", 0) - 1)

    duration_s = (round(ts_max - ts_min, 3)
                  if ts_min is not None and ts_max is not None else 0.0)
    total = sum(counts.values())
    rate = round(total / duration_s, 4) if duration_s > 0 else 0.0

    return {
        "run_id": run_id,
        "jetson_id": hostname(),
        "beam_energy": beam_energy or "unset",
        "shield_config": shield_config or "unset",
        "duration_s": duration_s,
        "records_scanned": matched,
        "total_sees": total,
        "sees_per_s": rate,
        "by_type": counts,
    }


# --- DUT-owned run timer ----------------------------------------------------

def cancel_auto_stop(state, lock):
    """Cancel a pending auto-stop timer, if any. Safe to call when none is armed."""
    with lock:
        timer = state.pop("auto_stop_timer", None)
        state.pop("auto_stop_run_id", None)
    if timer is not None:
        timer.cancel()


def auto_stop(cfg, run_id, state, lock):
    """Timer callback: do exactly what a manual STOP does -- disarm + stop every
    channel, summarize the run, and log it -- but without a reply (no peer). Guards
    against racing a manual STOP that already cleared this timer for the same run."""
    with lock:
        # If a manual STOP (or a new START) already fired for this run, the timer
        # entry is gone/replaced -> this callback is stale; do nothing.
        if state.get("auto_stop_run_id") != run_id:
            return
        state.pop("auto_stop_timer", None)
        state.pop("auto_stop_run_id", None)
    results = do_stop(cfg)
    baseline = stop_current_logger(cfg, state, lock)
    all_ok = all(r["ok"] for r in results)
    summary = None
    try:
        summary = summarize_run(cfg, run_id)
    except Exception as exc:                    # noqa: BLE001 - reporting never fails the stop
        summary = {"run_id": run_id, "error": "summary failed: %s" % exc}
    log_control(cfg, {"event": "auto_stop", "run_id": run_id,
                      "summary": summary, "baseline": baseline,
                      "channels": results, "ok": all_ok})


def schedule_auto_stop(cfg, run_id, duration_s, state, lock):
    """Arm the DUT-owned run timer: after `duration_s`, auto-stop this run. Replaces
    any timer still pending from a previous START."""
    cancel_auto_stop(state, lock)
    timer = threading.Timer(duration_s, auto_stop, args=(cfg, run_id, state, lock))
    timer.daemon = True
    with lock:
        state["auto_stop_timer"] = timer
        state["auto_stop_run_id"] = run_id
    timer.start()


# --- request handling -------------------------------------------------------

def handle_message(raw, cfg, state, lock):
    """Parse+dispatch one raw request; return the reply dict to send back."""
    reply = {"protocol_version": cfg["protocol_version"], "jetson_id": hostname(),
             "handled_at_utc": iso_now()}
    try:
        msg = json.loads(raw)
    except ValueError as exc:
        detail = "invalid JSON: %s" % exc
        reply.update(request_id=None, status=STATUS_REJECTED, detail=detail, error=detail)
        log_control(cfg, {"event": "reject", "reason": "invalid_json"})
        return reply

    reply["request_id"] = msg.get("request_id")
    cmd, errors = validate(msg, cfg)
    if errors:
        detail = "; ".join(errors)
        reply.update(status=STATUS_REJECTED, detail=detail, error=detail)
        log_control(cfg, {"event": "reject", "command": cmd,
                          "request_id": msg.get("request_id"), "errors": errors})
        return reply

    # Idempotency: the arbiter may retry a request_id; don't re-act on a repeat.
    with lock:
        if msg["request_id"] and msg["request_id"] == state.get("last_request_id"):
            reply.update(status=STATUS_ACCEPTED, detail="duplicate request_id; already handled",
                         channels=state.get("last_channels", []))
            return reply
        state["last_request_id"] = msg["request_id"]

    if cmd in ("START_TEST", "BASELINE_TEST"):
        # A baseline is a normal test run with the beam off, plus the current
        # sampler -- same channels, same arming, same DUT-owned timer.
        baseline = cmd == "BASELINE_TEST"
        # Any run replaces a previous one, so never leave an orphaned sampler
        # writing into the last run's CSV.
        stop_current_logger(cfg, state, lock)
        meta, results = do_start(msg, cfg, baseline=baseline)
        duration_s = msg.get(
            "duration_s",
            cfg["default_baseline_duration_s"] if baseline else cfg["default_duration_s"])

        baseline_info = None
        if baseline or (cfg.get("current_logger") or {}).get("on_start_test"):
            power_result, baseline_info = start_current_logger(
                cfg, msg["request_id"], duration_s, state, lock)
            results.append(power_result)

        all_ok = all(r["ok"] for r in results)
        detail = "started" if all_ok else "one or more channels failed to start"
        # DUT owns the run timer: auto-stop after duration_s. Arm it even on a
        # partial start so any channel that DID come up is cleaned up. A later
        # STOP or START cancels/replaces this timer.
        schedule_auto_stop(cfg, msg["request_id"], duration_s, state, lock)
        reply.update(status=STATUS_ACCEPTED if all_ok else STATUS_REJECTED,
                     detail=detail, applied=meta, channels=results,
                     duration_s=duration_s)
        if baseline_info:
            reply["baseline"] = baseline_info
        if not all_ok:
            reply["error"] = detail
        log_control(cfg, {"event": "baseline_test" if baseline else "start_test",
                          "request_id": msg["request_id"],
                          "applied": meta, "duration_s": duration_s,
                          "baseline": baseline_info,
                          "channels": results, "ok": all_ok})
    else:  # STOP_TEST
        # A manual STOP cancels the DUT-owned timer (whichever fires first wins).
        cancel_auto_stop(state, lock)
        results = do_stop(cfg)
        # Stop the current sampler too, and carry its stats back in the reply so a
        # baseline run reports its CSV the moment the operator presses stop.
        baseline = stop_current_logger(cfg, state, lock)
        if baseline:
            reply["baseline"] = baseline
        all_ok = all(r["ok"] for r in results)
        detail = "stopped" if all_ok else "one or more channels failed to stop"
        reply.update(status=STATUS_ACCEPTED if all_ok else STATUS_REJECTED,
                     detail=detail, channels=results)
        if not all_ok:
            reply["error"] = detail
        # target_request_id: the coordinator's STOP_TEST names which START to stop
        # (its own request_id is a fresh uuid). We stop all channels regardless, but
        # log it so a stop can be correlated back to its start.
        # Summarise the run just stopped -> the coordinator shows a popup + writes a
        # CSV from this. run_id is the START this STOP targets (fall back to the last
        # START we handled, in case the sender omitted target_request_id).
        run_id = msg.get("target_request_id") or state.get("last_start_run_id")
        if run_id:
            try:
                reply["summary"] = summarize_run(cfg, run_id)
            except Exception as exc:            # noqa: BLE001 - reporting never fails STOP
                reply["summary"] = {"run_id": run_id, "error": "summary failed: %s" % exc}
        log_control(cfg, {"event": "stop_test", "request_id": msg["request_id"],
                          "target_request_id": msg.get("target_request_id"),
                          "summary": reply.get("summary"),
                          "baseline": reply.get("baseline"),
                          "channels": results, "ok": all_ok})

    with lock:
        state["last_channels"] = reply.get("channels", [])
        if cmd in ("START_TEST", "BASELINE_TEST"):
            state["last_start_run_id"] = msg["request_id"]
    return reply


class _Handler(socketserver.BaseRequestHandler):
    """Reads one JSON object per connection, dispatches it, writes one JSON reply."""

    def handle(self):
        cfg = self.server.cfg
        peer_ip = self.client_address[0]
        if cfg["allowed_peers"] and peer_ip not in cfg["allowed_peers"]:
            log_control(cfg, {"event": "reject", "reason": "peer_not_allowed", "peer": peer_ip})
            return

        self.request.settimeout(cfg["read_timeout_s"])
        buf = b""
        decoder = json.JSONDecoder()
        while len(buf) < cfg["max_msg_bytes"]:
            try:
                chunk = self.request.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            # Accept pretty-printed or compact JSON: stop as soon as one full
            # object has been received (don't require the peer to half-close).
            try:
                decoder.raw_decode(buf.decode("utf-8"))
                break
            except (ValueError, UnicodeDecodeError):
                continue

        reply = handle_message(buf.decode("utf-8", "replace"),
                               cfg, self.server.state, self.server.lock)
        try:
            self.request.sendall((json.dumps(reply, separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="DUT-side arbiter test-control receiver.")
    ap.add_argument("--config", default="config/test_control.json")
    args = ap.parse_args()
    cfg = load_config(args.config)

    srv = _Server((cfg["listen_host"], cfg["listen_port"]), _Handler)
    srv.cfg = cfg
    srv.state = {}
    srv.lock = threading.Lock()
    log_control(cfg, {"event": "listening",
                      "addr": "%s:%s" % (cfg["listen_host"], cfg["listen_port"]),
                      "channels": [c["name"] for c in cfg["channels"]]})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
