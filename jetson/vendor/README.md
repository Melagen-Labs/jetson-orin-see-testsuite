# jetson/vendor — third-party dependencies (git submodules)

This directory holds the third-party tools this test depends on, pinned as **git
submodules** so we track an exact upstream commit without vendoring their source
into our history. Nothing here is authored by this repo.

## Add the submodules

Run these from the repository root **on a machine with internet/GitHub access**:

```bash
git submodule add https://github.com/wilicc/gpu-burn.git jetson/vendor/gpu-burn
git submodule add https://github.com/ComputationalRadiationPhysics/cuda_memtest.git jetson/vendor/cuda_memtest
git submodule add https://github.com/troglobit/watchdogd.git jetson/vendor/watchdogd
```

> NASA SMRT (channel 2a) is added the same way when you set it up — see
> [`../memory/run_smrt.md`](../memory/run_smrt.md):
> ```bash
> git submodule add https://github.com/nasa/System_Monitor_for_Radiation_Testing.git jetson/vendor/smrt
> ```

After cloning this repo elsewhere, pull the submodule contents with:

```bash
git submodule update --init --recursive
```

## Status of submodules in this checkout

`gpu-burn`, `cuda_memtest`, and `watchdogd` **were added** when this repo was
scaffolded (GitHub was reachable), and are pinned in `.gitmodules` and as gitlink
entries in the initial commit. After cloning this repo elsewhere, populate them
with `git submodule update --init --recursive`.

NASA SMRT (channel 2a) is **not** added yet — add it with the command in the note
above when you set up the memory workload. If any of the three vendored
directories is empty in a fresh clone, you simply have not run
`git submodule update --init --recursive` yet.

## IMPORTANT: build these on the Jetson, not a dev machine

`gpu-burn`, `cuda_memtest`, and `watchdogd` **must be built on the Jetson
itself** (or with a matching aarch64 / CUDA cross-toolchain). Their binaries are
tied to the exact GPU architecture (Ampere `sm_87`), CUDA toolkit, and driver
version on that JetPack image. A binary built on an x86 dev box — or even a
different JetPack release — will not run correctly on the DUT. Treat every build
step in the runbooks and [`docs/BUILD_PLAN.md`](../../docs/BUILD_PLAN.md) as
"run on the target."

| Submodule    | Upstream                                                       | Channel | Build notes                              |
|--------------|---------------------------------------------------------------|---------|------------------------------------------|
| `gpu-burn`   | https://github.com/wilicc/gpu-burn                            | 1a      | `make COMPUTE=87 CUDAPATH=/usr/local/cuda` |
| `cuda_memtest` | https://github.com/ComputationalRadiationPhysics/cuda_memtest | 2b      | `cmake -DCMAKE_CUDA_ARCHITECTURES=87 ..` |
| `watchdogd`  | https://github.com/troglobit/watchdogd                        | 3a      | autotools; needs libuEv, libite, libConfuse |
| `smrt`       | https://github.com/nasa/System_Monitor_for_Radiation_Testing  | 2a      | `python3 setup/install_tool.py`          |
