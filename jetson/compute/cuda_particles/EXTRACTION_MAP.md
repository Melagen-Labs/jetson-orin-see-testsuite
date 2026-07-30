# NVIDIA `particles` Sample — File-by-File Extraction Map

**Deliverable named in the repository review** (§ "Immediate next deliverable"):
a file-by-file map of the NVIDIA particles sample — what each file does, which
functions we need, which OpenGL/UI components can be removed, and what interfaces
must be added for configuration, checksums, and logs.

**Upstream source:** `NVIDIA/cuda-samples`, path
`cpp/2_Concepts_and_Techniques/particles/` (default branch, inspected 2026-07-29).
**Target DUT:** Jetson Orin Nano — Ampere, compute capability **SM 8.7** (the sample
README lists SM 8.7 as supported, so no arch porting is required).

---

## 1. Why this sample, and what it already gives us for free

The review picks the particles sample as the **Priority-1** deterministic CUDA
workload. Inspecting the actual code confirms three things that make it a strong
base — much of what we'd otherwise build is already present:

| Capability we need | Already in the sample |
|---|---|
| **Headless run (no display)** | `runBenchmark()` runs the sim with `-benchmark` and **never touches OpenGL**. |
| **Deterministic initial state** | `initGrid()` calls `srand(1973)` — fixed seed, `CONFIG_GRID` layout by default. |
| **Reference (golden) comparison** | `-file=<ref>.bin` dumps final positions and calls `sdkCompareBin2BinFloat()` against `data/ref_particles.bin`, incrementing `g_TotalErrors`; exit code reflects it. |
| **Real GPU physics load** | `update()` runs integrate → hash → radix-sort (Thrust) → reorder → collide every step. Floating-point, memory traffic, sorting, spatial hashing — exactly the workload profile the NASA TX1 test used. |

So the job is **not** "write a CUDA workload from scratch." It is: **strip the
rendering/UI, convert the one-shot benchmark into a continuous per-iteration
checksummed loop, and bolt on the project's config + logging + stall + recovery
interfaces.**

---

## 2. Core simulation files — KEEP (this is the physics; do not rewrite)

| File | Lines | Role | Action |
|---|---|---|---|
| `particles_kernel_impl.cuh` | 342 | All CUDA **device** kernels: `integrate`, `calcHashD`, `reorderDataAndFindCellStartD`, `collideD`, cell math. This is the actual computation. | **Keep verbatim.** |
| `particles_kernel.cuh` | 59 | `SimParams` struct + physics constants (gravity, damping, collision spring/damping/shear, collider pos/radius, grid). | **Keep.** Values here become part of the frozen, version-controlled run config. |
| `particleSystem_cuda.cu` | 218 | `extern "C"` host wrappers that launch the kernels: `integrateSystem`, `calcHash`, `sortParticles` (Thrust), `reorderDataAndFindCellStart`, `collide`, `setParameters` (`cudaMemcpyToSymbol`), `copyArray*`, `cudaInit`. | **Keep compute paths.** Strip GL-interop functions (see §4). |
| `particleSystem.cuh` | 71 | Declarations for the `extern "C"` wrappers above. | **Keep.** Drop GL-interop decls alongside §4. |
| `particleSystem.cpp` | 494 | Host-side orchestration: `_initialize`, `reset`, `initGrid` (the `srand(1973)` seed), and the `update()` step that chains the 6 pipeline stages. | **Keep core.** GL VBO allocation is already behind an `m_bUseOpenGL` flag — force it false (see §4). |
| `particleSystem.h` | 146 | `ParticleSystem` class declaration. | **Keep.** Remove GL VBO members when `useGL` is compiled out. |

**Determinism note:** the only randomness is `frand()` (jitter) after
`srand(1973)`, so a fixed particle count + grid + seed already produces a
repeatable initial state and repeatable per-iteration output on a given build.
The open question is *cross-build* bit-exactness (Thrust sort + FP reductions may
reorder) — that's the "deterministic result policy" decision in §6.

---

## 3. Entry point — `particles.cpp` (753 lines): SPLIT

This file mixes the parts we want with the parts we're deleting.

**KEEP / adapt:**
- `main()` argument parsing (`-n=`, `-grid=`, `-i=`, `-benchmark`, `-file=`, `-device=`).
- `initParticleSystem()` — constructs `ParticleSystem`; call it with `bUseOpenGL = false`.
- `runBenchmark()` — the headless loop `for (i<iterations) psystem->update(timestep)`, plus the `sdkCompareBin2BinFloat` reference check. **This becomes our continuous checksummed loop.**
- `cudaInit()` path (non-GL branch).
- Constants: `NUM_PARTICLES = 16384`, `timestep = 0.5f` — move into config.

**STRIP (OpenGL / interactive UI — all removable):**
- `initGL()`, `display()`, `reshape()`, `motion()`, `mouse()`, `keyboard()`, `special()`, `idle()`, `computeFPS()`.
- `glutInit`/`glutMainLoop` and every `glut*`/`gl*` call and GL global (`fpsCount`, `fpsLimit`, camera/mouse state, `displayMode`, VBO handles).
- The `cudaGLInit()` branch.

---

## 4. OpenGL / rendering files — STRIP ENTIRELY

| File | Lines | Action |
|---|---|---|
| `render_particles.cpp` | 174 | **Delete** — GL point-sprite renderer, unused headless. |
| `render_particles.h` | 75 | **Delete.** |
| `shaders.cpp` | 65 | **Delete** — GLSL vertex/fragment shader strings. |
| `shaders.h` | 29 | **Delete.** |

Plus, inside the kept files, remove the GL-interop seams (all already guarded by
`m_bUseOpenGL`, so this is deletion, not surgery):
- `particleSystem_cuda.cu`: `cudaGLInit`, `registerGLBufferObject`, `unregisterGLBufferObject`, `mapGLBufferObject`, `unmapGLBufferObject`.
- `particleSystem.cpp`: `createVBO`, `cudaGraphicsGLRegisterBuffer` calls — use the plain `cudaMalloc` path (already the `else` branch when `!m_bUseOpenGL`).

**Build effect:** dropping these removes the `X11 / OpenGL / freeglut / GLEW`
dependencies the sample README lists — a real win for a headless, frozen Jetson image.

---

## 5. Build & data files — ADAPT

| File | Action |
|---|---|
| `CMakeLists.txt` (96 lines) | Rewrite trimmed: drop GLUT/GLEW/GL/X11 `find_package`s and link libs; set `CMAKE_CUDA_ARCHITECTURES=87`; add our `checksum` + `logger` + `config` sources; produce a single headless executable. |
| `data/ref_particles.bin` | The stock golden file matches the **stock** particle count/steps and is under the loose demo tolerance. **Regenerate our own golden on the actual Orin Nano build** at our frozen config, then commit it. Do not trust the upstream binary as our SDC reference. |
| `README.md`, `doc/`, `.vscode/` | Not copied into the project workload (reference only). |

---

## 6. Interfaces we must ADD (project-owned code)

These do **not** exist in the sample and are ours to own. They align the workload
with the shared event schema every channel in this repo uses.

1. **Run configuration (JSON).** `num_particles`, `grid_dim`, `timestep`, `seed`,
   `iterations` (`0` = run until stopped), `checksum_interval`, `tolerance_mode`,
   plus run metadata: `run_id`, `jetson_id`, `beam_energy`, `fluence_source`,
   `shield_config`. Replaces the hardcoded constants + argv flags.
2. **Per-iteration checksum.** After each `update()` (or every *K* steps), copy the
   position **and** velocity buffers to host and hash them (CRC32 / FNV-1a) — an
   *invariant* SDC check that runs continuously, versus the sample's single
   end-of-run reference compare.
3. **Structured logger (JSONL).** One record per checksum event and per anomaly:
   `run_id, iteration, expected, actual, cuda_status, timing, boot_id, beam_energy,
   fluence, shield_config`. Written to the DUT-local `compute/` log dir **first**
   (survives Ethernet loss), matching `docs/BUILD_PLAN.md` §0.
4. **Stall / heartbeat counter.** A tiny iteration-counter file rewritten every pass,
   so the arbiter can tell *stalled* (counter frozen, process alive) from *crashed*
   (process gone) from *corruption* (checksum mismatch logged).
5. **Signal handling.** Graceful `SIGTERM` → flush logs → exit, so the coordinator /
   watchdogd can stop and restart the workload cleanly.
6. **`cuda_particles.service`** systemd unit (mirrors the existing
   `cpu_sort_check.service` pattern in this repo) so a crash shows as `failed` and
   the workload auto-starts on boot.

**Open decision (blocks freezing the checksum policy):** bit-exact hash vs.
numerical tolerance. The stock compare uses `MAX_EPSILON_ERROR = 5.0`,
`THRESHOLD = 0.30` — fine for a visual demo, far too loose to call a single-event
upset. We must pick: (a) bit-exact CRC with a build pinned for reproducibility,
(b) tolerance-band compare against a golden, or (c) physical-invariant checks
(e.g. particle count / energy bounds). See review §9 "Deterministic result policy."

---

## 7. Minimal file set for the standalone workload

```
jetson/compute/cuda_particles/
  particles_main.cpp        # from particles.cpp: main + continuous checksummed loop (GL stripped)
  particleSystem.cpp/.h     # kept, GL VBO path removed
  particleSystem_cuda.cu    # kept compute wrappers, GL-interop removed
  particleSystem.cuh        # kept decls
  particles_kernel.cuh      # kept SimParams + constants
  particles_kernel_impl.cuh # kept device kernels (verbatim)
  checksum.{cu,h}           # NEW — per-iteration pos/vel hashing
  logger.{cpp,h}            # NEW — JSONL structured logging to local compute/ dir
  config.{cpp,h}            # NEW — JSON run config + run/beam metadata
  CMakeLists.txt            # trimmed, headless, SM 8.7
  cuda_particles.service    # NEW — systemd unit
  data/ref_particles.bin    # regenerated golden (committed after first Orin build)
```

Dropped from upstream: `render_particles.*`, `shaders.*`, `doc/`, `.vscode/`,
GL sections of `particles.cpp`, GL-interop functions.
