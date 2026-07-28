# Sobel add-on for gpu-burn — design notes

Concrete plan for modification #1 from [`README.md`](README.md): adding a
checksummed Sobel edge-detection pass to `gpu-burn` so the GPU compute channel
exercises a second, different kernel and catches silent data corruption (SDC) in
its output. This is a design spec to implement against the vendored
`jetson/vendor/gpu-burn` submodule on the Jetson; it is intentionally not code
here because it can only be compiled/validated on the target (Ampere sm_87).

## 1. Fixed input image

Use a **fixed, deterministic** input so the output is reproducible and its golden
checksum can be precomputed once:

- Generate a `1024 x 1024` single-channel (8-bit grayscale) image at build time
  from a fixed seed — e.g. `img[y][x] = (x * 131 + y * 977) & 0xFF`. No file I/O
  at runtime; bake the generator into the workload so the image is identical on
  every DUT and every run.
- Upload it to the GPU **once** at startup and leave it resident, so each
  iteration only runs the kernel + checksum, not a fresh host->device copy (a copy
  each iteration would mask device-memory SDC behind a fresh clean upload).
- Keep a device-side copy of the golden *output* as well, so a mismatch can be
  localized (first differing pixel index) if you want address-level detail later.

## 2. Kernel

A standard 3x3 Sobel: compute `Gx` and `Gy` with the usual kernels, output
`min(255, sqrt(Gx^2 + Gy^2))` (or `|Gx| + |Gy|` for speed) per interior pixel,
border pixels set to 0. One thread per output pixel. Put it in a new
`sobel.cu` / `sobel.cuh` compiled into the gpu-burn target.

## 3. Checksum: CRC32

Use **CRC32** over the output image bytes:

- Rationale: cheap, order-sensitive, and detects single-bit flips reliably —
  which is exactly the SDC signature we care about. A plain 32-bit additive sum
  is weaker (bit flips can cancel); CRC32 is the build plan's recommended choice.
- Compute it once at startup on the known-good output to get `golden_crc`.
- Each iteration, after the kernel finishes, copy the output back (or run a
  reduction CRC on-device) and compare `crc == golden_crc`.
- A convenient standard polynomial is the zlib/IEEE CRC32 (`0xEDB88320`
  reflected); if gpu-burn is already linked against zlib you can reuse `crc32()`
  on the host side for the comparison.

## 4. Where to hook it in `gpu_burn-drv.cpp`

gpu-burn's main compute loop lives in `gpu_burn-drv.cpp`, inside the per-GPU
worker that repeatedly launches the CUBLAS matmul and then calls the comparison
that prints `FAULTY`. That loop body is the integration point:

```
// per-iteration, inside the existing while/compute loop in gpu_burn-drv.cpp:
launch_matmul();                 // existing gpu-burn work
matmul_ok = compare_matmul();    // existing checksum compare
log_compute("matmul", iteration, matmul_ok, golden_mm_crc, actual_mm_crc);

launch_sobel(d_image, d_out);    // NEW
uint32_t actual_crc = crc32_of(d_out);          // NEW
bool sobel_ok = (actual_crc == golden_crc);     // NEW
log_compute("sobel", iteration, sobel_ok, golden_crc, actual_crc);  // NEW

write_iteration_counter(iteration);   // NEW: rewrite the counter file each loop
```

Replace the existing stdout `FAULTY` print with `log_compute(...)` (modification
#2) so both kernels emit the same structured line.

## 5. Exact JSON log schema (must match `cpu_sort_check.py`)

`log_compute()` appends one JSON object per line to a file under the DUT's local
`compute/` folder (e.g. `/var/log/radtest/compute/gpu_burn.log`). Fields and
types are identical to what `jetson/compute/cpu_sort_check.py` emits so the
arbiter parses CPU and GPU compute with one parser:

```json
{"ts": 1753600000.12, "iteration": 42, "kernel": "sobel",
 "event": "OK", "expected": "1a2b3c4d", "actual": "1a2b3c4d"}
```

| field       | type            | notes                                            |
|-------------|-----------------|--------------------------------------------------|
| `ts`        | float (epoch)   | `clock_gettime(CLOCK_REALTIME)` as seconds.usec  |
| `iteration` | int             | loop counter, monotonic within a process         |
| `kernel`    | string          | `"matmul"` or `"sobel"`                           |
| `event`     | string          | `"OK"` or `"SDC_DETECTED"`                        |
| `expected`  | string          | golden checksum, hex (CRC32 for sobel)           |
| `actual`    | string          | computed checksum, hex                           |

Emit an `event: "OK"` line only occasionally (e.g. once/second) to bound log
growth, but emit **every** `SDC_DETECTED` immediately and fsync it.

## 6. Iteration-counter file (modification #3)

Rewrite a tiny separate file every loop, atomically (temp + rename), matching the
CPU workload's counter:

```json
{"iteration": 4211, "ts": 1753600000.12}
```

Suggested path `/var/log/radtest/compute/gpu_burn.counter.json`. This is the
signal the arbiter uses to tell *stalled* (file frozen, process alive) from
*crashed* (process gone) from *corrupted* (`SDC_DETECTED` in the log).
