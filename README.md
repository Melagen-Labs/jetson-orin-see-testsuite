# jetson-orin-radtest

Software suite for a **proton-beam Single Event Effect (SEE) radiation test of an
NVIDIA Jetson Orin Nano**. Two machines are involved: the **DUT** (the Jetson,
sitting in the beam, running a fixed/frozen software image) and the **arbiter**
(a separate PC outside the beam, wired to the DUT over Ethernet and to a
power-monitoring firmware board over USB/serial). The arbiter is the system of
record, because it is the one component that keeps running through a DUT hang,
reboot, or latchup. Five monitoring channels detect and time-correlate silent
data corruption, memory faults, loss of responsiveness, autonomous reboots, and
abnormal power / candidate single-event latchup (SEL).

The authoritative design document is **[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)** —
every script and doc here is built to match it.

## ⚠️ Component status (2026-07-30)

**Only the CUDA particle workload is built and verified on hardware. Everything
else in this repository is tentative and untested — scaffolding written to the
design, not yet compiled/run/qualified on the DUT.** Do not treat the tentative
components as working; they are a starting point for the staged build-out in
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

| Component | Status |
|---|---|
| [`jetson/compute/cuda_particles/`](jetson/compute/cuda_particles/) — deterministic CUDA workload (primary GPU SEE detector) | ✅ **Built & verified on the Orin Nano** — bit-exact determinism, fault-injection detection, ~67 min / 6,064-epoch soak (0 anomalies), golden table committed, schema-v1 logging + SEE counter. |
| [`jetson/memory/mem_check.py`](jetson/memory/mem_check.py) — CPU/system RAM pattern tester (channel 2a) | ✅ **Built & verified on the Orin Nano** — fault-injection detection proven, 0 false positives on 2 GB over a bounded run, all records schema-v1 valid. |
| `jetson/vendor/gpu-burn` (secondary GPU stress) | 🟠 Tentative — not built/qualified on DUT |
| `jetson/vendor/cuda_memtest` (GPU memory, channel 2b) | 🟠 Tentative — not built/qualified on DUT |
| `jetson/compute/cpu_sort_check.py` (CPU workload) | 🟠 Tentative — not run on DUT |
| `jetson/heartbeat/` + `arbiter/heartbeat_listener.py` | 🟠 Tentative — scaffolded, untested |
| `jetson/boot_state/` + pstore/ramoops | 🟠 Tentative — scaffolded, untested |
| `arbiter/` correlator, power reader, log pull | 🟠 Tentative — scaffolded, untested |
| Shared JSONL event schema (`docs/EVENT_SCHEMA.md`) | 🟠 Planned — freeze before building the other channels ([BUILD_PLAN §5a](docs/BUILD_PLAN.md)) |
| Operator dashboard (`arbiter/dashboard/`) — live view of all channel inputs/outputs | 🟠 Planned — read-only arbiter-side dashboard over the frozen schema ([BUILD_PLAN §5b](docs/BUILD_PLAN.md)) |
| `firmware/` power board | 🟠 Tentative — owned by EE, not implemented here |

## Monitoring channels

| # | Channel | DUT side | Arbiter side |
|---|---------|----------|--------------|
| 1 | GPU/CPU workload | ✅ **[`cuda_particles`](jetson/compute/cuda_particles/) (primary, verified)**; `gpu-burn` (secondary stress, 🟠 tentative) + [`cpu_sort_check.py`](jetson/compute/cpu_sort_check.py) (CPU, 🟠 tentative) | pulled compute logs |
| 2 | Memory workload | ✅ **[`mem_check.py`](jetson/memory/mem_check.py) (CPU RAM, 2a — verified)** + cuda_memtest (GPU mem, 2b — 🟠 tentative, [runbook](jetson/memory/run_cuda_memtest.md)) | pulled memory logs |
| 3 | Heartbeat | watchdogd (local HW watchdog) + [`heartbeat_sender.py`](jetson/heartbeat/heartbeat_sender.py) (external UDP) | [`heartbeat_listener.py`](arbiter/heartbeat_listener.py) |
| 4 | Boot-state | [`boot_state_logger.py`](jetson/boot_state/boot_state_logger.py) + kernel pstore/ramoops ([setup](docs/PSTORE_SETUP.md)) | pulled boot-state logs + pstore |
| 5 | Power | EE firmware (separate repo) per [interface spec](docs/POWER_FIRMWARE_INTERFACE.md) | [`power_reader.py`](arbiter/power_reader.py) |

The arbiter's [`arbiter_main.py`](arbiter/arbiter_main.py) ties channels 3, 4,
and 5 together and appends every event into one timestamped JSONL correlator file
so you can line up "heartbeat lost at T" against "current TRIPPED at T" against
"reboot logged at T+2s".

## Repository layout

```
jetson-orin-radtest/
  README.md
  docs/
    BUILD_PLAN.md                 # authoritative design doc
    CHANGELOG.md                  # every change, newest first (for review)
    SERVICES.md                   # systemd install + ARMED arming model
    DEPLOYMENT.md                 # 7-DUT fleet: git-clone dev + image-freeze


    POWER_FIRMWARE_INTERFACE.md   # channel-5 firmware<->arbiter contract
    PSTORE_SETUP.md               # channel-4 kernel pstore/ramoops runbook
  jetson/                         # runs on the DUT (Jetson Orin Nano)
    compute/
      cpu_sort_check.py           # CPU checksummed sort workload (1b)
      cpu_sort_check.service
      gpu_burn_patch/             # notes for modifying vendored gpu-burn (1a)
    memory/
      mem_check.py                # CPU/system RAM pattern tester (2a, verified)
      config/mem_check.json       # mem_check run config
      mem_check.service           # systemd unit (2a)
      run_smrt.md                 # SMRT runbook (reference for 2a)
      run_cuda_memtest.md         # cuda_memtest runbook (2b)
    heartbeat/
      heartbeat_sender.py         # external UDP heartbeat (3b)
      heartbeat_sender.service
    boot_state/
      boot_state_logger.py        # boot-event + uptime logger (4)
      boot_state_logger.service           # uptime loop (long-running)
      boot_state_logger-boot.service      # boot event (oneshot)
    vendor/                       # third-party tools as git submodules
    systemd/                      # DUT unit install guide
  arbiter/                        # runs on the arbiter PC (outside the beam)
    arbiter_main.py               # correlator: heartbeat + power + log pulls
    heartbeat_listener.py
    power_reader.py
    pull_logs.sh
    requirements.txt
  firmware/                       # placeholder; firmware owned by the EE
  .gitignore
```

## Quick start (mirrors [BUILD_PLAN.md](docs/BUILD_PLAN.md) §7)

> **Prerequisite — initialize submodules.** The GPU/memory/watchdog tools are
> vendored as git submodules under `jetson/vendor/`. After cloning, run:
> ```bash
> git submodule update --init --recursive
> ```
> `gpu-burn`, `cuda_memtest`, and `watchdogd` are already pinned in `.gitmodules`;
> the command above populates them. (NASA SMRT is added later per
> [jetson/vendor/README.md](jetson/vendor/README.md).) **Build the vendored
> C/CUDA tools on the Jetson itself** (sm_87 / JetPack CUDA), not on a dev machine.

1. **Build phase** — flash/configure the Jetson (JetPack/L4T), install each
   channel, and unit-test it individually on a bench Jetson (no beam). Confirm
   logs land in the expected format at `/var/log/radtest/{compute,memory,boot_state}`.
   The two from-scratch Python workloads have built-in self-tests:
   ```bash
   python3 jetson/compute/cpu_sort_check.py --once --n 100000 --logfile /tmp/cpu_sort.log
   python3 jetson/boot_state/boot_state_logger.py --mode boot --log-dir /tmp/bs
   ```
2. **Integration phase** — run all channels simultaneously for an hours-long
   soak; confirm no resource contention and that the arbiter's heartbeat listener
   and `rsync` pull both work over the real beam-line Ethernet run. Start the
   arbiter with:
   ```bash
   pip install -r arbiter/requirements.txt
   python3 arbiter/arbiter_main.py --dut-host <DUT_IP> --power-serial-port /dev/ttyUSB0
   ```
   Install the DUT services per [jetson/systemd/README.md](jetson/systemd/README.md).
3. **Calibration phase** — with everything running, capture the nominal power
   profile and hand it to the EE for threshold-setting
   ([interface spec](docs/POWER_FIRMWARE_INTERFACE.md) §6).
4. **Image and freeze** — image the eMMC/SD card, hash it; that hash is your
   fixed software image for the campaign.
5. **At-facility phase** — flash the frozen image, run the pre-test checklist
   (heartbeat up, pstore populates on a forced test panic, power firmware nominal),
   then irradiate while watching the arbiter's live correlator output.
6. **Post-test phase** — pull remaining logs, correlate by timestamp across all
   five channels, and re-flash the frozen image before the next run.

## Third-party dependencies

| Channel | Repo | Link |
|---|---|---|
| GPU compute | gpu-burn (modify) | https://github.com/wilicc/gpu-burn |
| CPU compute | none, build from scratch | — |
| CPU/system memory | `mem_check.py` — project-owned (SMRT method as reference) | `jetson/memory/mem_check.py` |
| GPU memory | cuda_memtest | https://github.com/ComputationalRadiationPhysics/cuda_memtest |
| Heartbeat (local/HW watchdog) | watchdogd | https://github.com/troglobit/watchdogd |
| Heartbeat (external/networked) | none, build from scratch | — |
| Boot-state logging | Linux kernel pstore/ramoops (no repo) + custom logger | — |
| Power | existing hardware + EE firmware, interface spec only | — |

Submodules must be initialized (`git submodule update --init --recursive`) before
building on the Jetson. See [jetson/vendor/README.md](jetson/vendor/README.md).

## What still needs a human

- Building/testing the vendored tools on real Jetson hardware (sm_87 / JetPack).
- Applying the gpu-burn Sobel + JSON-logging patch (design in
  [jetson/compute/gpu_burn_patch/](jetson/compute/gpu_burn_patch/)).
- The EE's power firmware implementation of
  [docs/POWER_FIRMWARE_INTERFACE.md](docs/POWER_FIRMWARE_INTERFACE.md).
- On-site kernel/device-tree changes for pstore, applied at flash time
  ([docs/PSTORE_SETUP.md](docs/PSTORE_SETUP.md)).
