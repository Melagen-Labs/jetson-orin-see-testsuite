# Jetson Orin Nano — Flash, Bring-Up & Test Manual

Full lifecycle for the Design Build Fly SEE-test fleet: take a bare Jetson Orin
Nano from box → flashed → **our test software installed** → **verified working
over Ethernet**. Also covers cloning that setup onto the other units so you don't
repeat the whole SDK Manager process seven times.

Written after flashing our first board (Orin Nano 8 GB dev kit, **JetPack
6.2.2**). Deep-dive details live in the per-topic docs; this manual is the
ordered path through all of them.

> **The fleet model (why there are two paths).** During development, git is the
> source of truth and each board is a `git pull` clone. For the frozen campaign
> run, one validated master is imaged, hashed, and flashed to all 7 so every DUT
> is provably bit-identical. See [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## 0. End-to-end checklist

A board is "campaign-ready" when all of these are done. Each links to its
section below.

- [ ] **Flash** JetPack 6.2.2 to the board's NVMe ([§1](#1-flash-a-board-from-scratch-sdk-manager)) — *or* clone from an existing board ([§5](#5-cloning-to-additional-units))
- [ ] **First boot / user** — `melagen` account exists, board on the network ([§2](#2-first-boot))
- [ ] **Accounts & log tree** — `radpull` user + arbiter key, `/var/log/radtest` ([§3](#3-one-time-os-setup))
- [ ] **Harden for the beam** — watchdog, fast panic reboot, headless ([§3](#3-one-time-os-setup) → CRASH_RECOVERY)
- [ ] **Deploy our software** — `setup-board.sh NN` (clone, CuPy, build, golden, arm, services) ([§4](#4-deploy-our-software))
- [ ] **Test over Ethernet** — control / heartbeat / log-pull all pass ([§6](#6-test-that-it-works-over-ethernet))

---

## 1. Flash a board from scratch (SDK Manager)

Only needed for the **first** board (or if you're not cloning). For units 2–7,
skip to [§5 cloning](#5-cloning-to-additional-units).

### Host requirements

- An **x86_64 Ubuntu 20.04 or 22.04** machine, **native install — not a VM or
  WSL.** Forum reports (and our own experience) show the USB link dropping
  mid-flash on virtualized hosts. This is the single biggest source of wasted
  hours.
- [NVIDIA SDK Manager](https://developer.nvidia.com/sdk-manager) installed, and
  an NVIDIA developer account to log in with.
- Host packages the flash needs (install up front to avoid mid-flash failures —
  see the [error table](#7-known-errors-and-fixes) for the symptoms each cures):
  ```bash
  sudo add-apt-repository universe && sudo apt update
  sudo apt install -y lbzip2 nfs-kernel-server
  sudo systemctl enable --now nfs-kernel-server
  ```

### Put the Jetson in Force Recovery Mode

1. Power **off** the board.
2. Place a jumper across **J14 pins 9 and 10** (FC REC ↔ GND) on the button
   header. (On the dev kit you can instead hold the **REC** button while applying
   power.)
3. Connect the board to the host with **USB-C**, then apply power.
4. Remove the jumper. Confirm the host sees it: `lsusb | grep -i nvidia` shows an
   `NVIDIA Corp.` device in recovery.

### Flash

In SDK Manager:

1. **Target:** Jetson Orin Nano; **JetPack 6.2.2**.
2. **Uncheck "Host SDK Components."** You only need *Target* components to flash a
   board, and installing host components onto a live-boot/RAM root fails with the
   `/cow` space error (see table).
3. **Storage device: NVMe** (`nvme0n1`) — our boards boot from the SSD, not
   eMMC/SD. This choice must be consistent everywhere; the clone tooling later
   assumes `nvme0n1`.
4. Set the pre-config username to **`melagen`** (with a password — `melagen` is
   the sudo/login account the rest of this manual and all our docs assume), or
   leave oem-config to prompt on first boot and create `melagen` there.
5. Flash. Expect **a couple of hours** including first-boot; most delay is host
   environment quirks, not the transfer.

If anything aborts, find the exact error in the [table below](#7-known-errors-and-fixes)
before retrying — almost every failure is a known host-side issue with a one-line
fix.

---

## 2. First boot

1. Complete oem-config if prompted (locale, and the `melagen` user if not
   pre-set).
2. Get the board on the network. For campaign use we run it over **Tailscale**
   plus a **direct Ethernet** link to the arbiter; for bring-up, WiFi/DHCP is
   fine:
   ```bash
   nmcli device wifi connect "<SSID>" password "<pw>"      # if using WiFi
   sudo tailscale up                                        # optional, for remote access
   ```
3. Note the board's address. Our reference dev board is Tailscale
   `100.122.15.91`; yours will differ.

---

## 3. One-time OS setup

These are the OS-level pieces our software and the arbiter depend on but that a
plain flash does **not** create. Run them once per board (they get captured in
the frozen image, so cloned boards inherit them).

### 3a. Log-pull user (`radpull`)

The arbiter pulls logs as an unprivileged `radpull` user over SSH (key-only).

```bash
sudo useradd -m -s /bin/bash radpull
sudo -u radpull mkdir -p /home/radpull/.ssh && sudo chmod 700 /home/radpull/.ssh
# Install the ARBITER machine's PUBLIC key (per-machine, not per-person):
echo 'PASTE_ARBITER_PUBLIC_KEY' | sudo tee -a /home/radpull/.ssh/authorized_keys
sudo chown -R radpull:radpull /home/radpull/.ssh
sudo chmod 600 /home/radpull/.ssh/authorized_keys
```
> `authorized_keys` is **per board**. When the pull moves to another machine
> (e.g. Daniel's), append *its* pubkey on **every** board. Wire this into
> `setup-board.sh` for the fleet.

### 3b. Log directory tree

```bash
sudo mkdir -p /var/log/radtest/{compute,memory,boot_state}
# Writable by the service user (melagen), readable by radpull for the pull:
sudo chown -R melagen:melagen /var/log/radtest
sudo chmod -R 755 /var/log/radtest
```
(Authoritative per-service user/permission detail: [`SERVICES.md`](SERVICES.md).)

### 3c. Harden for the beam — crash detection & fast restart

Arm the hardware watchdog, make a kernel panic reboot fast, and go headless.
Full rationale and the exact commands are in
[`CRASH_RECOVERY.md`](CRASH_RECOVERY.md); the short version:

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

### 3d. (Optional but recommended) pstore panic capture

So an autonomous reboot under the beam leaves a persistent "why" record. This is
kernel-config + device-tree work done at flash time — see
[`PSTORE_SETUP.md`](PSTORE_SETUP.md).

### 3e. Direct-Ethernet static profile (for the beam room)

The arbiter link is a direct cable with no DHCP, so the DUT gets a static IP.
Full procedure (and the NetworkManager gotchas) in
[`INTEGRATION_TEST.md`](INTEGRATION_TEST.md) Phase 1; the DUT side:
```bash
sudo nmcli con add type ethernet ifname enP8p1s0 con-name radtest-eth ip4 192.168.1.20/24
sudo nmcli con modify radtest-eth ipv4.method manual ipv6.method disabled ipv4.never-default yes
sudo nmcli con up radtest-eth
```
Interface name (`enP8p1s0`) is an example — check yours with `ip -br addr`.

---

## 4. Deploy our software

### The easy path — one script

`scripts/setup-board.sh` does the whole software bring-up: hostname/identity,
clone, GPU deps (CuPy), build the compute channel, generate this board's golden
table, arm the channels, and install+start the three core services.

```bash
ssh melagen@<board>
git clone https://github.com/Reece122/jetson-orin-see-testsuite.git ~/see-testsuite
~/see-testsuite/scripts/setup-board.sh 03      # this board becomes orin-nano-03
```
Re-running is safe (it just pulls, rebuilds, re-arms). Log out/in afterward so the
new hostname takes effect. What it installs and why is documented inline in the
script and in [`DEPLOYMENT.md`](DEPLOYMENT.md).

**Services it starts:** `cuda_particles` (GPU compute), `mem_check_gpu` (GPU DRAM
tester), `test_control` (arbiter start/stop receiver, runs as root). It does
**not** install the CPU memory tester (the campaign is GPU-only by design).

### Per-board identity (the only things that differ between boards)

The code is byte-identical on every DUT; only two things are per-board, both
handled by `setup-board.sh NN`:

1. **Hostname → `jetson_id`.** `orin-nano-01`..`07`; every log line is stamped
   with it (`jetson_id:"auto"` resolves to the hostname).
2. **Golden table.** `golden_hashes.txt` is device+build specific — each board
   generates and verifies its **own** with no beam present. A board whose golden
   doesn't match its peers on identical hardware/build is itself suspect.

### Optional extra services

`setup-board.sh` covers the three core channels. If you also want the
**boot-state logger** (autonomous-reboot evidence) and the **UDP heartbeat
sender**, install those units per [`jetson/systemd/README.md`](../jetson/systemd/README.md),
adjusting their `ExecStart`/paths to the clone layout (`~/see-testsuite/...`).

### Arming model (how a "campaign" starts and stops)

Each channel service is `enable`d but gated by a persistent `ARMED` flag file, so
it only runs during a campaign and **survives reboots** (including crash/watchdog
reboots). `setup-board.sh` arms them. To stand a board down: `rm` the `ARMED`
files. Full model in [`SERVICES.md`](SERVICES.md).

---

## 5. Cloning to additional units

Once **one** board is fully flashed, set up, and validated, replicate it onto the
rest rather than repeating §1–§4 seven times.

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
Manager downloaded to `~/workspace` during our flash).

**Prerequisites**
- The source Jetson and every target **must be the same board revision.** The
  restore script checks this and refuses on a mismatch — even between two boards
  with the same part number from different manufacturing batches.
- Host needs `nfs-kernel-server` installed and running (`sudo service
  nfs-kernel-server start`).
- Disable external-drive automount during the process:
  `systemctl stop udisks2.service`.
- Only **one** Jetson in recovery mode connected at a time.
- **Native Linux host, not a VM/WSL** (USB drops mid-process on virtualized hosts).

**Step 1 — back up the source Jetson** (in Force Recovery Mode, USB-C to host):
```bash
cd ~/workspace/JetPack_6.2.2_Linux_JETSON_ORIN_NANO_TARGETS/Linux_for_Tegra
sudo ./tools/backup_restore/l4t_backup_restore.sh -e nvme0n1 -b jetson-orin-nano-devkit
```
`-e nvme0n1` = rootfs on NVMe. Produces images in `tools/backup_restore/images/`.

**Step 2 — restore to a new Jetson** (new board in Force Recovery Mode: jumper
J14 9/10, USB-C, power cycle, remove jumper):
```bash
sudo ./tools/backup_restore/l4t_backup_restore.sh -e nvme0n1 -r jetson-orin-nano-devkit
```
This writes the images to the new board's NVMe **and reapplies the
board-specific boot configuration** — the part a raw `dd` would have missed.

**After restoring each clone,** it carries the source board's hostname and golden
table, so re-run the per-board identity step:
```bash
~/see-testsuite/scripts/setup-board.sh NN   # fixes hostname + regenerates this board's golden
```

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

## 6. Test that it works (over Ethernet)

Full procedure with copy-paste commands (and the Windows-arbiter quirks) is in
[`INTEGRATION_TEST.md`](INTEGRATION_TEST.md). This is the ordered summary; run it
after §4 on each board. **Any laptop can stand in as the arbiter** with the Python
snippets in that doc — swap in the real arbiter later.

### Topology

```
Jetson (enP8p1s0)  192.168.1.20/24  <--- Ethernet cable --->  Arbiter  192.168.1.10/24
```
Direct cable, no DHCP → static IPs on both ends, same /24. Auto-MDIX means a
standard cable works.

### The four checks

| # | Interface | What you do | Pass criterion |
|---|---|---|---|
| **1** | Link | Set both static IPs, `ping` each way | Both replies succeed |
| **2** | Control (TCP 6000) | Arbiter sends `START_TEST` JSON to `192.168.1.20:6000` | Reply `status:"ACCEPTED"`; both services `active`; beam metadata (`run_id/beam_energy/shield_config`) appears in the JSONL logs |
| **3** | Heartbeat (UDP 5555) | Run `heartbeat_sender.py --arbiter-ip 192.168.1.10`; listen on arbiter | One `{boot_id,seq,ts}` per second, `seq` climbing; unplug→stops, replug→resumes |
| **4** | Log pull (SSH) | From arbiter: `rsync -az -e ssh radpull@192.168.1.20:/var/log/radtest/ ./pulled_logs/` | Fresh `.jsonl` files transfer under `radpull`'s key |

### Full dry run (the real thing, end to end)

1. Heartbeat running → arbiter sees the DUT alive.
2. Arbiter sends **START_TEST** → both channels log with beam metadata.
3. Run ~1 min, arbiter **pulls logs** → confirm fresh records.
4. Arbiter sends **STOP_TEST** → channels stop and return a per-run **SEE summary**
   (counts by type) in the ack; heartbeat still alive.

All four checks + the dry run passing = the board is validated and ready.

> **Verify each recovery path too (bench only).** Before trusting a board in the
> beam, confirm it auto-recovers: `echo c | sudo tee /proc/sysrq-trigger` should
> reboot it in ~1 s and — because the channels are armed — bring the workloads
> back automatically. See [`CRASH_RECOVERY.md`](CRASH_RECOVERY.md) §4.

---

## 7. Known errors and fixes

| Error | Likely cause | Fix |
|---|---|---|
| `nvrestore_partitions.sh: ...board model that does not match the current board you're flashing onto` | Source and target boards have different revision strings (e.g. P.1 vs M.2), often from different manufacturing batches | Confirm both boards' revisions match before attempting. Some users `export BOARD_MATCH=true` to bypass, but that skips a real safety check — verify functionality carefully afterward |
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

## 8. Practical recommendations for the team

- **Match board revisions before cloning.** Physically check every target Orin
  Nano module shares the source board's revision marking. Unsure how to read it?
  Flag it and we'll check together rather than guess.
- **Keep the exact `Linux_for_Tegra` BSP folder** (already in `~/workspace` on the
  laptop we flashed from) — the backup/restore tooling must match the JetPack
  version originally flashed.
- **Test backup→restore on one spare board first** before doing the whole batch,
  so revision or NFS issues surface early, not mid-batch.
- **Then validate that spare with §6** over Ethernet before trusting the process
  — a board that flashes isn't a board that works.
- **Budget real time.** Our first single-board flash took multiple hours across
  several failed attempts, almost entirely host-environment quirks (Ubuntu
  version, missing packages, service conflicts), not the flash itself.
  Backup/restore uses the same NFS transfer — expect comparable per-board timing.
- **For the frozen campaign**, don't hand-set up 7 boards — bring up and validate
  one master, image + hash its storage, and flash that identical image to all 7
  (then per board: hostname, regenerate golden, re-arm). See
  [`DEPLOYMENT.md`](DEPLOYMENT.md).

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
