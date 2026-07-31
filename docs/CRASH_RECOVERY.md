# Crash Recovery Runbook — detect a downed DUT and restart it fast

How the Jetson recovers itself when the beam knocks it over, and the one-time
board settings that make that recovery **automatic and fast**. Like
[`PSTORE_SETUP.md`](PSTORE_SETUP.md), these settings live on the flashed L4T
image / OS config, **not** in a repo service file — this runbook is the
procedure; apply it on the board and keep the values with your image notes.

> Run these on the **bench, before a campaign**, not during a live run. Board
> must be idle (test stopped). `sudo` steps are run by the operator (`melagen`
> needs the sudo password).

---

## The four failure modes and who catches each

| Failure | What dies | Recovery mechanism | Latency |
|---|---|---|---|
| **Process crash** | one workload exits, board stays up | systemd `Restart=always` on the channel unit | ~1–2 s (`RestartSec`) |
| **Board hang / latchup** | whole Nano frozen, systemd can't act | **hardware watchdog** resets the board (§1) | = watchdog timeout |
| **Kernel panic / oops** | kernel dies | `kernel.panic` timeout → reboot (§2) | ~1 s once §2 is set |
| **Stall** (alive, not progressing) | nothing dies | arbiter sees frozen heartbeat/counter, re-issues start | 1 Hz heartbeat |

The process-crash path is already handled by the channel units
(`Restart=always`, `StartLimitIntervalSec=0`; see
[`docs/SERVICES.md`](SERVICES.md)). After any reboot, the `ARMED` flag files
re-launch the workloads with no operator action. **This runbook closes the two
paths that, as measured, currently hang forever: a hard board hang and a kernel
panic.**

---

## Measured baseline (2026-07-31, board `100.122.15.91`)

```
Boot time     : 15.5 s total (7.86 s kernel + 7.67 s userspace)
                multi-user.target reached at 5.69 s
HW watchdog   : NONE running — /dev/watchdog exists but nothing kicks it
kernel.panic  : 0   (panic hangs forever, never reboots)
panic_on_oops : 1   (good; but with panic=0 it hangs after the oops)
hung_task_*   : absent (CONFIG_DETECT_HUNG_TASK not built — needs a kernel rebuild)
default target: graphical.target (on-board desktop — unused; control is remote)
boot offenders: apt-daily / apt-daily-upgrade running at boot; lvm2-monitor on
                the critical chain
```

The 15.5 s reboot is fine and is **not** the thing to optimize. The gap is that
a hang or panic never triggers a reboot at all.

---

## 1. Arm a hardware watchdog (systemd-driven — no extra daemon)

systemd can drive `/dev/watchdog` itself: it pings the Tegra watchdog on a timer,
and if systemd's own event loop wedges (or the kernel locks up hard enough that
the ping stops), the hardware resets the board. This is simpler than deploying
the vendored `jetson/vendor/watchdogd` and is the standard modern approach.

Create a drop-in so the main `system.conf` stays pristine:

```bash
sudo mkdir -p /etc/systemd/system.conf.d
sudo tee /etc/systemd/system.conf.d/10-radtest-watchdog.conf >/dev/null <<'EOF'
[Manager]
# Hard board-hang recovery: if systemd's loop / the kernel stops pinging
# /dev/watchdog for this long, the Tegra WDT resets the board.
RuntimeWatchdogSec=10s
# Also guard a hung shutdown/reboot so it can't wedge on the way down.
RebootWatchdogSec=2min
EOF

# Apply live (re-executes the systemd manager; does not reboot):
sudo systemctl daemon-reexec
```

Verify it armed:

```bash
sudo journalctl -b | grep -i "watchdog" | head
# expect a line like: "Watchdog running with a timeout of 10s"
```

**Scope / honesty:** this catches a *hard* hang (kernel frozen, systemd loop
stuck). It does **not** catch "board alive but one workload wedged" — that case
stays alive and is caught by the arbiter's stall detection (frozen heartbeat /
counter), which re-issues start over the control channel. Between the two, both
"whole board dead" and "one channel stuck" are covered.

> Tegra WDT note: the Orin's watchdog accepts a range of timeouts; 10 s is well
> within it. If `journalctl` shows the driver clamped the value, keep it at or
> above whatever it reports as the minimum.

---

## 2. Make a panic reboot fast instead of hanging

```bash
sudo tee /etc/sysctl.d/99-radtest.conf >/dev/null <<'EOF'
# A kernel panic reboots after 1 s instead of hanging forever.
kernel.panic = 1
# Turn a recoverable oops into a panic (fast reboot + captured evidence) rather
# than a limping kernel. Already 1 on this board; set explicitly so it survives.
kernel.panic_on_oops = 1
EOF

sudo sysctl --system      # apply now
sysctl kernel.panic kernel.panic_on_oops   # verify: both = 1
```

A panic still leaves its pstore/ramoops dump (see
[`PSTORE_SETUP.md`](PSTORE_SETUP.md)) and its new `boot_id` in
`boot_state/boot_log.jsonl`, so the fast reboot loses no evidence — it just
shrinks the blind window from "forever" to ~1 s + the 15.5 s boot.

---

## 3. Go headless + stop unattended package changes

Control is **remote only** — the Stop button is in the coordinator GUI on the
operator's machine, which reaches the DUT over TCP 6000
([`CONTROL_INTERFACE.md`](CONTROL_INTERFACE.md)); `test_control.service` runs at
`multi-user.target`, before any desktop. Nothing is clicked on the Jetson's own
monitor, so the on-board desktop is pure overhead.

```bash
# Boot headless (takes effect next reboot):
sudo systemctl set-default multi-user.target

# Stop unattended apt from mutating the workload mid-campaign (integrity, not
# just speed): a background upgrade during a beam run could change what's under
# test. Mask the timers and the services.
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer \
                     apt-daily.service apt-daily-upgrade.service
```

Optional — `lvm2-monitor` sits on the boot critical chain (~1.7 s). Mask it
**only if the root filesystem is not LVM** (this board boots from an `nvme0n1p1`
partition, so likely safe — but verify first):

```bash
lsblk                       # confirm no lvm / device-mapper volumes in use
sudo systemctl mask lvm2-monitor.service   # only if the above shows no LVM
```

---

## 4. Verify the whole recovery loop (bench only)

With the test **armed** (`ARMED` flags present) but the beam off, force each
failure and confirm the board comes back and re-launches the workloads:

```bash
# Panic path (§2): hard-crashes on purpose — bench only, never during a run.
echo c | sudo tee /proc/sysrq-trigger
# ...board should reboot in ~1 s + boot, then:
systemctl is-active cuda_particles mem_check_gpu test_control   # expect active
ls /sys/fs/pstore/                                              # expect a dmesg-ramoops-*
```

For the watchdog path (§1) there is no safe "fake a hard hang" one-liner; the
`sysrq` panic above already exercises the reboot-and-re-arm chain, and the
watchdog is confirmed armed by the `journalctl` line in §1.

After a real autonomous reboot during a campaign, the evidence trail is:
`boot_log.jsonl` (new uncommanded `boot_id`) + `uptime_log.jsonl` (bounds the
death) + `/sys/fs/pstore/*` (why), all pulled by the arbiter's periodic rsync.

---

## Summary of one-time changes

| Change | File / command | Effect |
|---|---|---|
| Arm HW watchdog | `system.conf.d/10-radtest-watchdog.conf` + `daemon-reexec` | Hard hang → reset in ~10 s (was: never) |
| Fast panic reboot | `sysctl.d/99-radtest.conf` | Panic → reboot in ~1 s (was: hang forever) |
| Headless | `set-default multi-user.target` | Drop unused desktop |
| No mid-run apt | `mask apt-daily*` | Prevent package changes during a campaign |
| (opt) trim boot | `mask lvm2-monitor` if no LVM | ~1.7 s off boot |
