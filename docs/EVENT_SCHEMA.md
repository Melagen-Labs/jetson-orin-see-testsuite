# Shared event schema (v1) — FROZEN

> **Status (2026-08-03): frozen and in use.** Every deployed channel emits this
> schema through [`shared/event_log.py`](../shared/event_log.py), which validates
> the envelope at runtime, and both the coordinator's live SEE panel and the
> results CSV parse it. Changing it is a breaking change across three repos —
> version it rather than editing v1 in place.

Every monitoring channel (compute, memory, heartbeat, boot, power) writes **one
JSON object per line** (JSONL) using the **same envelope**. The arbiter and the
coordinator GUI's live SEE panel then need exactly one parser instead of five.

## Common envelope (every record, every channel)

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1`. Bump only on a breaking change. |
| `ts` | string | ISO-8601 UTC with milliseconds, e.g. `2026-07-30T18:22:04.531Z`. |
| `run_id` | string | Assigned by the test coordinator per irradiation run. |
| `jetson_id` | string | Which DUT, e.g. `orin-nano-01`. |
| `channel` | string | One of `compute`, `memory`, `heartbeat`, `boot`, `power`. |
| `event` | string | Channel-defined, e.g. `start`, `checksum`, `stop`. |
| `status` | string | One of `ok`, `anomaly`, `stall`, `crash`, `tripped`, `info`. |
| `beam_energy` | string | Run metadata (e.g. `64MeV`); `unset` if unknown. |
| `fluence_source` | string | Run metadata (e.g. `cyclotron-A`); `unset` if unknown. |
| `shield_config` | string | Run metadata (e.g. `2mm-Al`); `unset` if unknown. |

## Per-channel payload (additional fields)

- **compute** (`cuda_particles`): `iter`, `epoch`, `step`, `hash`, `golden`,
  `mismatch` (bool), `finite` (bool), `max_abs_pos` (float), `anomaly` (bool),
  `see_event` (bool, optional — set once per epoch on the first mismatch; see
  the SEE-counting note in BUILD_PLAN).
- **memory** (cuda_memtest / SMRT): `test`, `address`, `pattern`, `expected`,
  `actual`, `xor`.
- **heartbeat**: `seq`, `uptime_s`.
- **boot**: `boot_id`, `uptime_s`, `reboot_count`.
- **power** (EE firmware, via arbiter): `current_mA`, `tripped` (bool).

## Examples

```json
{"schema_version":1,"ts":"2026-07-30T18:22:04.531Z","run_id":"R-014","jetson_id":"orin-nano-01","channel":"compute","event":"checksum","status":"ok","iter":50,"epoch":0,"step":50,"hash":"836d5c79e3cfefa8","golden":"836d5c79e3cfefa8","mismatch":false,"finite":true,"max_abs_pos":1.0,"anomaly":false,"beam_energy":"64MeV","fluence_source":"cyclotron-A","shield_config":"2mm-Al"}
{"schema_version":1,"ts":"2026-07-30T18:22:41.902Z","run_id":"R-014","jetson_id":"orin-nano-01","channel":"power","event":"sample","status":"tripped","current_mA":1180,"tripped":true,"beam_energy":"64MeV","fluence_source":"cyclotron-A","shield_config":"2mm-Al"}
```

## Status derivation

`status` is the one field the dashboard colors on. Rule of thumb per channel:
`ok` normally; `anomaly` when the channel detects corruption (compute `anomaly`,
memory mismatch); `tripped` for a power cutoff; `stall`/`crash` are emitted by the
arbiter/supervisor when a channel's heartbeat freezes or its process dies;
`info` for lifecycle records (`start`/`stop`).

## Change policy

- **Additive-only within v1:** new *optional* payload fields are fine and do not
  bump the version. Consumers must ignore unknown fields.
- **Breaking change** (rename/remove a field, change a type, change an enum
  meaning) → bump `schema_version` to 2 and update this doc + `event_schema.json`.
- Do not change v1 mid-campaign; all logs from one campaign must share one schema.

## Current conformance

- `cuda_particles` already emits `ts`, `event`, and the run metadata
  (`run_id`, `jetson_id`, `beam_energy`, `fluence_source`, `shield_config`) plus
  the full compute payload. To become v1-conformant it must additionally emit
  `schema_version`, `channel:"compute"`, and `status`. **That code change is
  deferred** so the on-hardware-verified workload is not disturbed before its
  soak test; it is a small, isolated edit to `logger`/`particles_main.cpp`.
- All other channels are unbuilt and will be written against this schema directly.
