# Power Monitor Firmware ↔ Arbiter Interface (channel 5)

**Status:** contract between the EE's power-monitor firmware and the arbiter's
[`arbiter/power_reader.py`](../arbiter/power_reader.py).

**Scope:** hardware selection and firmware implementation are **out of scope for
this repo** and owned by the project's electrical engineer. This document is the
data/behavior contract the firmware must satisfy so the arbiter can ingest it,
log it, and correlate a candidate single-event latchup (SEL) against the other
four channels. It expands [`BUILD_PLAN.md`](BUILD_PLAN.md) §5.

---

## 1. What the firmware must measure

| Requirement | Value | Why |
|---|---|---|
| Current sample rate | 100 Hz – 1 kHz (as fast as the existing sense hardware allows) | fast enough to catch a fast SEL current spike |
| Absolute-current threshold | configurable | detects a slow, persistent abnormal current |
| Rate-of-change (di/dt) threshold | configurable | a fast SEL spike and a slow abnormal current are different signatures; the test plan distinguishes them |
| Trip behavior | **latching** | once cutoff engages it must stay off, not auto-retry — re-energizing mid-latchup can destroy the part |
| Timestamp | monotonic "time since firmware boot", in ms | the arbiter records its own receipt time and lines everything up on its single clock |

Thresholds are set during the pre-beam calibration step (§6), not guessed.

---

## 2. Transport

- **Link:** USB-serial is simplest and assumed here. Any byte-stream link the EE
  prefers is acceptable as long as the framing below holds.
- **Framing:** line-delimited JSON — one complete JSON object per line,
  terminated by `\n`. No multi-line objects.
- **Baud:** TBD / configurable. `arbiter/power_reader.py` defaults to `115200`
  (`--power-baud`); set both ends to the same value.
- **Direction:** firmware → arbiter for samples and events; arbiter → firmware
  for the recovery command only (§5).

---

## 3. Periodic sample line (sent at the sample rate)

```json
{"ts_fw": 12345, "current_mA": 812.4, "status": "NOMINAL"}
```

| Field | Type | Units / values | Notes |
|---|---|---|---|
| `ts_fw` | integer | milliseconds since firmware boot | monotonic; never wraps mid-run if avoidable |
| `current_mA` | number | milliamps | measured bus current |
| `status` | string | `"NOMINAL"` \| `"ABNORMAL"` \| `"TRIPPED"` | current state machine value |

The arbiter adds its own `ts_recv` (receipt epoch seconds, float) on ingest and
appends the augmented record to `power/power_log.jsonl`. Firmware does **not**
send `ts_recv`.

State semantics:
- `NOMINAL` — current within bounds.
- `ABNORMAL` — over the absolute and/or di/dt threshold but cutoff not (yet)
  engaged; a candidate "persistent abnormal current".
- `TRIPPED` — cutoff has engaged and **latched**; stays here until recovery (§5).

---

## 4. Out-of-band event line (sent the instant `status` changes)

The moment the state machine changes `status`, emit an event line immediately —
do not wait for the next periodic sample — so the arbiter notices a trip within
one event, not one sample period:

```json
{"ts_fw": 12801, "event": "STATUS_CHANGE", "from": "ABNORMAL", "to": "TRIPPED", "current_mA": 2450.0}
```

`arbiter/power_reader.py` also derives a `STATUS_CHANGE` from any periodic sample
whose `status` differs from the last seen, so an explicit event line is a
latency optimization, not the only path — but it is strongly recommended for
`TRIPPED`, which the arbiter escalates to a `CANDIDATE_SEL` and cross-references
against the same-second heartbeat and compute/memory logs.

---

## 5. Recovery command (arbiter → firmware)

Recovery is a **deliberate, arbiter-issued** action after the team's
cool-down/inspection decision — never automatic.

- The arbiter sends a single, fixed command to unlatch: default `R\n`
  (see `send_recovery_command()` in `arbiter/power_reader.py`).
- On receipt — and only then — the firmware may clear the latch and return to
  `NOMINAL` (subject to current actually being back in bounds).
- The firmware must **ignore** noise/garbage on the line and only act on the
  exact agreed command. Pick the final command byte(s) with the EE and update
  both this doc and `send_recovery_command()`'s `command` default to match.

---

## 6. Calibration (before beam time)

Run the full fixed test image workload (channels 1–4 all running) on the bench,
with no radiation, and have the firmware log nominal current over time. Hand that
profile to the EE so the absolute and di/dt thresholds are set with real margin
above actual running current — not a guess. This is build/test order step 3 in
[`BUILD_PLAN.md`](BUILD_PLAN.md) §7.

---

## 7. Quick reference (arbiter parser expectations)

`arbiter/power_reader.py`:
- parses each line as JSON; non-JSON / non-object lines are skipped,
- stamps `ts_recv`, calls `on_sample` for every sample,
- calls `on_status_change` whenever `status` differs from the previous sample,
- reconnects automatically if the USB-serial device disappears.
