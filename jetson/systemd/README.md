# jetson/systemd — DUT service units

Every long-running DUT workload runs as a `systemd` service so it (a) restarts
across reboots and (b) exposes a `failed` state the arbiter's status pull can
detect (crash vs. stall vs. corruption — see
[`docs/BUILD_PLAN.md`](../../docs/BUILD_PLAN.md) §1b).

## Units in this repo

| Unit file                                   | Source                                  | Type      | Purpose                                            |
|---------------------------------------------|-----------------------------------------|-----------|----------------------------------------------------|
| `compute/cuda_particles/cuda_particles.service` | `compute/cuda_particles/` (built binary) | simple | deterministic CUDA workload (channel 1a)        |
| `memory/mem_check_gpu.service`              | `memory/mem_check.py` (`target:"gpu"`)  | simple    | GPU DRAM pattern tester (channel 2b)               |
| `control/test_control.service`              | `control/test_control.py`               | simple    | arbiter START/STOP receiver, TCP 6000 (channel 3b) |
| `heartbeat/heartbeat_sender.service`        | `heartbeat/heartbeat_sender.py`         | simple    | 1 Hz UDP heartbeat to the arbiter (channel 3b)     |
| `boot_state/boot_state_logger.service`      | `boot_state/boot_state_logger.py --mode loop` | simple | uptime timeline logger (channel 4)            |
| `boot_state/boot_state_logger-boot.service` | `boot_state/boot_state_logger.py --mode boot` | oneshot | one boot-event record per power-on (channel 4) |

> These five units are what [`scripts/setup-board.sh`](../../scripts/setup-board.sh)
> installs. `cuda_particles` and `mem_check_gpu` are ARMED-gated workloads; the
> other three are always-on monitors. See [`docs/SERVICES.md`](../../docs/SERVICES.md)
> for the arming model.
>
> There is **no CPU workload unit** — the campaign deliberately does not stress the
> CPU, and `cpu_sort_check.py` was removed on 2026-08-03. `gpu-burn` was removed at
> the same time. The hardware watchdog (channel 3a) would be driven by `watchdogd`,
> which is vendored but unbuilt.

## Install

**The normal path is `scripts/setup-board.sh`** — it installs and enables the full
deployed set (`cuda_particles`, `mem_check_gpu`, `test_control`, `heartbeat_sender`,
and both `boot_state_logger` units) in one run. See [`docs/SERVICES.md`](../../docs/SERVICES.md)
for the per-service breakdown and the ARMED arming model. The steps below are the
**manual equivalent** for installing a board by hand.

The fleet deploys from the git clone at `/home/melagen/see-testsuite`, and logs go
to `/var/log/radtest/...` (the log tree is provisioned by the one-time operator step
in [`docs/FLASH_AND_BRINGUP.md`](../../docs/FLASH_AND_BRINGUP.md) §1b). On the DUT:

```bash
# Log tree (idempotent; the operator step in FLASH_AND_BRINGUP.md §1b sets the
# melagen:radlog setgid ownership the arbiter's radpull reader needs):
sudo mkdir -p /var/log/radtest/{compute,memory,boot_state}

# Copy the deployed units into place (clone paths):
sudo cp /home/melagen/see-testsuite/jetson/heartbeat/heartbeat_sender.service /etc/systemd/system/
sudo cp /home/melagen/see-testsuite/jetson/boot_state/boot_state_logger.service /etc/systemd/system/
sudo cp /home/melagen/see-testsuite/jetson/boot_state/boot_state_logger-boot.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now heartbeat_sender.service
sudo systemctl enable --now boot_state_logger.service
sudo systemctl enable --now boot_state_logger-boot.service
```

## Verify

```bash
systemctl status cuda_particles mem_check_gpu test_control heartbeat_sender boot_state_logger boot_state_logger-boot
tail -f /var/log/radtest/compute/compute_log.jsonl
```

A unit in `failed` state (rather than `active`) is exactly the signal the
arbiter interprets as a crashed workload. A unit that is `active` while its
iteration-counter file stops advancing is a *stall*.
