# Changelog

All notable changes to this repository, newest first. Each entry lists the
commit, the files touched, and what changed — so a reviewer can go straight to
the diff. Format loosely follows [Keep a Changelog](https://keepachangelog.com).

> **Scope note (read before reviewing):** the **only** channel built and
> verified on hardware is **`cuda_particles`** (GPU compute, §1a) — a
> project-owned adaptation of **NVIDIA/cuda-samples "particles"**. It is **not**
> NASA code. **No changes have been made to the NASA SMRT repo** (memory channel
> §2a); SMRT is not yet vendored (`jetson/vendor/smrt` does not exist) — only the
> runbook `jetson/memory/run_smrt.md` describes how to add it later. Likewise
> `gpu-burn`, `cuda_memtest`, and `watchdogd` are vendored upstream but unmodified
> and unbuilt.

## 2026-07-30

### `cuda_particles`: unify SEE field name + document counting semantics

- **`aa801d8` — cuda_particles: rename `see_count`→`see_events`, document SEE counting**
  - `jetson/compute/cuda_particles/particles_main.cpp`: the `see_event` record's
    field renamed `see_count` → **`see_events`** (now matches the heartbeat and
    `stop` field — one name everywhere). Expanded the epoch-boundary comment to
    state the semantics explicitly: `see_events` counts **epochs containing ≥1
    SEE, not the total number of SEEs** (an upset early in an epoch corrupts the
    state all later steps build on, so raw mismatches would over-count early hits);
    the undercount when two SEEs share an epoch is ~`(rate × epoch_seconds)/2`,
    negligible at the low fluxes SEE testing runs at.
  - `jetson/compute/cuda_particles/README.md`: log-format note updated to `see_events`.
  - **Verified on-target:** rebuilt on the Orin Nano; 5k-iter run, exit 0, no
    `see_count` present, `see_events:0` in the stop record + heartbeat.

### `cuda_particles` Stage 1 completion + docs (verified on the Orin Nano)

- **`4c8db4a` — BUILD_PLAN: mark §1a fully qualified, §5a schema frozen at v1**
  - `docs/BUILD_PLAN.md`: status banner now marks §1a fully qualified; §1a steps
    3–5 updated (golden committed, schema-v1 logging, one-event-per-epoch SEE
    counter, service ready); tolerance-policy decision resolved to bit-exact;
    §5a rewritten from "tentative" to **FROZEN v1** with the corrected compute
    payload (`iter, epoch, step, hash, golden, mismatch, finite, max_abs_pos,
    anomaly, see_event`) and a real emitted record as the example.

- **`7d007f0` — cuda_particles.service: align unit to the proven on-target layout**
  - `jetson/compute/cuda_particles/cuda_particles.service`: `WorkingDirectory`
    and `ExecStart` repointed from `/opt/see/...` to the deployed
    `/home/melagen/cuda_particles` (binary at `build/cuda_particles`); added
    `User=melagen`, `Environment=PATH=/usr/local/cuda/bin:...`, and
    `LD_LIBRARY_PATH=/usr/local/cuda/lib64` for the JetPack CUDA runtime.
    `/opt/see` system-wide option kept in a comment. **Not yet installed** (needs
    sudo on the DUT).

- **`8acb331` — cuda_particles: schema-v1 logging + one-event-per-epoch SEE counter**
  - `jetson/compute/cuda_particles/particles_main.cpp`:
    - Added `envelope()` helper emitting the schema-v1 required fields
      (`schema_version:1`, `ts`, `run_id`, `jetson_id`, `channel:"compute"`,
      `event`, `status`); applied to the start, checksum, stop, and new
      `see_event` records.
    - `nowIso()` upgraded from second- to **millisecond** precision (via
      `<chrono>`), matching `shared/event_log.py` `iso_now()`.
    - `metaFields()` trimmed to the beam/run trio (`run_id`/`jetson_id` moved
      into the envelope).
    - Checksum `status` = `"anomaly"` on mismatch/NaN/out-of-bounds, else `"ok"`.
    - **SEE counter:** an epoch with ≥1 anomaly is collapsed to exactly one
      `see_event` record at the epoch boundary (removes early-vs-late
      over-counting bias); running total surfaced as `see_events` in the
      heartbeat file and the `stop` record.
  - `jetson/compute/cuda_particles/README.md`: added a "Log format (schema v1)"
    section documenting the envelope and the SEE-counter semantics.
  - **Verified on-target:** rebuilt on the Orin Nano; 30k-iter / 30-epoch
    re-verify against the committed golden table — golden matched,
    `see_events:0`, exit 0. All four record types validate against
    `shared/event_log.py`.

- **`ada6808` — Commit on-target golden hash table; mark Stage 1 soak validated**
  - `jetson/compute/cuda_particles/data/golden_hashes.txt` (**new**): 20
    FNV-1a-64 hashes, one per checksum step, generated on the Orin Nano with
    `--generate-golden`.
  - `jetson/compute/cuda_particles/README.md`: checklist items ticked (on-target
    build, golden committed, bit-exact policy confirmed).
  - **Soak validation:** ~67 min / 6,064 epochs / 6,063,272 iterations, **0
    anomalies**, clean SIGTERM stop record (`corruption_seen:false`).

---

_Entries above are the changes made in the 2026-07-30 session. Prior commits
(scaffold `0f61401`, and the Stage 2 schema proposal `3ca4605`) predate this
changelog._
