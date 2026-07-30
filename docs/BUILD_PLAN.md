# Jetson Orin Nano Proton Beam SEE Test — Software Build Plan
This is a from-scratch, step-by-step build plan for the five-channel monitoring system, organized to match your table. GPU and CPU are split into two separate builds under the shared "GPU/CPU workload" row, as requested. The power row is written as a firmware/software interface spec for your EE rather than a hardware shopping list, since you already have hardware and don't want to source more.
---
## 0. Shared architecture (build this first, everything else plugs into it)
Two machines are involved:
- **DUT**: the Jetson Orin Nano, running the fixed test image, physically at the beam line.
- **Arbiter**: a separate PC or SBC outside the beam, wired to the DUT over Ethernet (and to the power firmware over USB/serial). This is your single source of truth, since it's the one component that keeps running through a DUT hang, reboot, or SEL.
Steps:
1. Flash and configure the Jetson (JetPack/L4T), install all test software (sections 1 to 4 below), then **image the eMMC/SD card** (e.g. `dd` or NVIDIA's flash-image backup tool) and record its checksum. This is your "fixed software image": every run starts from this exact, hashed image so a result is reproducible and you can always re-flash back to a known-good state between runs.
2. On the arbiter, create one directory per DUT-under-test with subfolders `heartbeat/`, `boot_state/`, `memory/`, `compute/`, `power/`. Every channel below ultimately writes a timestamped log that lands here, either pushed live (heartbeat, power) or pulled after reconnect (boot-state, memory, compute).
3. Write one **arbiter correlator script** (`arbiter_main.py`) that:
   - opens a UDP socket for the heartbeat (section 3),
   - opens a serial connection for the power firmware stream (section 5),
   - on a timer (e.g. every 30 to 60 s, or immediately on reconnect), runs `rsync`/`scp` over SSH to pull the latest log files from the DUT for the memory and compute channels and the boot-state file,
   - appends everything into one common CSV/JSONL file keyed by a shared wall-clock timestamp, so post-test you can line up "heartbeat lost at T" against "current spike at T" against "reboot logged at T+2s."
4. Use a fixed, low-privilege SSH key pair for the arbiter-to-DUT pull so `rsync` works unattended even if the DUT has just rebooted.
---
## 1. GPU/CPU workload
Two independent programs, run concurrently, both logging silent data corruption, crashes, and stalled iterations.
### 1a. GPU (primary): deterministic CUDA particle workload — cuda_particles
Repo: **project-owned**, adapted from `NVIDIA/cuda-samples` `particles` — lives in this repo at `jetson/compute/cuda_particles/`.

This is the **primary** GPU corruption detector. Rationale: a diverse physics workload (FP integration, integer spatial hashing, Thrust radix sort, atomics, irregular/scattered memory access) exercises far more *kinds* of GPU circuitry than a matrix multiply, so a single-event upset in a wider set of functional units actually manifests as a detectable wrong answer. Full reasoning: `jetson/compute/cuda_particles/EXTRACTION_MAP.md` §1.

Steps:
1. Prereqs on the DUT: JetPack CUDA toolkit (`nvcc`), CMake ≥ 3.18, `build-essential`.
2. Build headless for the Orin Nano (Ampere, SM 8.7):
   `cmake -S jetson/compute/cuda_particles -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j`
   (CMake pins `CMAKE_CUDA_ARCHITECTURES=87`; OpenGL is compiled out — `PARTICLES_USE_GL` is never defined.)
3. **Generate the golden reference on the target, once, with no beam:**
   `./cuda_particles --config config/particles.json --generate-golden`
   Commit the resulting `data/golden_hashes.txt`. It is build- and device-specific — do **not** reuse a golden from another machine or build.
4. Run: the workload loops in deterministic **epochs** (reset → known state via `srand(1973)`), checksums the position+velocity buffers every K steps (FNV-1a 64), compares against the golden table, and writes:
   - structured **JSONL** (one record per checksum event; anomalies flagged) to the DUT-local `compute/` log dir first, so records survive an Ethernet outage and are pulled later;
   - a **heartbeat/iteration counter** file each checksum step, so the arbiter can tell "stalled" (counter frozen, process alive) from "crashed" (process gone) from "corruption" (mismatch logged).
   Install `cuda_particles.service` so a crash shows as `failed` and the workload auto-starts on boot. Exit code 2 = corruption observed.
5. **Decision to confirm before freezing the image:** checksum/tolerance policy — bit-exact (default) vs. invariant-only. See `EXTRACTION_MAP.md` §6.

### 1b. GPU (secondary): gpu-burn stress / power profile
Repo: **gpu-burn** — https://github.com/wilicc/gpu-burn
Role: **secondary** — a maximum-intensity thermal/power ("power virus") profile and a dead-simple bit-exact cross-check that the detection/logging pipeline works. It is *not* the primary corruption detector (it keeps the whole GPU busy but exercises only a narrow set of operation types). Run it alongside, or in alternation with, 1a.
Steps:
1. On the DUT: `sudo apt update && sudo apt install git build-essential` (CUDA toolkit comes with JetPack, so you already have `nvcc`/`cublas`).
2. `git clone https://github.com/wilicc/gpu-burn.git && cd gpu-burn`
3. Build for the Orin Nano's GPU (Ampere, compute capability 8.7): `make COMPUTE=87 CUDAPATH=/usr/local/cuda`
4. Out of the box, gpu-burn already does a checksummed CUBLAS matrix-multiply loop and compares against a reference matrix, printing `FAULTY` if it detects a mismatch. Two modifications to make before beam time:
   - Replace the stdout "FAULTY" print with a structured log line (timestamp, iteration number, expected vs. actual checksum) written to a file in the DUT's local `compute/` log folder, since you need this to survive an Ethernet outage and be pulled later.
   - Add a heartbeat-style **iteration counter file** written once per loop, so the arbiter can tell "stalled iteration" (file stopped updating, process still alive) apart from "crashed" (process gone) apart from "corruption" (checksum mismatch logged).
### 1c. CPU: checksummed sort workload
No existing repo fits this narrowly, so write it from scratch (roughly 60 lines of Python or C). Structure it the same way NASA's SMRT structures `test_ram.py`, so your log format is consistent:
```python
# cpu_sort_check.py
import time, random, hashlib, json
SEED = 12345
N = 2_000_000
LOGFILE = "/var/log/radtest/compute/cpu_sort.log"
def make_reference():
    random.seed(SEED)
    data = [random.randint(0, 2**31) for _ in range(N)]
    expected = sorted(data)
    expected_hash = hashlib.sha256(str(expected).encode()).hexdigest()
    return data, expected_hash
data, expected_hash = make_reference()
iteration = 0
while True:
    result = sorted(data)  # or your own sort implementation
    actual_hash = hashlib.sha256(str(result).encode()).hexdigest()
    iteration += 1
    if actual_hash != expected_hash:
        log_event("SDC_DETECTED", iteration, expected_hash, actual_hash)
    write_iteration_counter(iteration)  # separate small file, updated every pass
    time.sleep(0.1)
```
Run this as a systemd service so a crash is visible (service state goes to `failed`) and gets picked up by the arbiter's periodic status pull. The separate iteration-counter file is what lets you detect "stalled" (process running, counter frozen) versus "crashed" (process gone, `systemctl status` shows failed).
---
## 2. Memory workload
Two programs, covering the CPU-attached and GPU-attached access paths to the Orin Nano's shared LPDDR5.
### 2a. CPU/system RAM: NASA SMRT
Repo: https://github.com/nasa/System_Monitor_for_Radiation_Testing
Steps:
1. `git clone https://github.com/nasa/System_Monitor_for_Radiation_Testing.git`
2. `cd System_Monitor_for_Radiation_Testing/setup && python3 install_tool.py` (installs `psutil` and friends; on the memory-heavy prompts say no unless you're also doing plotting on-device).
3. Edit the user-input section at the top of `py_src/start_tests.py`: set `ram_pct_to_use` (leave headroom, e.g. 80%, since you're also running the sort/compute workloads on the same board), `data_save_interval` (use < 3 s given you're expecting a real particle flux), `test_cycle_time` (0.1 s as the README recommends).
4. This gives you `test_ram.py`'s pattern-write/read/consistency-check loop and its `RAM STATE CHANGE DETECTED` log line, exactly the "pattern write/read and allocation stress; log mismatch address/value" row.
5. You don't need `test_disks.py`/`test_networks.py` for your spec, but there's no harm leaving them running, they're free peripheral-dropout indicators.
### 2b. GPU memory: cuda_memtest
Repo: https://github.com/ComputationalRadiationPhysics/cuda_memtest
Steps:
1. `git clone https://github.com/ComputationalRadiationPhysics/cuda_memtest.git && cd cuda_memtest`
2. `mkdir build && cd build`
3. `cmake -DCMAKE_CUDA_ARCHITECTURES=87 ..` (Orin Nano's Ampere compute capability). If CMake can't find the CUDA compiler, set `CUDACXX=/usr/local/cuda/bin/nvcc` first.
4. `make`
5. **Verify this actually builds on JetPack's CUDA/aarch64 toolchain before you're at the facility.** It was originally written for x86 discrete GPUs; you may need to patch the CMakeLists for the Tegra CUDA install path.
6. Run it in a loop (it's designed as a one-shot memtest, so wrap it in a shell loop or cron-style relauncher), redirecting its per-test pass/fail output (it already reports which of its 11 patterns, e.g. walking-1, moving inversions, failed and at which address) into the same `memory/` log folder as SMRT.
---
## 3. Heartbeat
Two separate mechanisms doing two separate jobs: local self-recovery, and external loss-of-responsiveness detection. Don't conflate them.
### 3a. Local: watchdogd (kicks the hardware watchdog, drives autonomous reboot/recovery)
Repo: https://github.com/troglobit/watchdogd
Steps:
1. Install build deps: libuEv (>= 2.1.0), libite (>= 2.0.1), libConfuse (>= 3.0).
   ```
   git clone https://github.com/troglobit/libuev.git && cd libuev && ./autogen.sh && ./configure && make && sudo make install && cd ..
   git clone https://github.com/troglobit/libite.git && cd libite && ./autogen.sh && ./configure && make && sudo make install && cd ..
   sudo apt install libconfuse-dev
   ```
2. `git clone https://github.com/troglobit/watchdogd.git && cd watchdogd && ./autogen.sh && ./configure && make && sudo make install`
3. Confirm the Jetson exposes `/dev/watchdog` (it should, via the Tegra watchdog driver); point watchdogd's config at it and set a timeout shorter than your expected hang-to-damage window.
4. This is what actually reboots the DUT autonomously when it hangs; the boot-state logging in section 4 is what proves *why* afterward.
### 3b. External: UDP heartbeat to the arbiter
No repo needed, this is intentionally minimal. On the DUT:
```python
# heartbeat_sender.py
import socket, time, json, uuid
ARBITER_IP = "192.168.1.10"
PORT = 5555
BOOT_ID = open("/proc/sys/kernel/random/boot_id").read().strip()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
seq = 0
while True:
    msg = json.dumps({"boot_id": BOOT_ID, "seq": seq, "ts": time.time()})
    sock.sendto(msg.encode(), (ARBITER_IP, PORT))
    seq += 1
    time.sleep(1.0)
```
On the arbiter:
```python
# heartbeat_listener.py
import socket, json, time
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 5555))
sock.settimeout(3.0)
last_seen = time.time()
while True:
    try:
        data, _ = sock.recvfrom(1024)
        last_seen = time.time()
        log(json.loads(data))
    except socket.timeout:
        log_event("HEARTBEAT_LOST", gap=time.time() - last_seen)
```
Run both as systemd services (`heartbeat_sender` on the DUT, `heartbeat_listener` as part of `arbiter_main.py` or standalone) so they survive reboots automatically.
---
## 4. Boot-state logging
No repo, this leans on the kernel's built-in pstore/ramoops mechanism plus one small logger script.
Steps:
1. Check what's already enabled: `zcat /proc/config.gz | grep PSTORE` (or check `/boot/config-$(uname -r)` if `/proc/config.gz` isn't available). You want `CONFIG_PSTORE=y`, `CONFIG_PSTORE_RAM=y`, and ideally `CONFIG_PSTORE_CONSOLE=y` and `CONFIG_PSTORE_FTRACE=y`.
2. If they're missing, you'll need to rebuild the Jetson Linux (L4T) kernel with those options set, then reflash.
3. Reserve the RAM region ramoops needs. Two ways, pick the simpler one that works for your L4T version:
   - **Kernel command line (simpler, try first)**: edit `/boot/extlinux/extlinux.conf`, add to the `APPEND` line something like `memmap=0x100000$0x50000000 ramoops.mem_address=0x50000000 ramoops.mem_size=0x100000 ramoops.record_size=0x10000`, reserving 1 MB at a physical address known to be free on your module, then reboot.
   - **Device tree (more robust, use if the above doesn't stick)**: add a `reserved-memory` node with a child `ramoops` node (`compatible = "ramoops"`, matching `reg`/size/record-size properties) to the Jetson's device tree source, rebuild the DTB, and reflash.
4. After reboot, confirm `/sys/fs/pstore/` populates on a forced panic (`echo c | sudo tee /proc/sysrq-trigger` as a bench test, **not** while anything important is running).
5. Write a tiny logger that runs at boot and periodically:
   ```python
   # boot_state_logger.py
   import time, json
   BOOT_ID = open("/proc/sys/kernel/random/boot_id").read().strip()
   with open("/var/log/radtest/boot_state/boot_log.jsonl", "a") as f:
       f.write(json.dumps({"event": "boot", "boot_id": BOOT_ID, "ts": time.time()}) + "\n")
   while True:
       uptime = float(open("/proc/uptime").read().split()[0])
       with open("/var/log/radtest/boot_state/uptime_log.jsonl", "a") as f:
           f.write(json.dumps({"boot_id": BOOT_ID, "uptime": uptime, "ts": time.time()}) + "\n")
       time.sleep(5.0)
   ```
   Run the boot-event write as a `systemd` unit with `WantedBy=multi-user.target` and `ExecStart` at boot; run the uptime loop as a long-running service.
6. Retrieval: this file, plus `/sys/fs/pstore/*`, is exactly what the arbiter's periodic `rsync` pull (section 0, step 3) grabs once Ethernet is back, "after Ethernet reconnects" is satisfied by that reconnect triggering (or simply not blocking) the next scheduled pull.
---
## 5. Power
Your EE is writing the firmware and you already have hardware, so this section is a **software/data interface spec** for that firmware to implement, not a parts list.
What the firmware needs to measure and report:
- Current samples at a rate fast enough to catch a fast SEL current spike (order of 100 Hz to 1 kHz depending on what your existing sense hardware supports).
- Both an absolute-current threshold and a rate-of-change (di/dt) threshold, since a slow "persistent abnormal current" and a fast SEL spike are different failure signatures and your table distinguishes them.
- A trip/latch status bit: once cutoff engages, it should **latch off** (not auto-retry) until the arbiter explicitly commands recovery, since re-energizing mid-latchup risks destroying the part.
- A synchronized timestamp on every sample and every trip event, ideally just "time since firmware boot," with the arbiter recording its own receipt time so everything lines up on the arbiter's single clock (same reasoning as section 0).
What the firmware should send to the arbiter (over whatever link your EE prefers, USB-serial is simplest):
```
{"ts_fw": <firmware clock, ms>, "current_mA": <value>, "status": "NOMINAL" | "ABNORMAL" | "TRIPPED"}
```
at the sample rate, plus an immediate out-of-band event line the moment status changes, so the arbiter doesn't have to wait for the next periodic sample to notice a trip.
What the arbiter side needs to do (add to `arbiter_main.py`):
1. Open the serial port, read line-delimited JSON, append to `power/power_log.jsonl` with its own receipt timestamp alongside `ts_fw`.
2. On a `status: "TRIPPED"` event, log it as a candidate SEL and cross-reference the same-second heartbeat and compute/memory logs for corroborating evidence (e.g. heartbeat also lost at the same time is a strong SEL/SEFI indicator; heartbeat still present suggests a less severe abnormal-current event).
3. Recovery is a deliberate, arbiter-issued command back to the firmware (e.g. a single serial command byte) after whatever cool-down/inspection your team decides on, not automatic.
One calibration step before beam time: run the fixed test image's full workload (sections 1 to 4 all running) on the bench, with no radiation, and have the firmware log nominal current over time. Hand that profile to your EE so the abnormal/trip thresholds are set with real margin above actual running current rather than a guess.
---
## 6. Repo reference table
| Channel | Repo | Link |
|---|---|---|
| GPU compute (primary) | cuda_particles — project-owned, adapted from NVIDIA `particles` | `jetson/compute/cuda_particles/` |
| GPU compute (secondary/stress) | gpu-burn | https://github.com/wilicc/gpu-burn |
| CPU compute | none, build from scratch | — |
| CPU/system memory | NASA SMRT (`test_ram.py`) | https://github.com/nasa/System_Monitor_for_Radiation_Testing |
| GPU memory | cuda_memtest | https://github.com/ComputationalRadiationPhysics/cuda_memtest |
| Heartbeat (local/HW watchdog) | watchdogd | https://github.com/troglobit/watchdogd |
| Heartbeat (external/networked) | none, build from scratch | — |
| Boot-state logging | Linux kernel pstore/ramoops (no repo) + custom logger script | — |
| Power | existing hardware + EE firmware, interface spec only | — |
---
## 7. Build and test order
1. **Build phase**: implement and unit-test each channel individually on a bench Jetson (no beam), confirm logs are being written in the expected format to the expected paths.
2. **Integration phase**: run all channels simultaneously on the bench for an extended soak (hours), confirm no resource contention (GPU/CPU workload competing with memory workload for bandwidth shouldn't crash anything), confirm the arbiter's `rsync` pull and heartbeat listener both work over your actual Ethernet run to the beam line.
3. **Calibration phase**: with everything running, capture the nominal power profile (section 5) and hand it to your EE for threshold-setting.
4. **Image and freeze**: once everything passes integration, image the eMMC/SD card, hash it, and treat that hash as your "fixed software image" for the actual test campaign.
5. **At-facility phase**: flash the frozen image, run the pre-test checklist (verify heartbeat, verify pstore populates on a forced test panic, verify power firmware reports nominal), then begin irradiation, watching the arbiter's live logs.
6. **Post-test phase**: pull all remaining logs, correlate by timestamp across heartbeat/power/compute/memory/boot-state, and re-flash the frozen image before the next run.
