# Test-control interface (arbiter → DUT)

The arbiter has a **start/stop-test button**; pressing it sends a JSON command to
each DUT over Ethernet. The arbiter (sender) lives in a teammate's separate repo;
this repo implements only the **DUT-side receiver**, `jetson/control/control_receiver.py`,
run as the `test_control.service` systemd unit.

## Wire contract

**Transport:** TCP. The DUT listens on `listen_port` (default **6000**); the
arbiter opens a connection, sends one JSON object, reads one JSON reply, closes.
Pretty-printed or compact JSON both work (the receiver reads until one complete
object has arrived).

> **Verified against the real coordinator** (`madhavsharma01312003/melagen-test-coordinator`):
> transport is **TCP** on **port 6000** (`jetson_port` in its `config.example.json`).
> The coordinator sends newline-terminated JSON and reads the reply with
> `readline`, and it **hard-validates that the reply's `request_id` matches** the
> request — our receiver already echoes `request_id` and terminates the reply with
> `\n`, so it interoperates. (Heartbeat is a separate UDP channel; the coordinator
> repo does not implement heartbeat or log-pull.)

### Request (arbiter → DUT)

```json
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
```

Validated against (mirrors the sender's spec, held in `config/test_control.json`):

| Field | Rule |
|---|---|
| `protocol_version` | must equal `1` |
| `command` | `START_TEST` (or `STOP_TEST` / `BASELINE_TEST`, see below) |
| `beam_energy_mev` | one of `53, 100, 200` |
| `shielding_material` | one of `Aluminium, MLC1, MLC2` |
| `shielding_thickness_mm` | one of `8, 12, 16` |
| `duration_s` | **optional**; positive number ≤ `max_duration_s` (86400). Default `default_duration_s` (100) |
| `request_id`, `sent_at_utc` | required (present) |

A request failing any rule is **rejected** (no action taken) with an `error` reply.

### Reply (DUT → arbiter)

```json
{
  "protocol_version": 1,
  "request_id": "unique-request-id",
  "status": "ACCEPTED",
  "detail": "started",
  "jetson_id": "orin-nano-03",
  "applied": {"run_id": "unique-request-id", "beam_energy": "100MeV", "shield_config": "MLC1_12mm"},
  "channels": [{"name": "compute", "service": "cuda_particles.service", "ok": true, "detail": "restart ok"}],
  "duration_s": 100,
  "handled_at_utc": "2026-07-31T15:00:00.123Z"
}
```

The START ack echoes the effective **`duration_s`** (the value sent, or the default
applied when the field was omitted) so the sender can confirm the run length in force.

**`status` is `ACCEPTED` on success, `REJECTED` on failure** (invalid request or any
channel failed to start/stop). This vocabulary is required by the coordinator's GUI
— `coordinator/ui.py::_validate_response` treats any status other than `ACCEPTED`
as a rejection and surfaces the reply's **`error`** field, which we include on every
`REJECTED` reply. (`TcpTransport.send` itself only checks that `request_id` echoes;
the `ACCEPTED` check lives in the UI layer, so a raw socket test can pass while the
GUI still rejects — which is exactly how this was first missed.) The per-channel
`ok`/`detail` entries say which channel failed.

### STOP reply — post-test `summary`

A **STOP_TEST** reply also carries a `summary` block: the receiver scans each
channel's JSONL log (`channels[].log`) for records tagged with the run's id
(`target_request_id`, else the last START handled) and tallies single-event
effects. The coordinator GUI uses this for its post-test popup and `test_N.csv`.

```json
"summary": {
  "run_id": "…", "beam_energy": "100MeV", "shield_config": "MLC1_12mm",
  "duration_s": 42.6, "records_scanned": 91, "total_sees": 3, "sees_per_s": 0.0704,
  "by_type": {
    "cuda_golden_mismatch": 2,   "cuda_nonfinite": 0, "cuda_anomaly": 0,
    "cuda_shutdown": 1,          "gpu_mem_upset": 0,
    "mem_tester_restart": 0,     "fatal_error": 0
  }
}
```

Each SEE is attributed to exactly one type, so `by_type` **partitions** `total_sees`.
Counting keys on record fields, not event names: `cuda_golden_mismatch` = compute
`checksum` with `mismatch:true`; `cuda_nonfinite` = `finite:false`; `cuda_anomaly` =
`anomaly:true`; `gpu_mem_upset` = one per `mem_upset` record (a flipped byte);
`cuda_shutdown`/`mem_tester_restart` = extra `start` records beyond the first (a
service crashed and systemd restarted it mid-run); `fatal_error` = any `status:error`.
`duration_s` is the span of the run's first→last log timestamp. Summarising is
best-effort — a log read/parse failure yields `summary.error` and never fails STOP.

## What START_TEST does on the DUT

For each configured channel (compute + GPU memory), in order:

1. **Writes the run metadata into the channel's JSON config** so every emitted
   event record carries the beam/shield context:
   - `run_id` ← `request_id`
   - `beam_energy` ← `"<beam_energy_mev>MeV"` (e.g. `"100MeV"`)
   - `shield_config` ← `"<material>_<thickness>mm"` (e.g. `"MLC1_12mm"`)
2. **Touches the channel's `ARMED` flag** (so the test also survives a reboot —
   see `docs/SERVICES.md`).
3. **`systemctl restart`s the channel service** (restart, not start, so a new
   START with different beam params re-applies them even if a test is running).

**Idempotency:** a repeated `request_id` is acknowledged `ok` without re-acting,
so an arbiter retry can't double-start.

**DUT-owned run timer (auto-stop).** After starting the channels, the receiver arms
a local `threading.Timer` for `duration_s` (default 100). When it fires it does
exactly what a manual STOP does — disarm each `ARMED` flag, `systemctl stop` each
channel, `summarize_run()`, and write an `auto_stop` control-log record — so a run
ends on time **even if the network drops** between arbiter and DUT. A manual
`STOP_TEST` still works and **cancels** the pending timer (whichever fires first
wins); a new `START_TEST` **replaces** any timer still pending. Because the summary
is recomputed by re-scanning the persisted logs, a later STOP (e.g. the coordinator's
mirror auto-STOP) still returns the correct `summary` even though the services were
already stopped — `systemctl stop` is idempotent. The coordinator schedules its own
STOP at the same `duration_s` purely to retrieve that summary and write `test_N.csv`;
the DUT timer is the authoritative stop.

> **Note:** the arbiter host code is a teammate's, in a separate repo — this repo
> owns only the DUT receiver. See [`arbiter/README.md`](../arbiter/README.md).

## STOP_TEST — our forward-compatible extension

The receiver accepts `STOP_TEST` (needs `protocol_version`, `command`,
`request_id`, `sent_at_utc`): it **removes each `ARMED` flag and `systemctl stop`s
each channel**. The coordinator's `StopTestRequest` also carries a
**`target_request_id`** (the START it cancels; its own `request_id` is a fresh
uuid) — we accept that extra field, stop all channels regardless, and log
`target_request_id` so a stop can be correlated to its start.

## BASELINE_TEST — the no-beam reference run

A **baseline** is a normal test run with the beam off, plus a current capture. The
coordinator GUI has its own **Baseline Test** button and a duration in **minutes**;
pressing it sends:

```json
{
  "protocol_version": 1,
  "command": "BASELINE_TEST",
  "request_id": "unique-request-id",
  "duration_s": 3600,
  "duration_minutes": 60,
  "sent_at_utc": "2026-08-06T15:00:00.000Z"
}
```

| Field | Rule |
|---|---|
| `protocol_version`, `command`, `request_id`, `sent_at_utc` | required |
| `duration_s` | **optional**; positive number ≤ `max_duration_s`. Default `default_baseline_duration_s` (3600) |
| `duration_minutes` | informational echo of the operator's entry; the DUT acts on `duration_s` |

**No beam parameters are accepted** — the beam is off during a baseline, so an
energy/shielding value would record a condition that never existed. The channel
configs get `beam_energy: "none"`, `shield_config: "none"`, which is also how a
baseline row is told apart from beam data downstream.

On the DUT it does everything `START_TEST` does — same metadata write, same ARMED
flags, same `systemctl restart` of **`cuda_particles` + `mem_check_gpu`**, same
DUT-owned auto-stop timer — and additionally starts
[`jetson/power/current_logger.py`](../jetson/power/current_logger.py), which samples
the module's INA3221 `VDD_IN` rail (**1 Hz** by default; the 2026-08-01 reference
capture used 5 s) into a CSV. Running
the real workloads is the point: the current envelope has to describe the machine
we actually test, not an idle board.

The START ack names the CSV so the arbiter knows what to collect:

```json
"baseline": {
  "csv": "/var/log/radtest/power/baseline_current_orin-nano-01_20260806T150000Z.csv",
  "csv_name": "baseline_current_orin-nano-01_20260806T150000Z.csv",
  "summary_path": "…/baseline_current_orin-nano-01_20260806T150000Z.csv.summary.json",
  "interval_s": 1.0, "expected_samples": 3600
}
```

and the STOP (or auto-stop) reply carries the finished stats alongside the usual
SEE `summary`:

```json
"baseline": {
  "run_id": "…", "csv_name": "…", "samples": 720, "sensor_failures": 0,
  "duration_s": 3597.95, "stopped_early": false, "exit_code": 0,
  "current_ma": {"min": 1880, "mean": 1922.7, "max": 2040},
  "voltage_mv": {"min": 4968, "mean": 4968.0, "max": 4968},
  "power_mw":   {"min": 9340, "mean": 9551.0, "max": 10135},
  "rolling_average_ma": {"min": 1892.8, "mean": 1922.7, "max": 1969.6}
}
```

The sampler is a **subprocess this receiver owns**, not a systemd unit, so the
capture starts and ends exactly with the workloads it measures. It also
self-terminates at `duration_s`, so the CSV still completes if the receiver is
restarted mid-run, and it finalizes on SIGTERM, so an early **Stop Test** yields
complete stats for the samples actually taken. A sampler that fails to start
**REJECTS** the command — on a baseline the CSV is the whole deliverable, so a
silent partial success would be worse than a clear failure.

Configured under `current_logger` in `config/test_control.json` (`interval_s`,
`rolling_window`, `rail`, `csv_dir`, `hwmon` to pin the sysfs path if auto-detect
fails, and `on_start_test` — **false**, so beam runs are unchanged until the
campaign decides to log current during them too).

The CSV columns are identical to the 2026-08-01 reference capture
([`baseline_current_noSEE_orin-nano-01_20260801.csv`](baseline_current_noSEE_orin-nano-01_20260801.csv)),
so old and new baselines concatenate without a converter. The arbiter's log pull
mirrors `/var/log/radtest/power/` like any other channel, and the GUI copies the
CSV into its `results/baseline_<N>.csv`.

To sample current by hand, without the GUI:

```bash
sudo python3 /home/melagen/see-testsuite/jetson/power/current_logger.py \
     --out /var/log/radtest/power/baseline.csv --duration-s 3600 --interval-s 1
```

That samples current **only** — it does not start the workloads, so use the GUI
button (or `systemctl start` the two channels yourself) for a true baseline.

## Metadata note (campaign vs dev)

START_TEST rewrites the tracked channel config files (to inject the beam metadata),
so during a campaign the git clone will show those configs as modified — expected.
This matches the frozen-image campaign model (you don't `git pull` mid-campaign).
On a dev board, `git checkout -- <config>` or STOP first if you need a clean pull.

## Install (on each DUT)

Standard-library only — no extra deps. Runs as **root** (it must control services
and write flags):

```bash
sudo cp ~/see-testsuite/jetson/control/test_control.service /etc/systemd/system/test_control.service
sudo systemctl daemon-reload
sudo systemctl enable --now test_control.service
systemctl status test_control.service --no-pager
```

Verify it's listening and watch commands arrive:

```bash
sudo ss -ltnp | grep 6000
tail -f ~/see-testsuite/jetson/control/logs/test_control.jsonl
```

Restrict who can command the board by setting `allowed_peers` (arbiter IPs) in
`config/test_control.json`; `[]` accepts any source.
