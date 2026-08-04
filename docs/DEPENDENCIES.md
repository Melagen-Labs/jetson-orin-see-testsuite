# Dependencies

Everything this project downloads or installs, and why. "Library" is used
loosely here to mean *anything we pull in from outside the repo* — importable
Python packages, but also toolchain pieces and package managers. Each row says
**what it is, where it lives, why we need it, and how it's installed.**

Update this file in the **same commit** as any change that adds, removes, or
re-pins a dependency (team review requirement — see `docs/CHANGELOG.md`).

> **Is `pip` a library?** No — `pip` is a *package manager* (a command-line
> tool), not something we `import`. But we do have to install it, and it's how
> the Python packages below get onto a board, so it's listed under **Toolchain**
> rather than **Python packages**.

---

## Toolchain (system-level, per board)

These come from JetPack/apt, not from `pip`. One-time per DUT.

| Item | Version | Purpose | Install |
|------|---------|---------|---------|
| Python | 3.10.12 | Runs `mem_check.py` (§2a CPU RAM) and its §2b GPU extension. | Ships with JetPack (L4T R36.5). |
| CUDA Toolkit (`nvcc`) | 12.6 | Builds `cuda_particles` (§1a); provides the CUDA runtime that CuPy binds to at import. Non-interactive SSH must `export PATH=/usr/local/cuda/bin:$PATH` to see `nvcc`. | Ships with JetPack. |
| `python3-pip` | 22.0.2 | **Package manager**, not a library. JetPack strips `pip`/`ensurepip` out of the base Python, so it must be installed before any `pip install`. | `sudo apt-get install -y python3-pip` (needs the board password). |

## Python packages — DUT (each Jetson)

Installed to the user site (`~/.local`, no sudo) with:

```bash
python3 -m pip install --user "cupy-cuda12x==13.*" "numpy>=1.22,<1.25"
```

| Package | Version | Purpose | Notes / pin rationale |
|---------|---------|---------|-----------------------|
| `numpy` | 1.24.4 | CPU array math for `mem_check.py` §2a (buffer fill, `np.where` read-back verify). Also a hard dependency of CuPy. | **Pinned `>=1.22,<1.25`.** CuPy 13 needs `>=1.22`; the system SciPy (JetPack) needs `<1.25`. A `--user` numpy shadows the system numpy 1.21.5 for this user, so it must stay inside SciPy's range or system tools break. |
| `cupy-cuda12x` | 13.6.0 | GPU array math for the §2b GPU-memory tester — the same moving-inversions method as §2a, run on GPU DRAM via `cp.full` / `cp.where`. The `cuda12x` build matches the board's CUDA 12.6. | **Pinned `==13.*`.** CuPy 14 requires numpy `>=2.0`, which pulls numpy 2.x into `~/.local` and breaks the system SciPy (compiled against numpy 1.x). Staying on 13 keeps numpy `<2`. A prebuilt `manylinux2014_aarch64` wheel exists, so no source build. |
| `fastrlock` | 0.8.3 | Fast reentrant lock used internally by CuPy for its memory pool. | Pulled in automatically as a CuPy dependency; not imported directly. |

## Python packages — Arbiter (host, not a DUT)

Tracked in [`arbiter/requirements.txt`](../arbiter/requirements.txt); install with
`pip install -r arbiter/requirements.txt`.

| Package | Version | Purpose |
|---------|---------|---------|
| `pyserial` | >=3.5 | Serial transport in `arbiter/power_reader.py` (§5) — **retired** with the power-monitor firmware board (2026-08-03); kept only so the module imports, and droppable once that module is retargeted to the DUT current collector's pulled logs. `heartbeat_listener.py`, `arbiter_main.py`, and `start_arbiter.py` use only the standard library. |

## Vendored third-party (in-repo, not downloaded)

These are checked into the tree, so there is nothing to install — listed here
for completeness of "outside code we depend on."

| Path | What it is | Status |
|------|-----------|--------|
| `jetson/compute/cuda_particles/` (+ `third_party/nvidia_common/*.h`) | Project-owned adaptation of NVIDIA `cuda-samples` "particles" plus its helper headers (§1a). | Built + verified on hardware. |
| `cuda_memtest`, `watchdogd` | Vendored upstream, **unmodified**. | Present but **unbuilt / unused** — kept as references. §2b uses CuPy instead of `cuda_memtest`. (`gpu-burn` was removed from the repo on 2026-08-03 — never built or used.) |
