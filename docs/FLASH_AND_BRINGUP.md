# Jetson Orin Nano — Replication & Bring-Up Manual

How to go from **the one board we already have working** to the full fleet of 7
validated SEE-test DUTs, and how to verify each one over Ethernet before it's
trusted in the beam.

We have already flashed and set up **board 1** (the dev board, Tailscale
`100.122.15.91`, JetPack **6.2.2**). So the main job now is **replicating that
board onto the other six** — not re-running the multi-hour SDK Manager flash six
more times. Flashing a board from bare metal is still documented, but as
[Appendix A](#appendix-a-flash-a-board-from-scratch-sdk-manager), for the rare
case (reflashing, or rebuilding a master).

> **Two replication strategies.** From the one master you can either
> **(B) clone its storage** onto each new board with NVIDIA's backup/restore tool
> — one flash total, bit-identical results, best for the frozen campaign — or
> **(A) flash each board fresh and `git clone` the software** onto it. This manual
> **leads with (B)** because it avoids repeating the flash and gives the
> provably-identical image the science wants. (A) is the dev-iteration fallback
> and lives in Appendix A + §4.

---

## 0. Where we are and where we're going

- **Done:** 1 of 7 — the dev board (`100.122.15.91`). It becomes the **master**
  and is named **`orin-nano-01`**.
- **To do:** 6 more boards, each an identical, hardened, Ethernet-validated DUT
  (`orin-nano-02` … `orin-nano-07`).
- **The fleet model** (why cloning at all): during development git is the source
  of truth and each board is a `git pull` clone; for the frozen campaign run, one
  validated master is imaged, hashed, and flashed to all boards so every DUT is
  provably bit-identical. See [`DEPLOYMENT.md`](DEPLOYMENT.md).

### Fleet checklist

| Board | Flashed | Master-ready (§1) | Cloned (§2) | Finalized (§3) | Ethernet-tested (§4) |
|---|---|---|---|---|---|
| orin-nano-01 (master) | ✅ | ⬜ verify to spec | n/a | n/a | ⬜ |
| orin-nano-02 … 07 | ⬜ | — | ⬜ | ⬜ | ⬜ |

---

## 1. Get the master (board 1) fully to spec — do this first

**Everything you clone is only as good as the master.** Before imaging board 1,
confirm it satisfies all of the following. Anything missing gets baked into all
six clones, so fix it here, once.

A master-ready board has:

1. **Our software on the clone-model layout** (`~/see-testsuite`, installed via
   `setup-board.sh`), not an ad-hoc hand-built layout.
2. **Beam hardening applied** — watchdog, fast panic reboot, headless.
3. **Log-pull account + log tree** — `radpull` user with the arbiter key, and
   `/var/log/radtest`.
4. **Validated over Ethernet** — the §4 checks pass on the master itself.

> **Verified current state of board 1 (2026-07-31).** Checked directly on
> `100.122.15.91`:
> - ✅ **Already on the clone model** — all three units point at
>   `/home/melagen/see-testsuite/...` (the older scp layout is gone; the
>   `DEPLOYMENT.md` 2026-07-30 snapshot is stale). Nothing to cut over.
> - ✅ **Services correct** — `cuda_particles`, `mem_check_gpu`, `test_control`
>   enabled on clone paths; CPU `mem_check` disabled (GPU-only). All inactive
>   except `test_control` (correct stopped state).
> - ⬜ **Hostname is still `ubuntu`**, not `orin-nano-01` — the per-board identity
>   step (§1a) has not been run yet.
> - ⬜ **Not hardened yet** — `kernel.panic=0`, no watchdog, boots to
>   `graphical.target` (§1c still to do).
>
> So for board 1, §1a and §1c are the remaining work before it's a clean master.

### 1a. Put board 1 on the clone-model software

```bash
ssh melagen@100.122.15.91
git clone https://github.com/Reece122/jetson-orin-see-testsuite.git ~/see-testsuite
~/see-testsuite/scripts/setup-board.sh 01     # names it orin-nano-01, builds, golden, arms, installs services
```
`setup-board.sh` does hostname/identity, clone, GPU deps (CuPy, pinned), builds
the compute channel, generates this board's golden table, arms the channels, and
installs+starts the three core services (`cuda_particles`, `mem_check_gpu`,
`test_control`). Re-running is safe. Details: [`DEPLOYMENT.md`](DEPLOYMENT.md) and
the script's inline comments.

For board 1 specifically, the clone + services are already in place — the main
thing `setup-board.sh 01` fixes is the hostname (currently `ubuntu` → `orin-nano-01`)
and regenerating its golden table. It's safe to just run it.

### 1b. Log-pull account + log tree (if not already present)

```bash
sudo useradd -m -s /bin/bash radpull 2>/dev/null || true
sudo -u radpull mkdir -p /home/radpull/.ssh && sudo chmod 700 /home/radpull/.ssh
echo 'PASTE_ARBITER_PUBLIC_KEY' | sudo tee -a /home/radpull/.ssh/authorized_keys   # per-MACHINE key
sudo chown -R radpull:radpull /home/radpull/.ssh && sudo chmod 600 /home/radpull/.ssh/authorized_keys

sudo mkdir -p /var/log/radtest/{compute,memory,boot_state}
sudo chown -R melagen:melagen /var/log/radtest && sudo chmod -R 755 /var/log/radtest
```
(Authoritative per-service user/permissions: [`SERVICES.md`](SERVICES.md).)

### 1c. Harden for the beam

Full rationale in [`CRASH_RECOVERY.md`](CRASH_RECOVERY.md); the commands:

```bash
# hardware watchdog (hard hang -> reset in ~10 s)
sudo mkdir -p /etc/systemd/system.conf.d
sudo tee /etc/systemd/system.conf.d/10-radtest-watchdog.conf >/dev/null <<'EOF'
[Manager]
RuntimeWatchdogSec=10s
RebootWatchdogSec=2min
EOF
sudo systemctl daemon-reexec

# fast panic reboot instead of hang-forever
sudo tee /etc/sysctl.d/99-radtest.conf >/dev/null <<'EOF'
kernel.panic = 1
kernel.panic_on_oops = 1
EOF
sudo sysctl --system

# headless + no unattended package changes during a run
sudo systemctl set-default multi-user.target
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service
```
Optionally set up pstore panic capture ([`PSTORE_SETUP.md`](PSTORE_SETUP.md)) —
kernel-config/device-tree work, best done on the master before imaging.

### 1d. Validate the master over Ethernet

Run [§4](#4-test-each-board-over-ethernet) against board 1 itself. Only clone a
master that passes.

---

## 2. Clone the master onto boards 2–7

Once board 1 passes §1, replicate its storage. This is the part that avoids
repeating SDK Manager six times.

### Important: what "cloning the SSD" actually means here

The simplest-sounding approach — pulling the NVMe SSD, imaging it with `dd`, and
dropping that image onto another board's SSD — is **not documented or supported by
NVIDIA**, and I could not find a source confirming it works reliably. Multiple
NVIDIA forum threads describe exactly this raw copy failing to boot: a mismatched
PARTUUID causing the kernel to mount the wrong device, or a boot loop after manual
UUID edits.

The reason: an Orin Nano's boot chain depends on data stored in the module's QSPI
firmware and board-specific configuration (BCT), **not just the SSD contents**. A
plain drive image only captures the rootfs partition, not that board-tied boot
data. NVIDIA's cloning tools regenerate/reapply that boot-layer data, which is why
they exist instead of people just using `dd`.

**Bottom line: use one of the two methods below, not a raw drive copy.**

### Method 1 (recommended): `l4t_backup_restore.sh`

NVIDIA's officially documented tool for backing up a full Jetson (including NVMe)
and restoring onto other boards. Described in `README_backup_restore.txt` inside
`Linux_for_Tegra/tools/backup_restore/` of the L4T BSP (the same package SDK
Manager downloaded to `~/workspace` when we flashed board 1).

**Prerequisites**
- The master and every target **must be the same board revision.** The restore
  script checks this and refuses on a mismatch — even between two boards with the
  same part number from different manufacturing batches. **Check this first**
  (see [team recommendations](#5-practical-recommendations-for-the-team)).
- Host needs `nfs-kernel-server` installed and running (`sudo service
  nfs-kernel-server start`).
- Disable external-drive automount during the process:
  `systemctl stop udisks2.service`.
- Only **one** Jetson in recovery mode connected at a time.
- **Native Linux host, not a VM/WSL** (USB drops mid-process on virtualized hosts).

**Step 1 — back up the master** (board 1 in Force Recovery Mode, USB-C to the host
that flashed it):
```bash
cd ~/workspace/JetPack_6.2.2_Linux_JETSON_ORIN_NANO_TARGETS/Linux_for_Tegra
sudo ./tools/backup_restore/l4t_backup_restore.sh -e nvme0n1 -b jetson-orin-nano-devkit
```
`-e nvme0n1` = rootfs on NVMe. Produces images in `tools/backup_restore/images/`.
**Record the image hash** — this is the fixed, reproducible software image for the
campaign ([`DEPLOYMENT.md`](DEPLOYMENT.md), BUILD_PLAN §0).

Force Recovery Mode: power off, jumper **J14 pins 9 & 10** (FC REC ↔ GND) — or
hold **REC** while powering — connect USB-C, power on, remove jumper. Confirm with
`lsusb | grep -i nvidia`.

**Step 2 — restore to each new board** (target in Force Recovery Mode, same way):
```bash
sudo ./tools/backup_restore/l4t_backup_restore.sh -e nvme0n1 -r jetson-orin-nano-devkit
```
This writes the master's images to the new board's NVMe **and reapplies the
board-specific boot configuration** — the part a raw `dd` would have missed.
Repeat for boards 02–07 (one at a time).

### Method 2 (alternative, less reliable for NVMe): `flash.sh -G` clone image

NVIDIA also documents cloning just the rootfs (APP) partition:
```bash
sudo ./flash.sh -r -k APP -G clone.img jetson-orin-nano-devkit-nvme nvme0n1p1
```
Officially documented, but flagged as **secondary**: multiple forum reports
describe it failing on NVMe Orin Nano setups — the erase step during restore can
hang indefinitely, and boards that finish sometimes fail to boot with "root
partition cannot be mounted," needing an inconsistently documented
`--no-systemimg` flag. It appears designed for eMMC and carried over to NVMe
without full reliability. **Use Method 1 unless you have a specific reason.**

---

## 3. Finalize each cloned board

A fresh clone is a byte-for-byte copy of the master, so it comes up **as
`orin-nano-01` with board 1's golden table and SSH keys.** Fix the per-board
identity on each clone before use:

```bash
ssh melagen@<new-board>
~/see-testsuite/scripts/setup-board.sh 0N     # N = 02..07: sets hostname, regenerates THIS board's golden, re-arms
```
`setup-board.sh` re-does exactly the two things that must differ per board:

1. **Hostname → `jetson_id`.** Each board must be `orin-nano-0N` so every log line
   is stamped with the right board (`jetson_id:"auto"` resolves to the hostname).
2. **Golden table.** `golden_hashes.txt` is device+build specific — each board
   must generate and verify its **own** with no beam present. A board whose golden
   doesn't match its peers on identical hardware/build is itself suspect.

Then log out/in for the new hostname to take effect. The `radpull` `authorized_keys`
carried over from the master, so the arbiter key already works; if a *different*
machine will pull from this board, append its pubkey too (keys are per-machine).

---

## 4. Test each board over Ethernet

Full procedure with copy-paste commands (and the Windows-arbiter quirks) is in
[`INTEGRATION_TEST.md`](INTEGRATION_TEST.md). Run this on the **master (§1d)** and
on **every clone (§3)**. **Any laptop can stand in as the arbiter** with the Python
snippets in that doc — swap in the real arbiter later.

### Topology

```
Jetson (enP8p1s0)  192.168.1.20/24  <--- Ethernet cable --->  Arbiter  192.168.1.10/24
```
Direct cable, no DHCP → static IPs on both ends, same /24. Auto-MDIX means a
standard cable works. The DUT static profile (persistent, via NetworkManager):
```bash
sudo nmcli con add type ethernet ifname enP8p1s0 con-name radtest-eth ip4 192.168.1.20/24
sudo nmcli con modify radtest-eth ipv4.method manual ipv6.method disabled ipv4.never-default yes
sudo nmcli con up radtest-eth
```
(Interface name is an example — check with `ip -br addr`. This profile is on the
master, so clones inherit it; no need to redo per board.)

### The four checks

| # | Interface | What you do | Pass criterion |
|---|---|---|---|
| **1** | Link | Set both static IPs, `ping` each way | Both replies succeed |
| **2** | Control (TCP 6000) | Arbiter sends `START_TEST` JSON to `192.168.1.20:6000` | Reply `status:"ACCEPTED"`; both services `active`; beam metadata (`run_id/beam_energy/shield_config`) appears in the JSONL logs |
| **3** | Heartbeat (UDP 5555) | Run `heartbeat_sender.py --arbiter-ip 192.168.1.10`; listen on arbiter | One `{boot_id,seq,ts}` per second, `seq` climbing; unplug→stops, replug→resumes |
| **4** | Log pull (SSH) | From arbiter: `rsync -az -e ssh radpull@192.168.1.20:/var/log/radtest/ ./pulled_logs/` | Fresh `.jsonl` files transfer under `radpull`'s key |

### Full dry run (end to end)

1. Heartbeat running → arbiter sees the DUT alive.
2. Arbiter sends **START_TEST** → both channels log with beam metadata.
3. Run ~1 min, arbiter **pulls logs** → confirm fresh records.
4. Arbiter sends **STOP_TEST** → channels stop and return a per-run **SEE summary**
   (counts by type) in the ack; heartbeat still alive.

All four checks + the dry run passing = the board is validated.

> **Verify the recovery path too (bench only).** Before trusting a board in the
> beam, confirm it auto-recovers: `echo c | sudo tee /proc/sysrq-trigger` should
> reboot it in ~1 s and — because the channels are armed — bring the workloads
> back automatically. See [`CRASH_RECOVERY.md`](CRASH_RECOVERY.md) §4.

---

## 5. Practical recommendations for the team

- **Match board revisions before cloning.** Physically check every target Orin
  Nano module shares the master's revision marking. Unsure how to read it? Flag it
  and we'll check together rather than guess — a mismatch makes `l4t_backup_restore.sh`
  refuse (see the error table).
- **Bring the master fully to spec first (§1), then validate it (§4), then clone.**
  A defect in the master multiplies by six.
- **Test backup→restore on one board first** before doing the whole batch, so
  revision or NFS issues surface early, not mid-batch. Then §4-test that board
  before continuing.
- **Keep the exact `Linux_for_Tegra` BSP folder** (already in `~/workspace` on the
  laptop we flashed board 1 from) — the backup/restore tooling must match the
  JetPack version originally flashed.
- **Record the master image hash** at backup time; re-restoring that same hashed
  image is how you return any board to a known-good state between runs.
- **Budget real time.** Board 1's from-scratch flash took multiple hours across
  several failed attempts, almost entirely host-environment quirks (Ubuntu version,
  missing packages, service conflicts) — see Appendix A's error table.
  Backup/restore uses the same NFS transfer, so expect comparable per-board timing,
  minus the debugging now that the host is sorted.

---

## Appendix A: Flash a board from scratch (SDK Manager)

You only need this to **rebuild the master** or replace a dead board's storage
from bare metal — the normal path for boards 2–7 is cloning (§2).

### Host requirements

- An **x86_64 Ubuntu 20.04 or 22.04** machine, **native install — not a VM or
  WSL** (USB link drops mid-flash on virtualized hosts — our biggest time sink).
- [NVIDIA SDK Manager](https://developer.nvidia.com/sdk-manager) + an NVIDIA
  developer account.
- Host packages, installed up front (each cures a symptom in the [error table](#appendix-b-known-errors-and-fixes)):
  ```bash
  sudo add-apt-repository universe && sudo apt update
  sudo apt install -y lbzip2 nfs-kernel-server
  sudo systemctl enable --now nfs-kernel-server
  ```

### Force Recovery Mode

Power off → jumper **J14 pins 9 & 10** (FC REC ↔ GND), or hold **REC** while
powering → connect USB-C → power on → remove jumper. Confirm: `lsusb | grep -i
nvidia` shows an `NVIDIA Corp.` device.

### Flash (SDK Manager)

1. **Target:** Jetson Orin Nano; **JetPack 6.2.2**.
2. **Uncheck "Host SDK Components"** — you only need *Target* components; host
   components on a live-boot/RAM root cause the `/cow` space error.
3. **Storage device: NVMe** (`nvme0n1`). Must be consistent — the clone tooling
   assumes `nvme0n1`.
4. Pre-config username **`melagen`** (with password — the account all our docs
   assume), or let oem-config prompt on first boot.
5. Flash (~a couple of hours incl. first boot).

### First boot + full setup

Complete oem-config, get on the network (`nmcli device wifi connect …`; optional
`sudo tailscale up`), then run the entire **§1** master-prep sequence on it
(software, accounts/logs, hardening) and **§4** to validate. That produces a
board identical to how board 1 was built.

---

## Appendix B: Known errors and fixes

| Error | Likely cause | Fix |
|---|---|---|
| `nvrestore_partitions.sh: ...board model that does not match the current board you're flashing onto` | Master and target have different revision strings (e.g. P.1 vs M.2), often from different manufacturing batches | Confirm both boards' revisions match before attempting. Some users `export BOARD_MATCH=true` to bypass, but that skips a real safety check — verify functionality carefully afterward |
| `Waiting for target to boot-up... Timeout` during backup/restore | More than one Jetson connected at once, or running from a VM/WSL host | Disconnect all other Jetsons; confirm only one target; run from native Linux |
| Board boots but kernel mounts the wrong storage device regardless of boot order | PARTUUID mismatch between the drive and what the boot config expects (most often the `l4t_initrd_flash.sh` USB/external workflow) | Re-run the intended official cloning method; **don't** hand-edit `extlinux.conf` or partition UUIDs |
| Persistent boot loop / corrupted UEFI variables after manually editing `extlinux.conf` UUID | Manual UUID edits outside NVIDIA's tools | Don't hand-edit boot config. If it happens, reflash from scratch via SDK Manager |
| `E: Unable to locate package lbzip2` during flash | Host missing the `universe` repo or hasn't `apt update`-d | `sudo add-apt-repository universe && sudo apt update && sudo apt install -y lbzip2` |
| `rpcbind: another rpcbind is already running. Aborting` | Ubuntu's default `rpcbind` conflicts with the one the flash script starts for NFS | `sudo systemctl stop rpcbind && sudo systemctl stop rpcbind.socket` before flashing |
| Flash hangs on the NFS rootfs transfer with no error | `nfs-kernel-server` not installed/running on the host | `sudo apt install -y nfs-kernel-server && sudo systemctl enable --now nfs-kernel-server`; confirm `/etc/exports` isn't empty during an active flash |
| SDK Manager: "not enough space on required partitions... on /cow" | Host is a live-boot Ubuntu (RAM-backed root) and Host SDK Components target that RAM disk | Uncheck **Host SDK Components** — you only need Target Components to flash |
| Disk-encrypted source Jetson won't clone cleanly | Not clearly resolved in NVIDIA's forums as of this writing | If any board uses disk encryption, test the full backup/restore cycle on a spare board before relying on it |
| CuPy install pulls numpy 2.x and breaks system SciPy | CuPy 14 upgrades numpy | Pin `cupy-cuda12x==13.*` + `numpy>=1.22,<1.25` (what `setup-board.sh` does; see [`DEPENDENCIES.md`](DEPENDENCIES.md)) |

---

## Sources

- NVIDIA Jetson Linux Developer Guide, Flashing Support: https://docs.nvidia.com/jetson/archives/r35.6.0/DeveloperGuide/SD/FlashingSupport.html
- `README_backup_restore.txt` (NVIDIA L4T BSP, mirrored copy): https://ftp.technexion.com/development_resources/NVIDIA/backup_restore/README_backup_restore.txt
- Connect Tech, "Jetson Cloning using L4T Backup and Restore": https://support.connecttech.com/hc/en-us/articles/33142872284315-Jetson-Cloning-using-L4T-Backup-and-Restore
- Seeed Studio Wiki, "Create Backup and Restore on reComputer": https://wiki.seeedstudio.com/create_backup_and_restore_on_recomputer/
- NVIDIA Developer Forums: "Clone Jetson Orin Nano NVMe and flash to another Jetson Orin Nano": https://forums.developer.nvidia.com/t/clone-jetson-orin-nano-nvme-and-flash-to-another-jetson-orin-nanod/337743
- NVIDIA Developer Forums: "Flashing a cloned system.img Jetson Orin Nano Developer Kit": https://forums.developer.nvidia.com/t/flashing-a-cloned-system-img-jetson-orin-nanon-developer-kit/310579
- NVIDIA Developer Forums: "Model changes cause l4t_backup_restore.sh error": https://nvidia-jetson.piveral.com/jetson-orin-nano/model-changes-cause-l4t_backup_restore-sh-error/
- NVIDIA Developer Forums: "About README_backup_restore.txt" (timeout failures): https://forums.developer.nvidia.com/t/about-readme-backup-restore-txt/227225
- NVIDIA Developer Forums: "Boot Failure on Jetson Orin Nano - UEFI Variables Corrupted": https://forums.developer.nvidia.com/t/boot-failure-on-jetson-orin-nano-uefi-variables-corrupted-os-chain-a-unbootable/362474
- Industrial Monitor Direct, "Fix Jetson Initrd USB Flash Rootfs PARTUUID Mismatch" (third-party, not NVIDIA-official): https://industrialmonitordirect.com/blogs/knowledgebase/resolving-jetson-orin-nano-initrd-usb-flash-partuuid-mismatch
- NVIDIA Developer Forums: "Cloning a disk encryption enabled Orin Nano" (unresolved): https://forums.developer.nvidia.com/t/cloning-a-disk-encryption-enabled-orin-nano/298138
