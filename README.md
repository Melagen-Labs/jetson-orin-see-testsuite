# jetson-orin-radtest

Software suite for a **proton-beam Single Event Effect (SEE) radiation test of an
NVIDIA Jetson Orin Nano**. Two machines are involved: the **DUT** (the Jetson,
sitting in the beam, running a fixed/frozen software image) and the **arbiter**
(a separate PC outside the beam, wired to the DUT over a direct Ethernet cable).
The arbiter is the system of record, because it is the one component that keeps
running through a DUT hang, reboot, or latchup. Monitoring channels detect and
time-correlate silent data corruption, memory faults, loss of responsiveness,
autonomous reboots, and abnormal current draw.

The day-to-day procedure is
**[docs/DRYRUN_PIPELINE_TEST.md](docs/DRYRUN_PIPELINE_TEST.md)** (end-to-end run)
and **[docs/FLASH_AND_BRINGUP.md](docs/FLASH_AND_BRINGUP.md)** (board rollout).
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) is the original design doc and the source
of the channel section numbers (§1a, §2b, §3b, §4, §5) that other docs cite; it is
stale in places and kept as a reference, not as instructions.

## Component status (2026-08-03)

`orin-nano-01` is the validated master: all five services deployed and the full
dry-run pipeline passed on 2026-08-02 (chaos run → live SEE panel → results CSV,
then a clean run confirming 0 SEEs). Boards 02–07 are not yet flashed.

| Component | Status |
|---|---|
| [`jetson/compute/cuda_particles/`](jetson/compute/cuda_particles/) — deterministic CUDA workload (primary GPU SEE detector) | ✅ **Built & verified on the Orin Nano** — bit-exact determinism, fault injection + config-driven chaos, ~67 min / 6,064-epoch soak (0 anomalies), golden table committed, schema-v1 logging + SEE counter. |
| [`jetson/memory/mem_check.py`](jetson/memory/mem_check.py) — DRAM pattern tester | ✅ **Verified.** Deployed **GPU-only** (`mem_check_gpu.service`, CuPy) to minimize CPU load; the CPU variant (2a) stays in the file but its unit is not installed. |
| [`jetson/control/control_receiver.py`](jetson/control/control_receiver.py) — arbiter start/stop receiver (TCP 6000) | ✅ **Deployed & exercised** via the coordinator GUI. Two fixes (config ownership on START, racing-STOP retry) are merged but **not yet confirmed on hardware**. |
| [`jetson/heartbeat/heartbeat_sender.py`](jetson/heartbeat/heartbeat_sender.py) — DUT UDP heartbeat, 1 Hz | ✅ **Deployed**, verified streaming over Ethernet. Currently the strongest latchup/SEFI signal. |
| [`jetson/boot_state/`](jetson/boot_state/) — boot-event + uptime loggers | ✅ **Deployed** (loop unit + oneshot, root → `/var/log/radtest/boot_state`). Kernel pstore/ramoops capture is a separate flash-time step ([setup](docs/PSTORE_SETUP.md)). |
| [`arbiter/`](arbiter/) — correlator, log pull, launcher | ✅ **Proven end-to-end** — `python arbiter/start_arbiter.py` brings up the heartbeat listener, log-pull loop, and GUI in one command. |
| Shared JSONL event schema ([`docs/EVENT_SCHEMA.md`](docs/EVENT_SCHEMA.md)) | ✅ **Frozen (v1)** and enforced at runtime by [`shared/event_log.py`](shared/event_log.py). |
| [`jetson/power/current_logger.py`](jetson/power/current_logger.py) — INA3221 current sampler | 🟢 **New, unit-tested, not yet run on hardware.** Backs the coordinator's **Baseline Test** button: full stack, no beam, current CSV out. |
| Current / SEL detection during beam runs (channel 5) | 🟠 **Owned by Ansh and Daniel** — tracked outside this repo. |
| `jetson/vendor/` (`cuda_memtest`, `watchdogd`) | ⚪ Vendored upstream, **unbuilt and unused** — reference only. CuPy superseded `cuda_memtest`. |

**Removed, deliberately:** `gpu-burn` and its patch notes (superseded by
`cuda_particles`), `cpu_sort_check.py` (the campaign does not stress the CPU), and
the `firmware/` power-board stub (no new hardware this campaign). All recoverable
from git history if ever needed.

## Monitoring channels

| # | Channel | DUT side | Arbiter side |
|---|---------|----------|--------------|
| 1 | GPU workload | ✅ **[`cuda_particles`](jetson/compute/cuda_particles/)** (primary detector, verified) | pulled compute logs |
| 2 | Memory workload | ✅ **[`mem_check.py`](jetson/memory/mem_check.py)** in GPU DRAM mode (2b) | pulled memory logs |
| 3 | Heartbeat | [`heartbeat_sender.py`](jetson/heartbeat/heartbeat_sender.py) — 1 Hz UDP | [`heartbeat_listener.py`](arbiter/heartbeat_listener.py) |
| 4 | Boot-state | [`boot_state_logger.py`](jetson/boot_state/boot_state_logger.py) + kernel pstore/ramoops ([setup](docs/PSTORE_SETUP.md)) | pulled boot-state logs + pstore |
| 5 | Current / SEL | [`current_logger.py`](jetson/power/current_logger.py) — INA3221 sampler, run by `BASELINE_TEST` (beam-run SEL detection is 🟠 Ansh and Daniel's, outside this repo) | pulled `power/` CSV + JSONL; [`power_reader.py`](arbiter/power_reader.py) — parser only, not wired up |

Channel 3b (start/stop control, TCP 6000) is
[`control_receiver.py`](jetson/control/control_receiver.py); the arbiter side is the
coordinator GUI in a teammate's repo.

The arbiter's [`arbiter_main.py`](arbiter/arbiter_main.py) ties the channels
together and appends every event into one timestamped JSONL correlator file, so
you can line up "heartbeat lost at T" against "reboot logged at T+2s".

## Repository layout

```
jetson-orin-see-testsuite/
  README.md
  docs/
    DRYRUN_PIPELINE_TEST.md       # end-to-end run procedure (start here)
    FLASH_AND_BRINGUP.md          # board rollout / imaging
    SERVICES.md                   # systemd install + ARMED arming model
    DEPLOYMENT.md                 # 7-DUT fleet: git-clone dev + image-freeze
    EVENT_SCHEMA.md               # frozen JSONL schema v1 (+ event_schema.json)
    PSTORE_SETUP.md               # channel-4 kernel pstore/ramoops runbook
    CONTROL_INTERFACE.md          # arbiter -> DUT START/STOP/BASELINE contract
    SEE_VALIDATION_SUMMARY.md     # what was proven on hardware, and how
    BUILD_PLAN.md                 # original design doc (reference; stale in places)
    CHANGELOG.md                  # milestone log (reference)
    INTEGRATION_TEST.md           # first-contact bring-up (reference)
  shared/
    event_log.py                  # schema-v1 emitter/validator (all channels)
  tests/                          # python -m unittest discover -s tests
    test_event_log.py             # schema v1: validation, round-trip, rejection
    test_current_logger.py        # INA3221 sampler: CSV shape, rolling avg, failures
    test_control_receiver.py      # BASELINE_TEST: validation + sampler lifecycle
  jetson/                         # runs on the DUT (Jetson Orin Nano)
    compute/
      cuda_particles/             # deterministic CUDA workload (1a, primary)
    memory/
      mem_check.py                # DRAM pattern tester (deployed GPU-only, 2b)
      config/mem_check_gpu.json   # GPU-mode run config
      mem_check_gpu.service       # systemd unit (2b)
    control/
      control_receiver.py         # arbiter START/STOP/BASELINE receiver, TCP 6000 (3b)
      test_control.service        # unit name kept; runs control_receiver.py
    power/
      current_logger.py           # INA3221 VDD_IN sampler -> baseline CSV (5)
    heartbeat/
      heartbeat_sender.py         # external UDP heartbeat, 1 Hz (3b)
      heartbeat_sender.service
    boot_state/
      boot_state_logger.py        # boot-event + uptime logger (4)
      boot_state_logger.service           # uptime loop (long-running)
      boot_state_logger-boot.service      # boot event (oneshot)
    vendor/                       # third-party reference tools (unbuilt)
    systemd/                      # DUT unit install guide
  arbiter/                        # runs on the arbiter PC (outside the beam)
    start_arbiter.py              # one-command launcher (listener + pull + GUI)
    arbiter_main.py               # correlator: heartbeat + current + log pulls
    heartbeat_listener.py
    power_reader.py               # current-stream parser (ingest pending retarget)
    pull_logs.sh
    requirements.txt
  scripts/
    setup-board.sh                # install + enable all five services on a board
    fleet.sh                      # fleet-wide build/deploy helper
  .gitignore
```

## Quick start

Run the arbiter and GUI with one command (both repos cloned side by side — see
[docs/DRYRUN_PIPELINE_TEST.md](docs/DRYRUN_PIPELINE_TEST.md)):

```bash
python arbiter/start_arbiter.py
```

Take a **current baseline** (the no-beam reference run): press **Baseline Test** in
the GUI, set the length in minutes, confirm the beam is off. That runs the same
workloads a beam test does — the CUDA particle sim and the GPU RAM tester — while
logging the board's INA3221 `VDD_IN` current, and drops the CSV in the
coordinator's `results/baseline_<N>.csv`. Details, wire contract and the
by-hand equivalent: [docs/CONTROL_INTERFACE.md](docs/CONTROL_INTERFACE.md).

Run the tests (offline, no DUT, standard library only):

```bash
python -m unittest discover -s tests -v
```

Set up a fresh board:

```bash
sudo ARBITER_IP=192.168.1.10 scripts/setup-board.sh 02
```

Then follow [docs/DRYRUN_PIPELINE_TEST.md](docs/DRYRUN_PIPELINE_TEST.md) to prove
the pipeline end to end before beam day, and
[docs/FLASH_AND_BRINGUP.md](docs/FLASH_AND_BRINGUP.md) for the rollout to boards
02–07.

## Third-party dependencies

Full list with pins and rationale in
[docs/DEPENDENCIES.md](docs/DEPENDENCIES.md). Summary:

| Channel | Source |
|---|---|
| GPU compute (1a) | `cuda_particles` — project-owned, adapted from NVIDIA cuda-samples "particles" |
| GPU memory (2b) | `mem_check.py` — project-owned (SMRT method as reference), CuPy runtime |
| Control / heartbeat / boot-state (3b, 4) | project-owned, standard library only |
| Boot-state kernel side (4) | Linux pstore/ramoops — kernel feature, configured at flash time |
| Current / SEL (5) | DUT-side INA3221 collector — separate tool, owned by Ansh and Daniel |

`jetson/vendor/` still carries `cuda_memtest` and `watchdogd` as unbuilt upstream
references; initialize with `git submodule update --init --recursive` only if you
actually want them.

## What still needs a human

- **Channel 5** — current/SEL detection is Ansh and Daniel's; nothing in this repo
  is blocking on it beyond wiring in whatever they deliver.
- **Boards 02–07** — flash from the `orin-nano-01` master per
  [docs/FLASH_AND_BRINGUP.md](docs/FLASH_AND_BRINGUP.md).
- **On-hardware confirmation** of the two merged `control_receiver.py` fixes, on a
  fresh chaos → clean run.
- **Kernel/device-tree changes for pstore**, applied at flash time
  ([docs/PSTORE_SETUP.md](docs/PSTORE_SETUP.md)).
