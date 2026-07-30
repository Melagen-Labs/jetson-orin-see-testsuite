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
- [x] **On-target:** built on the Orin Nano, `--generate-golden` produced `data/golden_hashes.txt` (committed); validated by a ~67 min / 6,064-epoch soak with 0 anomalies
- [x] **Decision:** checksum/tolerance policy = bit-exact default (0 false positives over the 6M-iteration soak) — see EXTRACTION_MAP §6
- [ ] Update `docs/BUILD_PLAN.md` §1a: demote gpu-burn to secondary, make cuda_particles the primary GPU compute channel

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
  data/golden_hashes.txt    # golden table — generated on-target, committed (20 hashes)
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

Then commit `data/golden_hashes.txt` and run normally — each checksum step is compared
against the golden hash for that step index; a mismatch is logged as an anomaly, and
NaN/Inf or out-of-bounds positions are logged as secondary signals.

## Build (on the Orin Nano)
```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```
Requires the JetPack CUDA toolkit. **Cannot be built off-target** (aarch64 / SM 8.7);
the code here has not been compiled — it is written for the on-target build.

## What we changed vs. upstream
- All OpenGL/GLUT/cuda_gl_interop code is behind `#ifdef PARTICLES_USE_GL`; the CUDA-only
  `cudaMalloc` paths (already present as the `else` branches of `m_bUseOpenGL`) are used.
- Added `getCudaVel()` accessor; the checksum reads device buffers directly via
  `getCudaPosVBO()` / `getCudaVel()` — **not** the GL-tangled `getArray`/`dumpParticles`
  paths (which read the null `m_dPos` in headless and are left unused).
- Physics kernels (`particles_kernel_impl.cuh`) and constants are untouched.

## Relationship to gpu-burn
`docs/BUILD_PLAN.md` §1a still treats gpu-burn as the primary GPU compute workload. Per
the approved re-prioritization, gpu-burn becomes a **secondary** high-intensity stress /
power profile and this particles workload becomes the primary corruption detector. That
BUILD_PLAN edit is the last remaining checklist item.
