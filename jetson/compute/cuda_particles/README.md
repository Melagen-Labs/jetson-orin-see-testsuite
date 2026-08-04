# cuda_particles — deterministic CUDA compute workload (Priority 1)

Project-owned home for the **primary deterministic GPU compute channel**, adapted
from the `NVIDIA/cuda-samples` particles sample per the repository review
(Priority-1 repo; "Final recommendation" and "Immediate next deliverable").

## Status
- [x] File-by-file extraction map — see [`EXTRACTION_MAP.md`](EXTRACTION_MAP.md)
- [x] Vendor the minimal source — `src/` (physics) + `third_party/nvidia_common/` (BSD-3 helper headers + LICENSE)
- [x] Strip OpenGL/UI from `src/` — every GL touchpoint guarded behind `#ifdef PARTICLES_USE_GL` (never defined by our CMake)
- [x] Headless `particles_main.cpp` — continuous, epoch-based, per-iteration checksummed loop
- [x] Project-owned modules: `config.*` (JSON), `checksum.*` (FNV-1a), `logger.*` (JSONL), heartbeat, SIGTERM handling
- [x] Trimmed `CMakeLists.txt` (headless, `CMAKE_CUDA_ARCHITECTURES=87`, GL off) + `cuda_particles.service`
- [x] **On-target:** built on the Orin Nano, `--generate-golden` produced `data/golden_hashes.txt` (per-board, git-ignored); validated by a ~67 min / 6,064-epoch soak with 0 anomalies
- [x] **Decision:** checksum/tolerance policy = bit-exact default (0 false positives over the 6M-iteration soak) — see EXTRACTION_MAP §6
- [x] Update `docs/BUILD_PLAN.md` §1a — done 2026-08-03: gpu-burn removed entirely, cuda_particles is the sole GPU compute channel

## Layout
```
cuda_particles/
  particles_main.cpp        # headless main: epoch loop, checksum, log, heartbeat, signals
  config.{h,cpp}            # flat-JSON run config + run/beam metadata
  checksum.{h,cpp}          # FNV-1a 64 over pos+vel; NaN/Inf + bounds invariants
  logger.{h,cpp}            # append-only JSONL, flushed per line, to the DUT-local log dir
  CMakeLists.txt            # headless, SM 8.7, no GL
  cuda_particles.service    # systemd unit (Restart=always)
  config/particles.json     # example config
  src/                      # vendored physics (GL guarded out): particleSystem.{cpp,h,cuh},
                            #   particleSystem_cuda.cu, particles_kernel{,_impl}.cuh
  third_party/nvidia_common/# BSD-3 NVIDIA helper headers + LICENSE
  data/golden_hashes.txt    # golden table — generated on each board (git-ignored, 20 hashes)
  logs/                     # runtime JSONL + heartbeat (created on the DUT)
```

## Determinism / epoch model
`initGrid()` seeds `srand(1973)`, so `reset(CONFIG_GRID)` reproduces a bit-identical
initial state on a fixed build. The workload runs in **epochs** of `epoch_iterations`
steps; at the start of every epoch it resets to that known state. Absent a radiation
upset, the sequence of per-step checksums is therefore identical epoch-to-epoch, and a
**golden table of one epoch's clean hashes** is enough to detect corruption forever
(bounded memory, continuous running). Generate it once on the target hardware:

```bash
./cuda_particles --config config/particles.json --generate-golden
```

Each board generates its own `data/golden_hashes.txt` (git-ignored) and runs normally — each checksum step is compared
against the golden hash for that step index; a mismatch is logged as an anomaly, and
NaN/Inf or out-of-bounds positions are logged as secondary signals.

## Build (on the Orin Nano)
```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```
Requires the JetPack CUDA toolkit. **Cannot be built off-target** (aarch64 / SM 8.7).
Built and verified on the Orin Nano: a 6,064-epoch soak (0 anomalies) plus a 30k-iter
re-verify after the schema-v1 logging change (golden matched, `see_events:0`, exit 0).

## Log format (schema v1)
Every JSONL record carries the shared envelope from `docs/EVENT_SCHEMA.md`:
`schema_version:1`, `ts` (ISO-8601 UTC, ms), `run_id`, `jetson_id`, `channel:"compute"`,
`event`, `status` (`ok`/`anomaly`/`info`), then the payload and beam/run metadata.
Checksum records set `status:"anomaly"` on a mismatch/NaN/out-of-bounds, else `"ok"`.

**Detection = final checkpoint only.** Because every epoch resets to the golden state,
a single upset cascades to the end, so only the **last** checkpoint's hash is compared
to the golden's last hash. An epoch that mismatches (or goes NaN/out-of-bounds) is one
`see_event` (`status:"anomaly"`, cumulative `see_events`, mirrored in `heartbeat.txt`
and the `stop` record). Intermediate checkpoints aren't compared — they're only buffered
for the dump below.

**SEE state dump (offline reconstruction).** On a flagged epoch, the buffered
per-checkpoint trajectory is written to the SSD at
`logs/see_dumps/epoch_<N>_iter_<M>.bin` — raw float32, `dump_checkpoints ×
[pos(count) + vel(count)]` — and the `see_event` record carries `dump`,
`dump_checkpoints`, `dump_stride`, `num_particles`, `floats_per_checkpoint`. A reference
Orin can replay the corrupted state forward and count grouped SEEs (see BUILD_PLAN §1a).
Gated by `save_see_epochs` (default true). The arbiter pulls `logs/` over Ethernet
(tentative until wired); the data is on the SSD regardless.

**Crash handling.** A `logs/running.flag` marker is held while running and removed on a
clean stop. If it's present at startup, the prior instance died abnormally (CUDA abort,
segfault, hang→reboot, power) → logged as a `sim_fault`/`crash` SEE. CUDA errors caught
at the checkpoint memcpy are logged (`sim_fault`/`crash` + `cudaGetErrorString`), dumped,
and exit 2 so `cuda_particles.service` (`Restart=always`, `StartLimitIntervalSec=0`,
`RestartSec=1`) relaunches within ~1 s.

## Inducing SEEs without a beam (validation)

`--inject` (TEST ONLY, off by default) corrupts one float of GPU particle state at
`--inject-at <iter>`, writing to the **device** buffer so it propagates like a real
upset and is caught by the same detector, with a real dump written. Every injected
run tags its `inject` and `see_event` records `"injected":true`, so injected events
can never be mistaken for — or silently pollute — real campaign data.

| Mode | Effect | Detector exercised |
|---|---|---|
| `--inject bitflip` | flips `--inject-bit` (default 22) | `cuda_golden_mismatch` |
| `--inject nan` | writes a quiet NaN | `cuda_nonfinite` (`finite:false`) |
| `--inject oob` | writes `1e6` (\|pos\|≫2) | out-of-bounds (see note) |

```bash
# one epoch, isolated log dir, inject at iter 500 (verified on orin-nano-01):
python3 -c "import json;c=json.load(open('config/particles.json'));c['log_dir']='/tmp/inj';c['iterations']=1000;json.dump(c,open('/tmp/inj.json','w'))"
./build/cuda_particles --config /tmp/inj.json --inject bitflip --inject-at 500
cp data/golden_hashes.txt /tmp/inj/ && python3 tools/see_dump_triage.py --logs /tmp/inj
```

**Random placement:** vary `--inject-index <float 0..count-1>` and `--inject-bit
<0..31>` (script random values) to hit different particles/bits. `count =
num_particles × 4`.

**Chaos mode** (`--chaos`, the random-in-time-and-place cousin): each step, with
probability `--chaos-prob` (default 0.01), flips a random bit of a random GPU float.
`--chaos-seed` (default 1) keeps it repeatable. Produces a continuous stream of
mixed, randomly-placed upsets — stresses the whole detect→dump→report chain and, if a
corrupted value derails a kernel, the CUDA-fault recovery path (→ `sim_fault` →
service restart). Verified on `orin-nano-01`: `--chaos --chaos-prob 0.02` over 3
epochs → 3 SEEs, all `"chaos":true`. **Ceiling (honest):** flipping bits in *valid*
GPU buffers corrupts values (detected) but rarely causes an illegal access, so it
won't reboot/hang the SoC — the GPU MMU protects the rest of the system. A full-board
crash is the beam's domain; chaos validates detection + CUDA-fault recovery, not
whole-system reboot.

Both `--inject` and `--chaos` are **per-invocation CLI flags — never in
`cuda_particles.service`** — so they affect only that one manual run; the next real
test is clean. Both write a loud `synthetic_run` marker at the top of the log and tag
every event `"injected":true` / `"chaos":true`, so synthetic data can never be
mistaken for a real campaign event. Always run them against a throwaway `log_dir`
(the JSONL records and `.bin` dumps persist on disk wherever `log_dir` points).

> **Subtype note.** The live panel / CSV label an SEE by the **final** checkpoint's
> fields with `mismatch` checked first, so an `oob` hit that renormalizes before the
> epoch ends is labeled `cuda_golden_mismatch`, not `cuda_anomaly`. That's consistent
> between panel and CSV by design; the **authoritative** subtype comes from
> `see_dump_triage.py`, which scans every checkpoint (it correctly reported the `oob`
> injection above as `out_of_bounds`, localized to steps [450,500)).

**Other SEE types (no code):**
- `cuda_shutdown` / `mem_tester_restart`: `sudo systemctl kill -s SIGKILL
  cuda_particles.service` mid-run — systemd restarts it, the extra `start` record is
  counted.
- Force **every** epoch to flag: back up `data/golden_hashes.txt`, corrupt its last
  line, restart. Exercises the whole detect→dump→pull→panel→CSV→triage chain live.
  Restore the backup after.

**Whole-SoC random corruption = the beam.** Scribbling random physical memory from
userspace is blocked (`CONFIG_STRICT_DEVMEM`); the honest system-level chaos test is
the beam itself — which is what the heartbeat/power/pstore/boot channels exist to
capture. `--inject` validates the *detectors*; the beam validates the *response*.

## Tuning the epoch length (SEE pile-up control)
Because `see_events` counts *epochs with ≥1 SEE* (not raw upsets), two SEEs in the same
epoch are undercounted by one. The undercount fraction ≈ `(SEE_rate × epoch_seconds) / 2`,
so it only matters if the beam flux is high relative to the epoch window. The current
epoch is ~0.66 s (`epoch_iterations: 1000` at the measured ~1,500 iters/s).

**When to change it:** watch the live `see_events` rate — in `logs/heartbeat.txt` and
across the `see_event` records' `epoch` numbers. As long as SEE-affected epochs are
spaced **> ~50 epochs apart on average (≈ one SEE slower than every ~30 s)** the
undercount stays under ~1% and no change is needed. If you start detecting SEEs **more
often than about one every ~30 s** (affected epochs routinely < ~50 apart), the
same-epoch double-hit fraction climbs and you should shorten the epoch. (General rule:
undercount ≈ `SEE_rate × epoch_seconds / 2`.)

To shorten the epoch window and reduce the chance of two SEEs landing in one epoch,
**lower `epoch_iterations` in [`config/particles.json`](config/particles.json)** (from
the current `1000`; halving it halves the epoch time and the undercount). **Important:**
the golden table holds one hash per
checksum step = `epoch_iterations ÷ checksum_interval`, so **any change to
`epoch_iterations` (or `checksum_interval`) requires regenerating the golden table** on
the Jetson (`./build/cuda_particles --config config/particles.json --generate-golden`)
and re-generating `data/golden_hashes.txt` on each board.

## What we changed vs. upstream
- All OpenGL/GLUT/cuda_gl_interop code is behind `#ifdef PARTICLES_USE_GL`; the CUDA-only
  `cudaMalloc` paths (already present as the `else` branches of `m_bUseOpenGL`) are used.
- Added `getCudaVel()` accessor; the checksum reads device buffers directly via
  `getCudaPosVBO()` / `getCudaVel()` — **not** the GL-tangled `getArray`/`dumpParticles`
  paths (which read the null `m_dPos` in headless and are left unused).
- Physics kernels (`particles_kernel_impl.cuh`) and constants are untouched.

## Relationship to gpu-burn
None — `gpu-burn` was removed from the repo on 2026-08-03. It was never built or
used, and this workload is the sole GPU compute detector. `docs/BUILD_PLAN.md` §1a
still describes the old arrangement and is marked historical.
