# Running the test channels as systemd services

Each DUT-side workload can run as a **systemd service** so the operating system
keeps it alive without anyone babysitting a terminal. This covers install,
the **ARMED** arming model, and how to stop it. It currently applies to the two
deployed channels — `cuda_particles` (compute, §1a) and `mem_check` in **GPU
DRAM** mode (§2b) — and the same pattern will extend to the others.

> **Memory testing is GPU-only.** The campaign minimizes CPU workload, so only
> the GPU DRAM tester (`mem_check_gpu.service`, `target:"gpu"`) is deployed. The
> CPU/system-RAM tester (§2a) is the same `mem_check.py` with `target:"cpu"` and
> remains in the repo, but its service is **not** installed or enabled.

## What a service gives you

- **Auto-start on boot** — after a power-cycle or a watchdog/crash reboot, the
  workload comes back on its own (critical during a beam run: a radiation-induced
  reboot must not leave a monitoring blind spot).
- **Auto-restart on crash** — `Restart=always` relaunches the process ~2 s after
  it dies while the board stays up. Exit code 2 (corruption/upset observed) also
  restarts, but is recorded in the systemd journal so a suspect run is preserved.
- **Status at a glance** — `systemctl status <unit>` shows `active` / `failed`,
  which is how the arbiter tells "crashed" (process gone) from "stalled"
  (heartbeat frozen) from "corruption" (anomaly logged).

## The ARMED arming model — one-time, persists across reboots

We want the workload to restart after a crash/reboot **during a test campaign**,
but not to run on an ordinary power-on when nobody is testing. A reboot looks
identical to the machine either way, so a persistent flag file draws the line:

- Each unit is `enable`d (wired into boot) **but gated** by
  `ConditionPathExists=<workdir>/ARMED`. It only actually starts when that file
  exists.
- **`touch ARMED` once** to arm. The file lives on disk, so it **survives every
  reboot** — you never run it again. Every boot (including a crash/watchdog
  reboot) then restarts the workload automatically.
- **`rm ARMED` once** to disarm, so ordinary power-ons don't run it.

So there is **no per-boot or per-session command** — one command to arm at the
start of a campaign, one to disarm at the end. Nothing in between.

## Install & arm (run once, on the Jetson)

`sudo` steps must be run by the operator (the DUT requires a password for sudo).

### Compute — `cuda_particles`
```bash
sudo cp /home/melagen/see-testsuite/jetson/compute/cuda_particles/cuda_particles.service /etc/systemd/system/cuda_particles.service
sudo systemctl daemon-reload
sudo systemctl enable cuda_particles.service
touch /home/melagen/see-testsuite/jetson/compute/cuda_particles/ARMED
sudo systemctl start cuda_particles.service
systemctl status cuda_particles.service --no-pager
```
(Needs the committed golden table at `data/golden_hashes.txt`, already in place.)

### GPU DRAM — `mem_check` (`target:"gpu"`, §2b)
Requires CuPy on the board first (see `docs/DEPENDENCIES.md`):
```bash
sudo apt-get install -y python3-pip
python3 -m pip install --user "cupy-cuda12x==13.*" "numpy>=1.22,<1.25"
```
Then install the GPU memory unit:
```bash
sudo cp /home/melagen/see-testsuite/jetson/memory/mem_check_gpu.service /etc/systemd/system/mem_check_gpu.service
sudo systemctl daemon-reload
sudo systemctl enable mem_check_gpu.service
touch /home/melagen/see-testsuite/jetson/memory/ARMED
sudo systemctl start mem_check_gpu.service
systemctl status mem_check_gpu.service --no-pager
```
(In the clone, `mem_check.py` resolves `shared/event_log.py` via `../../shared` —
no `event_log.py` copy needed. The unit sets `HOME` so `python3` finds CuPy in
`~/.local`.)

> The CPU/system-RAM tester (§2a, `mem_check.service`, `target:"cpu"`) is **not**
> deployed — memory testing is GPU-only to minimize CPU workload. To swap a board
> that already runs the 2a unit over to GPU-only:
> ```bash
> sudo systemctl disable --now mem_check.service   # stop + unwire the CPU unit
> ```
> then run the GPU install above.

## Stop / disarm (run once, when done testing)
```bash
rm /home/melagen/<channel>/ARMED
sudo systemctl stop <unit>
```
`rm ARMED` alone stops it from coming back after the next reboot; `systemctl stop`
ends the currently-running instance now. To remove it from boot entirely, also
`sudo systemctl disable <unit>`.

## Two different "heartbeats" — don't conflate them

The word "heartbeat" appears in two unrelated places:

1. **`logs/heartbeat.txt`** (per workload) — a **DUT-local liveness / counter
   snapshot**, overwritten each checksum step/sweep. It carries the live counters
   (`iter`/`epoch`/`see_events` for compute; `sweep`/`upsets` for memory) so an
   operator or the arbiter's log pull can read progress at a glance without
   parsing the whole JSONL. It is **not** a network message.
2. **The external UDP heartbeat** (channel §3b, `heartbeat_sender.py`) — a
   separate, still-tentative mechanism that sends a small packet to the arbiter
   over the network so the arbiter detects loss-of-responsiveness in real time.

The authoritative, per-event record is always the JSONL event log (schema v1);
`heartbeat.txt` is a convenience snapshot, and the §3 UDP heartbeat is the
outgoing liveness signal.
