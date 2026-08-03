# SEE Test-Suite Validation Summary

Record of the validation performed on 2026-08-01, on the master board
`orin-nano-01` (Tailscale `100.122.15.91`) and in the coordinator unit suite.
Each row is a feature or capability and how it was proven.

| Feature / test | What we exercised | Result |
|---|---|---|
| **Configurable test duration + DUT auto-stop timer** (§6a) | Added `duration_s` (default 100) to `START_TEST`; the DUT arms a `threading.Timer` that auto-stops and summarizes, while a manual STOP cancels it and a new START replaces it. | `validate()` plus the timer schedule/cancel/replace logic verified in isolation; deployed to `orin-nano-01` and confirmed the running service loaded the new code (restarted, 22 `duration_s` references present). |
| **Live SEE panel via log-tailing** (§6b) | New `see_monitor.py` tails the arbiter's pulled log mirror; new "Live SEEs" GUI panel polls every 2.5 s; `--see-log-root` points it at the mirror. | Headless GUI construction + live-poll smoke test passed. Proven end-to-end on real hardware — see the "live panel end-to-end" row. |
| **Offline dump triage** (`see_dump_triage.py`) | Re-hashes each dumped checkpoint against the golden table to localize an upset to its 50-step window and classify it (silent bit corruption / NaN blow-up / out-of-bounds). | On a synthetic bit-flip dump it localized the upset to checkpoint 2, steps [100,150), "silent_bit_corruption." On a real injected `oob` dump it reported steps [450,500), "out_of_bounds" — the exact injection window. |
| **Fault injection** (`--inject bitflip / nan / oob`) | Corrupt one GPU-resident float to induce each compute SEE type on demand, with no beam. | All three modes on `orin-nano-01` produced a real `see_event` + state dump, tagged `injected:true`, exit 2. Subtypes were correct (bitflip→golden mismatch, nan→`finite:false`), and the triage tool pinpointed the injection window exactly. |
| **Chaos mode** (`--chaos`) | Continuous random GPU bit-flips at a configurable per-step probability. | `--chaos-prob 0.02` over 3 epochs produced 3 SEEs, all tagged `chaos:true`. Confirmed the ceiling: it corrupts values (detected) but does not crash the SoC — a whole-board crash remains the beam's domain. |
| **`cuda_shutdown` (crash / restart)** | SIGKILL a running compute instance mid-run, then let it restart. | The restart detected the stale `running.flag` and logged a `sim_fault` / `unclean_restart` record (panel label: "shut down / restarted"). Both detectors were confirmed — the live panel via `sim_fault`, and the CSV summary via the extra `start`-record count. |
| **Live panel — real end-to-end** | Ran chaos on the board, mirrored its log to the laptop, and watched the coordinator's Live SEEs panel. | Real SEEs streamed to the panel in near-real-time over Tailscale — §6b proven on hardware, not just in unit tests. A display fix landed alongside so dumpless `see_event` markers no longer flood the panel. |

## Baseline current run

One-hour **VDD_IN current baseline** on `orin-nano-01`, captured with the
`melagen-jetson-current-baseline` collector (INA3221, one sample / 5 s) while the
**complete DUT stack ran with no SEEs**: `cuda_particles` + `mem_check_gpu`
(started via the real `START_TEST`, no injection/chaos), plus `test_control`,
`heartbeat_sender`, and `boot_state_logger`. This is the expected current envelope
for a clean test — the reference for defining an acceptable range later (with Daniel).

- **Run:** `baseline-noSEE-full-01`, 2026-08-01 17:11:29 → 18:11:27 UTC
  (3597.95 s ≈ 60 min), **720 samples, 0 sensor failures**.
- **Voltage rail:** ~4.97 V, so mean draw ≈ **9.6 W**.

| Metric | Current (mA) |
|---|---|
| Minimum | 1872 |
| Maximum | 2040 |
| Mean | 1923.0 |
| Median | 1912 |
| Rolling avg (~50 s) min / mean / max | 1892.8 / 1922.7 / 1969.6 |

Per-sample data (720 rows): [`baseline_current_noSEE_orin-nano-01_20260801.csv`](baseline_current_noSEE_orin-nano-01_20260801.csv).
No upper-current threshold is defined here — the collector intentionally records
`upper_current_limit_ma: null` / `decision_logic_enabled: false`; this run is the
evidence for choosing that limit, not the limit itself.
