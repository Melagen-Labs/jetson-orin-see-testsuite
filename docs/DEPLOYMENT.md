# Deploying to the Jetson DUT fleet

The campaign will run **7 Jetson Orin Nano DUTs**. This is how code gets onto
them and stays consistent. Two phases, two mechanisms:

| Phase | Mechanism | Why |
|---|---|---|
| **Development** (iterating now) | git is the source of truth; each DUT is a **clone**, updated with `git pull` | one command updates all boards, zero drift |
| **Campaign** (frozen run) | build+validate on one master, **image its storage, hash it, flash to all 7** | every DUT is provably bit-identical (reproducible science) |

Do **not** build/patch on 7 boards by hand, and do not `scp` to each — that does
not scale and drifts. Git for dev, one hashed image for the run.

## One-time: turn a DUT into a clone

**The easy way** — `scripts/setup-board.sh` does the whole bring-up (hostname,
clone, build, per-board golden, arm, install+start services) in one interactive
run on the board:
```bash
ssh melagen@<board>
git clone https://github.com/Reece122/jetson-orin-see-testsuite.git ~/see-testsuite
~/see-testsuite/scripts/setup-board.sh 03    # this board becomes orin-nano-03
```
The manual steps below are exactly what that script automates, if you prefer to
run them by hand.

```bash
ssh melagen@orin-nano-0N
git clone https://github.com/Reece122/jetson-orin-see-testsuite.git ~/see-testsuite
cd ~/see-testsuite
# build the compute channel in place (needs JetPack CUDA):
export PATH=/usr/local/cuda/bin:$PATH
cmake -S jetson/compute/cuda_particles -B jetson/compute/cuda_particles/build -DCMAKE_BUILD_TYPE=Release
cmake --build jetson/compute/cuda_particles/build -j
# generate this board's own golden table (device+build specific):
jetson/compute/cuda_particles/build/cuda_particles \
  --config jetson/compute/cuda_particles/config/particles.json --generate-golden
```
In the clone layout the tools resolve their own paths: `mem_check.py` finds
`shared/event_log.py` via `../../shared`, so **no `event_log.py` copy is needed**
(unlike the old scp deployment). `build/`, `logs/`, `ARMED`, and per-board
`golden_hashes.txt` are git-ignored so they never fight `git pull`.

## Per-DUT identity — set once

Code is identical on every board; only two things differ per DUT:

1. **Hostname → `jetson_id`.** Set each board's hostname to `orin-nano-01`..`07`.
   Both tools take `"jetson_id": "auto"` (the committed default) and resolve it to
   the hostname at startup, so every log line is stamped with the right board and
   the *same config file* is correct everywhere.
   ```bash
   sudo hostnamectl set-hostname orin-nano-0N   # then re-login
   ```
2. **Golden table.** `golden_hashes.txt` is device+build specific, so each board
   generates and verifies its **own** (command above). If a board's golden does
   not match the others on identical hardware/build, that board is itself suspect.

## Updating the fleet during development

From a dev machine with passwordless SSH to the boards:
```bash
scripts/fleet.sh status     # git HEAD + service state on every board
scripts/fleet.sh build      # git pull + rebuild cuda_particles on every board
scripts/fleet.sh restart    # restart the deployed services on every board
```
Override the board list with `HOSTS="orin-nano-01 orin-nano-03" scripts/fleet.sh ...`.

## Services and arming

`setup-board.sh` installs and enables all **five** deployed units — the two
ARMED-gated workloads (`cuda_particles`, `mem_check_gpu`) plus the three always-on
monitors (`test_control`, `heartbeat_sender`, and `boot_state_logger` ×2) — and
`touch`es the ARMED flag for the workloads. Every unit already points at the clone
paths (`/home/melagen/see-testsuite/...`), so there is no per-unit path editing.
See `docs/SERVICES.md` for the per-service install, the ARMED model (which gates the
workloads only — the monitors always run), and the manual equivalent.

## Freezing for the campaign

1. Bring **one** master DUT fully up (clone, build, golden, services, arm) and
   validate every channel.
2. Image its eMMC/SD (`dd` or NVIDIA's backup tool) and **record the image hash**
   — this is the fixed, reproducible software image (BUILD_PLAN §0).
3. Flash that identical image to all 7. Per board: set the hostname, regenerate +
   verify the golden table, re-arm.
4. Re-flash the same hashed image between runs to return to a known-good state.

## Current state (2026-07-30)

The dev board (`melagen@100.122.15.91`) is still the **older standalone scp
deployment** (`~/cuda_particles`, `~/mem_check`), which works and is verified. The
clone model above is validated (the board can `git clone`/`git pull` from GitHub)
and is the path forward; cutting the running services over to the clone paths is a
deliberate one-shot step, not done mid-review to avoid disturbing the proven rig.
