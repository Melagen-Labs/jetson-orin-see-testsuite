# Runbook — cuda_memtest (GPU memory workload, channel 2b)

Covers the GPU-attached path to the Orin Nano's shared LPDDR5. `cuda_memtest`
runs 11 memory patterns (walking-1, moving inversions, etc.) and reports which
pattern failed and at which address. Upstream is vendored as a git submodule;
this runbook is the checklist to build it for the Orin Nano and loop it into this
repo's log convention.

Repo: https://github.com/ComputationalRadiationPhysics/cuda_memtest

## 0. Prerequisites

- Build **on the Jetson** (or a matching aarch64 + JetPack CUDA toolchain). The
  binary is tied to the exact CUDA toolkit and driver on the image.
- CUDA toolkit ships with JetPack, so `nvcc` is at `/usr/local/cuda/bin/nvcc`.
- Log destination for this repo: `/var/log/radtest/memory/` (shared with SMRT).

## 1. Initialize the submodule

If the vendored copy is not yet present (see
[`jetson/vendor/README.md`](../vendor/README.md)):

```bash
git submodule add https://github.com/ComputationalRadiationPhysics/cuda_memtest.git \
    jetson/vendor/cuda_memtest
git submodule update --init --recursive
```

## 2. Build for the Orin Nano (Ampere, sm_87)

```bash
cd jetson/vendor/cuda_memtest
mkdir -p build && cd build
cmake -DCMAKE_CUDA_ARCHITECTURES=87 ..
make
```

If CMake can't find the CUDA compiler, set it explicitly first:

```bash
export CUDACXX=/usr/local/cuda/bin/nvcc
cmake -DCMAKE_CUDA_ARCHITECTURES=87 ..
```

> **Caveat (do this before facility day):** cuda_memtest was written for x86
> discrete GPUs. **Verify it actually builds and runs on JetPack's CUDA/aarch64
> toolchain on the bench.** You may need to patch `CMakeLists.txt` for the Tegra
> CUDA install path. Do not discover this at the beam line.

## 3. Loop it (it is one-shot by design)

cuda_memtest runs its patterns once and exits, so wrap it in a relauncher that
keeps it running for the whole campaign and appends every pass/fail line to the
shared memory log:

```bash
mkdir -p /var/log/radtest/memory
cd jetson/vendor/cuda_memtest/build
while true; do
    echo "=== cuda_memtest run @ $(date -Iseconds) ==="
    ./cuda_memtest --stress   # flags per the tool's --help
    sleep 1
done 2>&1 | tee -a /var/log/radtest/memory/cuda_memtest.log
```

Its per-test output already names which of the 11 patterns failed and the failing
address — that is exactly the "log mismatch address/value" requirement for the
GPU-memory path. Keeping it in the same `/var/log/radtest/memory/` folder as SMRT
means the arbiter's `rsync` pull collects both together.

## 4. Before facility day

- Confirm the loop survives (relauncher restarts it) and that fail lines are
  written to `/var/log/radtest/memory/`.
- Run it concurrently with the SMRT (CPU RAM) and compute workloads during the
  integration soak to confirm no contention crash on the shared LPDDR5.
