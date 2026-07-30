# Operator dashboard (arbiter-side, live) — PLANNED / TENTATIVE

> **Status: not built yet.** This is a design stub for the live dashboard called
> out in [`docs/BUILD_PLAN.md` §5b](../../docs/BUILD_PLAN.md). Build it only after
> the shared event schema (§5a) is frozen and the arbiter correlator is running.

A single-page, **read-only** dashboard that runs on the **arbiter** (outside the
beam) and shows, at a glance, every testing channel's **inputs** (the run
configuration) and **outputs** (live results) during a run.

## Why read-only, arbiter-side
It reads the **same correlator JSONL** that
[`arbiter_main.py`](../arbiter_main.py) already writes — it does **not** connect
to the DUT and **never issues commands**. It cannot interfere with the test or
the power-safety path. If the dashboard crashes, the test is unaffected.

## Depends on the frozen schema
Every panel reads the frozen JSONL event record (`docs/EVENT_SCHEMA.md`, once it
exists). One schema → one parser → a simple renderer. Do not build this against
per-channel ad-hoc formats.

## What it shows
- **Run header (inputs):** `run_id`, `jetson_id`, `beam_energy`, `fluence_source`,
  `shield_config`, frozen image hash, active workloads.
- **Per-channel status tiles (outputs), green/amber/red:**
  - **Compute** — iteration counter, last checksum, cumulative **SEE event count**, exit state
  - **Memory** — pass count, mismatch count
  - **Heartbeat** — alive / stalled / lost + last-seen age
  - **Boot-state** — boot ID, uptime, reboot count
  - **Power** — `current_mA`, NOMINAL / ABNORMAL / TRIPPED
- **Live event feed:** tail of the correlator JSONL, newest first, anomalies highlighted.
- **Headline readout:** cross-section = cumulative SEE events ÷ fluence, updated live.

## How it will be built
- A small Python process tails the correlator JSONL and serves a static HTML page
  that polls a `/state` JSON endpoint at ~1 Hz.
- No cloud, no external services — runs on the lab LAN, self-contained.
- Planned files (not yet present): `server.py` (tail + `/state` endpoint),
  `index.html` (tiles + feed), `state.py` (fold JSONL → current-state snapshot).

## Build order
1. Freeze `docs/EVENT_SCHEMA.md` + `event_schema.json` (BUILD_PLAN §5a).
2. Confirm `arbiter_main.py` writes the correlator JSONL in that schema.
3. Build `state.py` (pure function: list of events → snapshot), unit-test offline.
4. Add `server.py` + `index.html`; verify on a bench run with all channels.
