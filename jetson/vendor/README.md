# jetson/vendor — third-party dependencies (git submodules)

This directory holds the third-party tools this test depends on, pinned as **git
submodules** so we track an exact upstream commit without vendoring their source
into our history. Nothing here is authored by this repo.

## Add the submodules

Run these from the repository root **on a machine with internet/GitHub access**:

```bash
git submodule add https://github.com/ComputationalRadiationPhysics/cuda_memtest.git jetson/vendor/cuda_memtest
git submodule add https://github.com/troglobit/watchdogd.git jetson/vendor/watchdogd
```

> **`gpu-burn` was removed on 2026-08-03** — never built or used, and
> `cuda_particles` is the sole GPU compute detector. NASA SMRT was never vendored:
> `mem_check.py` is our own tester using SMRT's moving-inversions method as a
> reference only (see [`../memory/run_smrt.md`](../memory/run_smrt.md)).

After cloning this repo elsewhere, pull the submodule contents with:

```bash
git submodule update --init --recursive
```

## Status of submodules in this checkout

`cuda_memtest` and `watchdogd` are pinned in `.gitmodules` and as gitlink entries.
After cloning this repo elsewhere, populate them with
`git submodule update --init --recursive` — but note that **neither is built or
used** by the campaign: `cuda_particles` covers GPU compute and CuPy
(`mem_check.py` in `target:"gpu"` mode) covers GPU DRAM. They are kept as
references. If a vendored directory is empty in a fresh clone, you simply have not
run the submodule update.

## IMPORTANT: build these on the Jetson, not a dev machine

If you ever do build them, `cuda_memtest` and `watchdogd` **must be built on the
Jetson itself** (or with a matching aarch64 / CUDA cross-toolchain). Their binaries are
tied to the exact GPU architecture (Ampere `sm_87`), CUDA toolkit, and driver
version on that JetPack image. A binary built on an x86 dev box — or even a
different JetPack release — will not run correctly on the DUT. Treat every build
step in the runbooks and [`docs/BUILD_PLAN.md`](../../docs/BUILD_PLAN.md) as
"run on the target."

| Submodule    | Upstream                                                       | Channel | Build notes                              |
|--------------|---------------------------------------------------------------|---------|------------------------------------------|
| `cuda_memtest` | https://github.com/ComputationalRadiationPhysics/cuda_memtest | 2b      | `cmake -DCMAKE_CUDA_ARCHITECTURES=87 ..` — superseded by CuPy, unbuilt |
| `watchdogd`  | https://github.com/troglobit/watchdogd                        | 3a      | autotools; needs libuEv, libite, libConfuse — unbuilt |
