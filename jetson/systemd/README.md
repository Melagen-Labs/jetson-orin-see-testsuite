# jetson/systemd — DUT service units

Every long-running DUT workload runs as a `systemd` service so it (a) restarts
across reboots and (b) exposes a `failed` state the arbiter's status pull can
detect (crash vs. stall vs. corruption — see
[`docs/BUILD_PLAN.md`](../../docs/BUILD_PLAN.md) §1b).

## Units in this repo

| Unit file                                   | Source                                  | Type      | Purpose                                            |
|---------------------------------------------|-----------------------------------------|-----------|----------------------------------------------------|
| `compute/cpu_sort_check.service`            | `compute/cpu_sort_check.py`             | simple    | CPU checksummed sort workload (channel 1b)         |
| `heartbeat/heartbeat_sender.service`        | `heartbeat/heartbeat_sender.py`         | simple    | 1 Hz UDP heartbeat to the arbiter (channel 3b)     |
| `boot_state/boot_state_logger.service`      | `boot_state/boot_state_logger.py --mode loop` | simple | uptime timeline logger (channel 4)            |
| `boot_state/boot_state_logger-boot.service` | `boot_state/boot_state_logger.py --mode boot` | oneshot | one boot-event record per power-on (channel 4) |

> The GPU compute workload (`gpu-burn`, channel 1a) and the memory workloads
> (SMRT + cuda_memtest, channel 2) are built from the vendored submodules on the
> Jetson; wrap them in their own units the same way once built (see
> [`../compute/gpu_burn_patch/README.md`](../compute/gpu_burn_patch/README.md)
> and the memory runbooks). The hardware watchdog (channel 3a) is driven by
> `watchdogd`, which installs its own unit.

## Install

The unit files assume the repo is deployed at `/opt/radtest` and logs go to
`/var/log/radtest/...`. Adjust the `ExecStart` paths in each unit if you deploy
elsewhere. Then, on the DUT:

```bash
sudo mkdir -p /var/log/radtest/{compute,memory,boot_state}

# Copy every unit in the repo into place:
sudo cp /opt/radtest/jetson/compute/cpu_sort_check.service /etc/systemd/system/
sudo cp /opt/radtest/jetson/heartbeat/heartbeat_sender.service /etc/systemd/system/
sudo cp /opt/radtest/jetson/boot_state/boot_state_logger.service /etc/systemd/system/
sudo cp /opt/radtest/jetson/boot_state/boot_state_logger-boot.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now cpu_sort_check.service
sudo systemctl enable --now heartbeat_sender.service
sudo systemctl enable --now boot_state_logger.service
sudo systemctl enable --now boot_state_logger-boot.service
```

(Or, from the unit directories, `sudo cp *.service /etc/systemd/system/ &&
sudo systemctl daemon-reload && sudo systemctl enable --now <name>`.)

## Verify

```bash
systemctl status cpu_sort_check heartbeat_sender boot_state_logger boot_state_logger-boot
tail -f /var/log/radtest/compute/cpu_sort.log
```

A unit in `failed` state (rather than `active`) is exactly the signal the
arbiter interprets as a crashed workload. A unit that is `active` while its
iteration-counter file stops advancing is a *stall*.
