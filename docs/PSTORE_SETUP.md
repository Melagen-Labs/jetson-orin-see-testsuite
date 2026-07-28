# pstore / ramoops Setup Runbook (channel 4, boot-state)

Standalone runbook for the kernel side of boot-state logging, expanded from
[`BUILD_PLAN.md`](BUILD_PLAN.md) §4. Goal: when the DUT panics or reboots
autonomously under the beam, the kernel leaves a persistent record under
`/sys/fs/pstore/` that survives the reboot and proves *why* it happened. The
[`jetson/boot_state/boot_state_logger.py`](../jetson/boot_state/boot_state_logger.py)
script provides the boot_id + uptime timeline; pstore provides the panic/console
dump the arbiter correlates it against.

> All kernel-config and device-tree changes here happen at **flash time** and
> cannot live in this repo — they are applied to the L4T image directly. This
> runbook is the procedure; keep the actual values you used with your image notes.

---

## 1. Check what the kernel already has

```bash
zcat /proc/config.gz | grep PSTORE
# or, if /proc/config.gz is absent:
grep PSTORE /boot/config-$(uname -r)
```

You want at least:

```
CONFIG_PSTORE=y
CONFIG_PSTORE_RAM=y
```

and ideally also:

```
CONFIG_PSTORE_CONSOLE=y
CONFIG_PSTORE_FTRACE=y
```

If they are missing, rebuild the Jetson Linux (L4T) kernel with those options set
and reflash. (This is one of the "can't live in a repo" items — done at flash
time on the image.)

---

## 2. Reserve the RAM region ramoops needs

Pick the simpler option that sticks for your L4T version.

### Option A — kernel command line (try first)

Edit `/boot/extlinux/extlinux.conf` and append to the `APPEND` line, reserving
1 MB at a physical address known to be free on your module:

```
memmap=0x100000$0x50000000 ramoops.mem_address=0x50000000 ramoops.mem_size=0x100000 ramoops.record_size=0x10000
```

Reboot. Confirm the address is actually free on your specific module before
committing to it.

### Option B — device tree (more robust)

If Option A does not stick, add a `reserved-memory` node with a child `ramoops`
node to the Jetson's device tree source, rebuild the DTB, and reflash:

```dts
reserved-memory {
    #address-cells = <2>;
    #size-cells = <2>;
    ranges;

    ramoops@50000000 {
        compatible = "ramoops";
        reg = <0x0 0x50000000 0x0 0x100000>;   /* 1 MB */
        record-size = <0x10000>;
        console-size = <0x10000>;
    };
};
```

Match `reg`/sizes to a free region on your module.

---

## 3. Verify it works (bench only)

Force a panic and confirm `/sys/fs/pstore/` populates:

```bash
echo c | sudo tee /proc/sysrq-trigger
```

> **Do this on the bench only — never while a real run is in progress.** It hard
> crashes the machine on purpose. After it reboots, look for records:

```bash
ls -l /sys/fs/pstore/
cat /sys/fs/pstore/dmesg-ramoops-0
```

A `dmesg-ramoops-*` (and, if enabled, `console-ramoops-*`) file is your proof
that a future real panic will be captured.

---

## 4. Retrieval during the campaign

`/sys/fs/pstore/*` plus the boot-state JSONL logs are pulled by the arbiter's
periodic rsync — see [`arbiter/pull_logs.sh`](../arbiter/pull_logs.sh). "After
ethernet reconnects" is satisfied simply by the next scheduled pull succeeding.

Note pstore is usually root-readable only; see the pstore note in
`pull_logs.sh` for how to let the low-privilege pull user read it (narrow sudoers
or a udev/tmpfiles perms rule), or pull it in a separate root-authorized step.

---

## 5. Housekeeping

pstore has finite reserved space. Records persist across reboots until removed;
after you have pulled and archived a record, clear it so the next panic has room:

```bash
sudo rm /sys/fs/pstore/dmesg-ramoops-0
```

Do this only after the arbiter has copied it off.
