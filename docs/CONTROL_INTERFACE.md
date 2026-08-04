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
| `command` | `START_TEST` (or `STOP_TEST`, see below) |
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
