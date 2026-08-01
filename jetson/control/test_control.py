#!/usr/bin/env python3
"""test_control.py -- DUT-side test-control receiver (arbiter command channel).

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
import os
import socket
import socketserver
import subprocess
import sys
import threading
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
    "supported_commands": ["START_TEST", "STOP_TEST"],
    "default_duration_s": 100,         # DUT-owned run timer when START omits duration_s
    "max_duration_s": 86400,           # sanity cap (24 h) on an operator-supplied duration
    "beam_energies_mev": [53, 100, 200],
    "shielding_materials": ["Aluminium", "MLC1", "MLC2"],
    "shielding_thicknesses_mm": [8, 12, 16],

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
}

# START_TEST must carry all of these (the arbiter's REQUIRED_FIELDS).
REQUIRED_START_FIELDS = (
    "protocol_version", "command", "request_id", "beam_energy_mev",
    "shielding_material", "shielding_thickness_mm", "sent_at_utc",
)
# STOP_TEST needs only enough to identify the request (no beam params to stop).
REQUIRED_STOP_FIELDS = ("protocol_version", "command", "request_id", "sent_at_utc")

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

def validate(msg, cfg):
    """Return (command, errors). `errors` empty == valid. `command` may be None."""
    errors = []
    if not isinstance(msg, dict):
        return None, ["payload is not a JSON object"]

    cmd = msg.get("command")
    if cmd not in cfg["supported_commands"]:
        errors.append("unsupported command %r (expected one of %s)"
                      % (cmd, cfg["supported_commands"]))
        # can't validate fields without a known command
        return cmd, errors

    required = REQUIRED_START_FIELDS if cmd == "START_TEST" else REQUIRED_STOP_FIELDS
    for f in required:
        if f not in msg:
            errors.append("missing required field %r" % f)

    if msg.get("protocol_version") != cfg["protocol_version"]:
        errors.append("protocol_version must be %s" % cfg["protocol_version"])

    if cmd == "START_TEST":
        if msg.get("beam_energy_mev") not in cfg["beam_energies_mev"]:
            errors.append("beam_energy_mev must be one of %s" % cfg["beam_energies_mev"])
        if msg.get("shielding_material") not in cfg["shielding_materials"]:
            errors.append("shielding_material must be one of %s" % cfg["shielding_materials"])
        if msg.get("shielding_thickness_mm") not in cfg["shielding_thicknesses_mm"]:
            errors.append("shielding_thickness_mm must be one of %s" % cfg["shielding_thicknesses_mm"])
        # duration_s is optional (defaults to default_duration_s). If present it must
        # be a positive number within the sanity cap. bool is an int subclass -> reject.
        if "duration_s" in msg:
            d = msg.get("duration_s")
            if isinstance(d, bool) or not isinstance(d, (int, float)):
                errors.append("duration_s must be a positive number")
            elif not (0 < d <= cfg["max_duration_s"]):
                errors.append("duration_s must be > 0 and <= %s" % cfg["max_duration_s"])

    return cmd, errors


# --- actions ----------------------------------------------------------------

def apply_metadata(channel, meta):
    """Write run metadata into a channel's JSON config so its event log carries
    the beam/shield context. Leaves all other config keys untouched."""
    path = channel["config"]
    with open(path, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    cfg.update(meta)                     # run_id / beam_energy / shield_config / fluence_source
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, indent=2)
        fp.write("\n")
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


def do_start(msg, cfg):
    """Apply metadata, arm, and (re)start every channel. Returns per-channel results."""
    meta = {
        "run_id": msg["request_id"],
        "beam_energy": "%sMeV" % msg["beam_energy_mev"],
        "shield_config": "%s_%smm" % (msg["shielding_material"], msg["shielding_thickness_mm"]),
        # fluence_source is not in the arbiter contract -> left as each config has it.
    }
    results = []
    for ch in cfg["channels"]:
        r = {"name": ch["name"], "service": ch["service"]}
        try:
            apply_metadata(ch, meta)
            open(ch["armed_flag"], "a").close()      # touch ARMED (create if absent)
            ok, detail = systemctl("restart", ch["service"], cfg)
            r["ok"], r["detail"] = ok, detail
        except Exception as exc:                      # noqa: BLE001
            r["ok"], r["detail"] = False, "arm/config error: %s" % exc
        results.append(r)
    return meta, results


def do_stop(cfg):
    """Disarm and stop every channel. Returns per-channel results."""
    results = []
    for ch in cfg["channels"]:
        r = {"name": ch["name"], "service": ch["service"]}
        try:
            if os.path.exists(ch["armed_flag"]):
                os.remove(ch["armed_flag"])           # rm ARMED so a reboot won't restart it
            ok, detail = systemctl("stop", ch["service"], cfg)
            r["ok"], r["detail"] = ok, detail
        except Exception as exc:                      # noqa: BLE001
            r["ok"], r["detail"] = False, "disarm/stop error: %s" % exc
        results.append(r)
    return results


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
    all_ok = all(r["ok"] for r in results)
    summary = None
    try:
        summary = summarize_run(cfg, run_id)
    except Exception as exc:                    # noqa: BLE001 - reporting never fails the stop
        summary = {"run_id": run_id, "error": "summary failed: %s" % exc}
    log_control(cfg, {"event": "auto_stop", "run_id": run_id,
                      "summary": summary, "channels": results, "ok": all_ok})


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

    if cmd == "START_TEST":
        meta, results = do_start(msg, cfg)
        all_ok = all(r["ok"] for r in results)
        detail = "started" if all_ok else "one or more channels failed to start"
        # DUT owns the run timer: auto-stop after duration_s (default 100). Arm it
        # even on a partial start so any channel that DID come up is cleaned up. A
        # later STOP or START cancels/replaces this timer.
        duration_s = msg.get("duration_s", cfg["default_duration_s"])
        schedule_auto_stop(cfg, msg["request_id"], duration_s, state, lock)
        reply.update(status=STATUS_ACCEPTED if all_ok else STATUS_REJECTED,
                     detail=detail, applied=meta, channels=results,
                     duration_s=duration_s)
        if not all_ok:
            reply["error"] = detail
        log_control(cfg, {"event": "start_test", "request_id": msg["request_id"],
                          "applied": meta, "duration_s": duration_s,
                          "channels": results, "ok": all_ok})
    else:  # STOP_TEST
        # A manual STOP cancels the DUT-owned timer (whichever fires first wins).
        cancel_auto_stop(state, lock)
        results = do_stop(cfg)
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
                          "channels": results, "ok": all_ok})

    with lock:
        state["last_channels"] = reply.get("channels", [])
        if cmd == "START_TEST":
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
