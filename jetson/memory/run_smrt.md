# Runbook — NASA SMRT (CPU/system RAM memory workload, channel 2a)

Covers the CPU-attached path to the Orin Nano's shared LPDDR5. SMRT
(`test_ram.py`) does the pattern write/read + consistency-check loop and emits a
`RAM STATE CHANGE DETECTED` line on a mismatch. Upstream is vendored as a git
submodule; this runbook is the checklist for building and configuring it to
match this repo's log conventions.

Repo: https://github.com/nasa/System_Monitor_for_Radiation_Testing

## 0. Prerequisites

- Do this **on the Jetson** (or a matching aarch64 environment). SMRT is Python,
  but `install_tool.py` pulls native deps (`psutil`) that must match the target.
- Log destination for this repo: `/var/log/radtest/memory/` (same folder the GPU
  memtest writes to), so the arbiter's `rsync` pull grabs both in one shot.

## 1. Initialize the submodule

If the vendored copy is not yet present (see
[`jetson/vendor/README.md`](../vendor/README.md)):

```bash
git submodule add https://github.com/nasa/System_Monitor_for_Radiation_Testing.git \
    jetson/vendor/smrt
git submodule update --init --recursive
```

## 2. Install SMRT's dependencies

```bash
cd jetson/vendor/smrt/setup
python3 install_tool.py
```

- Installs `psutil` and friends.
- On the plotting/analysis prompts, answer **no** unless you specifically want
  on-device plotting — the DUT should spend its cycles on the workload, not on
  matplotlib.

## 3. Configure the user-input section

Edit the user-input block at the top of `py_src/start_tests.py`:

| setting             | value for this test | why                                                            |
|---------------------|---------------------|----------------------------------------------------------------|
| `ram_pct_to_use`    | `80`                | leave headroom — the sort/compute workloads share this board   |
| `data_save_interval`| `< 3` (e.g. `2`)    | real particle flux expected; save often so events aren't lost  |
| `test_cycle_time`   | `0.1`               | the value SMRT's README recommends                             |

## 4. Point the output at this repo's memory log folder

Run SMRT so its logs land in `/var/log/radtest/memory/`. Either set SMRT's output
directory in its config to that path, or launch from there and symlink/redirect,
e.g.:

```bash
mkdir -p /var/log/radtest/memory
cd jetson/vendor/smrt/py_src
python3 start_tests.py 2>&1 | tee -a /var/log/radtest/memory/smrt.log
```

The `RAM STATE CHANGE DETECTED` line (with mismatch address/value) is the row the
test plan's "log mismatch address/value" requirement maps to.

## 5. Optional peripheral indicators

`test_disks.py` / `test_networks.py` are not required by the spec, but leaving
them running is free peripheral-dropout coverage. Skip them if you want to
minimize load.

## 6. Before facility day

- **Verify SMRT actually runs on the JetPack aarch64 Python** with your chosen
  `ram_pct_to_use` for an extended soak (hours) alongside the compute + GPU
  memtest workloads — confirm no OOM and no resource contention crash.
- Confirm `RAM STATE CHANGE DETECTED` lines are being written to
  `/var/log/radtest/memory/` and that the arbiter's pull retrieves them.
