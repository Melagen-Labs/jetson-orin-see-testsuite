# pstore / ramoops Setup Runbook (channel 4, boot-state)

Standalone runbook for the kernel side of boot-state logging, expanded from
[`BUILD_PLAN.md`](BUILD_PLAN.md) §4. Goal: when the DUT panics or reboots
autonomously under the beam, the kernel leaves a persistent record under
`/sys/fs/pstore/` that survives the reboot and proves *why* it happened. The
[`jetson/boot_state/boot_state_logger.py`](../jetson/boot_state/boot_state_logger.py)
script provides the boot_id + uptime timeline; pstore provides the panic/console
dump the arbiter correlates it against.

> **Status (2026-08-05): verified working on `orin-nano-01`, no flash-time work
> needed.** Contrary to this runbook's original assumption, NVIDIA's stock L4T
> image for the Orin Nano already ships everything: a `ramoops_carveout`
> reserved-memory node in the device tree and the `ramoops` module, which
> auto-loads at boot. A bench `sysrq-c` panic test left a 70 KB
> `dmesg-ramoops-0` ("Panic#1") plus `console-ramoops-0` in `/sys/fs/pstore/`
> after the automatic reboot. The only thing a new board needs is the
> radpull-access tmpfiles rule, which `scripts/setup-board.sh` now installs.

---

## 1. What the stock L4T kernel provides (verified, JetPack 5.15.185-tegra)

```
CONFIG_PSTORE=y
CONFIG_PSTORE_CONSOLE=y          # console log persisted continuously
CONFIG_PSTORE_RAM=m              # ramoops driver as a module (auto-loads)
CONFIG_PSTORE_DEFAULT_KMSG_BYTES=10240
```

Device tree (present on the stock image — check with
`ls /proc/device-tree/reserved-memory/`):

```
ramoops_carveout: compatible "ramoops", status "okay", no-map,
                  2 MB @ 0x2725f0000, record-size 64 KB, console-size 512 KB
```

Boot dmesg proof that it registered:

```
pstore: Registered ramoops as persistent store backend
ramoops: using 0x200000@0x2725f0000, ecc: 0
```

If a future image lacks the carveout, add a `reserved-memory` ramoops node to
the device tree and reflash (the original Option B). Note the old Option A
(`memmap=...` on the kernel command line) is **x86-only** — the parameter does
not exist on arm64, so do not use it on a Jetson.

---

## 2. Per-board setup: let the arbiter's pull user read pstore

The record files are world-readable (0444) but the `/sys/fs/pstore` directory
is 0750 root:root, which blocks `radpull` (and therefore `pull_logs.sh`).
`scripts/setup-board.sh` installs this tmpfiles rule (applies now and on every
boot):

```
# /etc/tmpfiles.d/radtest-pstore.conf
z /sys/fs/pstore 0755 root root -
```

Verify: `sudo -u radpull ls /sys/fs/pstore` must succeed.

---

## 3. Verify it works (bench only)

Force a panic and confirm `/sys/fs/pstore/` populates:

```bash
echo c | sudo tee /proc/sysrq-trigger
```

> **Do this on the bench only — never while a real run is in progress.** It hard
> crashes the machine on purpose. With `panic=1` armed (docs/CRASH_RECOVERY.md)
> the board reboots itself in ~1 s. After it comes back:

```bash
sudo ls -l /sys/fs/pstore/
sudo head /sys/fs/pstore/dmesg-ramoops-0    # expect "Panic#1 ..."
```

Verified on `orin-nano-01` 2026-08-05: back on the network in under ~90 s with
`dmesg-ramoops-0` (70 KB, Panic#1) and `console-ramoops-0` present.

**Retention caveat (important for beam interpretation):** the carveout is plain
DRAM. Records survive panics, warm reboots, and watchdog resets — but **not a
cold power cycle**. An SEL/latchup recovery that cuts power will wipe any
unpulled record, so pull promptly (the periodic arbiter pull does this).

---

## 4. Retrieval during the campaign

`/sys/fs/pstore/*` plus the boot-state JSONL logs are pulled by the arbiter's
periodic rsync — see [`arbiter/pull_logs.sh`](../arbiter/pull_logs.sh) (the
pstore rsync is best-effort and needs the §2 tmpfiles rule on the DUT). "After
ethernet reconnects" is satisfied simply by the next scheduled pull succeeding.

Note: stock systemd ships `systemd-pstore.service`, which on some distros
harvests pstore into `/var/lib/systemd/pstore/` at boot and empties the
originals. On this L4T image it did **not** harvest (records stay in
`/sys/fs/pstore/` — observed after the verification panic), so the pull path
above is correct. If a future image update starts harvesting, point the pull at
`/var/lib/systemd/pstore` instead (`PSTORE_DIR` env var of `pull_logs.sh`).

---

## 5. Housekeeping

pstore has finite reserved space (2 MB). Records persist across reboots until
removed; after the arbiter has pulled and archived a record, clear it so the
next panic has room:

```bash
sudo rm /sys/fs/pstore/dmesg-ramoops-0 /sys/fs/pstore/console-ramoops-0
```

Do this only after the arbiter has copied it off.
