# gpu_burn_patch — GPU compute workload modifications (channel 1a)

`gpu-burn` (https://github.com/wilicc/gpu-burn) is the third-party GPU compute
workload for this test. It is **not** modified in-tree here; the upstream source
is vendored as a git submodule at [`jetson/vendor/gpu-burn`](../../vendor/README.md)
and must be built **on the Jetson itself** (its binaries are tied to the exact
GPU arch, CUDA toolkit, and driver on the JetPack image).

This folder documents the three changes the build plan
([`docs/BUILD_PLAN.md`](../../../docs/BUILD_PLAN.md) §1a) calls for so that,
once someone with CUDA hardware can build and test, the patch can be applied to
the submodule and its output lines up with the rest of the pipeline.

## Why the patch is documented here, not applied yet

Applying it means editing `gpu-burn`'s C++/CUDA source (`gpu_burn-drv.cpp` and a
new `.cu` kernel) and rebuilding with `make COMPUTE=87 CUDAPATH=/usr/local/cuda`.
That build can only be validated on the target (Orin Nano, Ampere sm_87), which
is out of reach in this scaffolding pass. Doing it blind risks committing code
that does not compile. So the *design* lives here; the *diff* gets applied to
`jetson/vendor/gpu-burn` on the bench.

## The three modifications

1. **Add a Sobel kernel pass** alongside the existing checksummed CUBLAS
   matrix-multiply. Each iteration runs edge detection on a fixed, checksummed
   test image and compares the output checksum (CRC32) against a precomputed
   golden value, logging any mismatch with the iteration count. Full design in
   [`sobel_addon_notes.md`](sobel_addon_notes.md).

2. **Replace the stdout `FAULTY` print with a structured JSON log line** written
   to the DUT's local `compute/` log folder, so a fault survives an Ethernet
   outage and is pulled later by the arbiter. The schema is identical to
   `cpu_sort_check.py`'s so the arbiter parses GPU and CPU compute the same way:

   ```json
   {"ts": 1753600000.12, "iteration": 42, "kernel": "matmul",
    "event": "SDC_DETECTED", "expected": "3f2a...", "actual": "9c81..."}
   ```

   `kernel` is `"matmul"` or `"sobel"`; `event` is `"OK"` or `"SDC_DETECTED"`.

3. **Add an iteration-counter file** rewritten once per loop (same idea as
   `cpu_sort_check.py`'s counter): `{"iteration": N, "ts": ...}`. This is what
   lets the arbiter distinguish:
   - *stalled iteration* — process alive, counter file frozen,
   - *crashed* — process gone (`systemctl status` failed),
   - *corruption* — an `SDC_DETECTED` line in the log.

## Suggested layout once patched

Keep the diff self-contained and reproducible:

```
jetson/compute/gpu_burn_patch/
  README.md            <- this file
  sobel_addon_notes.md <- Sobel design + exact schema
  0001-radtest-sobel-and-json-logging.patch   <- git format-patch, added on the bench
```

Apply on the Jetson with:

```bash
cd jetson/vendor/gpu-burn
git apply ../../compute/gpu_burn_patch/0001-radtest-sobel-and-json-logging.patch
make COMPUTE=87 CUDAPATH=/usr/local/cuda
```

Then point its `--logfile`/counter output at `/var/log/radtest/compute/` to match
the CPU workload and the arbiter's pull.
