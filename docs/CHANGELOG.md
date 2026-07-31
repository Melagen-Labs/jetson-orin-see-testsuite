# Changelog

All notable changes to this repository, newest first. Each entry lists the
commit, the files touched, and what changed — so a reviewer can go straight to
the diff. Format loosely follows [Keep a Changelog](https://keepachangelog.com).

> **Scope note (read before reviewing):** two **project-owned** channels are
> built and verified on hardware — **`cuda_particles`** (GPU compute, §1a; a
> project-owned adaptation of **NVIDIA/cuda-samples "particles"**, not NASA code)
> and **`mem_check.py`** (CPU/system-RAM, §2a). **No changes have been made to
> the NASA SMRT repo**; SMRT is not vendored (`jetson/vendor/smrt` does not
> exist) — `mem_check.py` is our own tester using SMRT's method only as a
> reference. `gpu-burn`, `cuda_memtest`, and `watchdogd` are vendored upstream
> but unmodified and unbuilt; all other channels remain tentative.

## 2026-07-31

### control: align test-control port to 6000 + accept coordinator STOP_TEST

- **`9053d95` — test_control.py, config/test_control.json, CONTROL_INTERFACE.md, INTEGRATION_TEST.md**
  - Verified our DUT receiver against the real coordinator repo
    (`madhavsharma01312003/melagen-test-coordinator`, not ours — read-only). Its
    `config.example.json` uses **`jetson_port: 6000`**, so the DUT now listens on
    **6000** (was 5599) in both the config and the `DEFAULTS`.
  - Confirmed our reply already satisfies the coordinator's transport: it sends
    newline-terminated JSON and **hard-validates the reply's `request_id` matches**
    the request (it does *not* check `status`); our receiver already echoes
    `request_id` and terminates the reply with `\n`, so no wire change was needed.
  - The coordinator's `StopTestRequest` carries an extra **`target_request_id`**
    (its own `request_id` is a fresh uuid). Our `validate()` already tolerates the
    unknown field; we now also log `target_request_id` on STOP so a stop can be
    correlated to its start. STOP still stops all channels.
  - Docs/memory updated: port 6000 marked **confirmed** (no longer an open item);
    noted the coordinator repo implements neither heartbeat nor log-pull (those are
    Madhav's separate heartbeat monitor and Ansh's `pull_logs.sh`).

### docs: add INTEGRATION_TEST.md (DUT↔arbiter over-Ethernet runbook)

- **`ffcc155` — docs/INTEGRATION_TEST.md (new)**
  - Step-by-step runbook to validate the DUT against the arbiter over a direct
    Ethernet cable: static-IP setup (Jetson `nmcli` + Windows `New-NetIPAddress`),
    test-control (TCP 5599), heartbeat (UDP 5555), and log pull (SSH/radpull), with
    laptop-as-arbiter Python snippets so the DUT side is testable without the
    teammate's arbiter. Captures the Windows quirks hit in practice (elevated
    PowerShell for IP change; `python -c` strips quotes → write-to-file; USB-GbE
    is `Ethernet 2`, not the VirtualBox virtual adapter; NM static profile to stop
    the DHCP "connection failed" popup).
  - **Results so far (2026-07-31):** phases 0–3 PASS on hardware — control command
    over Ethernet restarts both channels with beam metadata in the logs; heartbeat
    streams 1 Hz with climbing seq. Phase 4 (log pull) pending the arbiter's pubkey.

### logs: standardize DUT log output to /var/log/radtest/<channel> for arbiter pull

- **`3efd3c7` — mem_check_gpu.json + particles.json log paths**
  - Both deployed channels now write to the canonical DUT log location the
    arbiter's `pull_logs.sh` expects, instead of `./logs` inside the clone:
    memory → `/var/log/radtest/memory`, compute → `/var/log/radtest/compute`
    (log_dir + heartbeat_path). This lets the arbiter's rsync log pull reach the
    logs via a low-priv `radpull` user **without exposing the operator's home
    dir**, and matches `pull_logs.sh`'s `DUT_LOG_DIR/{memory,compute,boot_state}`
    layout. The board is a single 467 GB NVMe mounted at `/`, so `/var/log` is on
    the SSD — the compute channel's large SEE dumps stay on the SSD.
  - **One-time DUT setup (operator, sudo):** create a shared `radlog` group, add
    `melagen` (writer) + `radpull` (reader), and create
    `/var/log/radtest/{memory,compute,boot_state}` owned `melagen:radlog`,
    mode `2750` (setgid, group-readable). Confirmed compatibility with Madhav's
    heartbeat monitor (UDP 5555, `{boot_id,seq,ts}`) — no sender code change.

### docs: mark arbiter/ as not-owned; confirm control transport = TCP

- **`de17365` — arbiter/README.md (new) + CONTROL_INTERFACE.md**
  - `arbiter/README.md` (**new**): prominent notice that the entire `arbiter/`
    directory is a teammate's (Ansh's) responsibility in a separate repo —
    reference/scaffolding only, **not used, built, or deployed** by this project.
    Adds an ownership table (DUT = this repo; arbiter = separate) and lists the
    DUT↔arbiter contracts we do own.
  - `docs/CONTROL_INTERFACE.md`: transport is now **confirmed TCP** (test
    coordinator = TCP, heartbeat monitor = UDP); only the port (5599) remains to
    align with the sender. Added a pointer to `arbiter/README.md`.
  - Reminder: `arbiter/pull_logs.sh` was handed to the teammate as the reference
    log-pull script.

### control: DUT-side arbiter test-control receiver (start/stop over Ethernet)

- **`8c6c1b7` — jetson/control/ (new) + setup-board.sh + docs**
  - New `jetson/control/test_control.py`: a TCP receiver for the arbiter's
    start/stop-test button. The arbiter (sender) is a teammate's separate repo;
    this is only our side, built to the agreed JSON contract (`protocol_version`,
    `command`, `request_id`, `beam_energy_mev`, `shielding_material`,
    `shielding_thickness_mm`, `sent_at_utc`).
    - **START_TEST**: validates against the contract (protocol_version 1; energy
      ∈ {53,100,200}; material ∈ {Aluminium,MLC1,MLC2}; thickness ∈ {8,12,16}),
      writes the beam/shield metadata into each channel's JSON config (`run_id`←
      request_id, `beam_energy`←"<n>MeV", `shield_config`←"<mat>_<mm>mm"), touches
      each `ARMED` flag, and `systemctl restart`s each channel. Idempotent on a
      repeated `request_id`.
    - **STOP_TEST** (our forward-compatible extension; arbiter contract lists only
      START_TEST so far): removes the `ARMED` flags and stops the channels.
    - Replies with a JSON ack (`status` ok/error + per-channel results). Standard
      library only — no new deps.
  - `config/test_control.json` (**new**): listen host/port (TCP **5599**),
    `allowed_peers` allow-list, the contract enumerations (must match the sender),
    and the channel→config/armed_flag/service map.
  - `test_control.service` (**new**): runs the receiver as **root** (needs to
    `systemctl` the channels + write flags), always-on (not ARMED-gated — it must
    listen to receive the arm command).
  - `scripts/setup-board.sh`: installs + enables `test_control.service` alongside
    the channels. `docs/CONTROL_INTERFACE.md` (**new**) documents the full contract.
  - **Open coordination items (flagged, not blockers):** transport+port (TCP/5599
    chosen here) must match the arbiter's sender; the arbiter must send `STOP_TEST`
    for the stop button to reach us.
  - **Verified off-hardware:** unit + TCP round-trip tests cover valid START (incl.
    pretty-printed/chunked JSON), metadata injection, ARMED touch/remove, idempotent
    retry, STOP, and every rejection path (bad version/enum, missing field, unknown
    command, garbage JSON).

### cuda_particles: final-checkpoint detection, SEE state dump, crash flag + restart

- **`fa8592c` — final-hash detection + SEE dump-to-SSD + crash handling**
  - `jetson/compute/cuda_particles/particles_main.cpp`:
    - **Detection now uses only the FINAL checkpoint** of each epoch vs the
      golden's last hash (any earlier upset cascades to the end, so the final
      hash still flags the epoch). Drops the 19 intermediate golden compares +
      their per-checkpoint `checksum` log spam; `--generate-golden` still writes
      the full 20-hash table.
    - **SEE state dump (for offline reconstruction).** Each checkpoint's full
      particle state is buffered in RAM; on a flagged epoch the whole trajectory
      is written to `logs/see_dumps/epoch_<N>_iter_<M>.bin` (raw float32,
      `nCheckpoints × [pos(count) + vel(count)]`) on the **SSD**, and the
      `see_event` record carries `dump`, `dump_checkpoints`, `dump_stride`,
      `num_particles`, `floats_per_checkpoint` so a reference Orin can replay it
      and count grouped SEEs. Gated by config `save_see_epochs` (default true).
    - **Crash / unclean-shutdown handling.** A `logs/running.flag` marker is held
      while running and removed on a clean stop; if present at startup the prior
      instance died abnormally (CUDA abort, segfault, hang→reboot, power) → logged
      as a `sim_fault`/`crash` SEE (`reason:"unclean_restart"`, prev pid/ts). CUDA
      errors at the checkpoint memcpy are caught gracefully: logged as
      `sim_fault`/`crash` with `cudaGetErrorString`, dumped, then exit 2 for a
      fast restart (rather than the old abort).
  - `config.{h,cpp}`, `config/particles.json`: add `save_see_epochs` bool + a
    `getB` parser.
  - `cuda_particles.service`: `RestartSec` 2→1 and `StartLimitIntervalSec=0`
    (never stop restarting — crashes are expected data during a beam run).
  - Ethernet-to-arbiter of the dumps is **tentative** (link not wired); the data
    sits on the SSD under `logs/see_dumps/` ready for the arbiter's rsync pull.
  - **Verified on the Orin clone:** clean 2-epoch run → exit 0, 0 dumps, marker
    removed; corrupt-final-golden run → 2 SEEs + two 10 MB dumps + full see_event
    records; `kill -9` mid-run → marker survives, restart logs the
    `unclean_restart` crash SEE.

### mem_check: add GPU DRAM tester (§2b); memory testing is now GPU-only

- **`573a9ff` — mem_check.py GPU backend + gpu config/service + docs**
  - `jetson/memory/mem_check.py`: the moving-inversions tester now selects its
    backend from config `target`. `target:"gpu"` (channel 2b) allocates a **CuPy**
    uint8 buffer in **GPU DRAM** and runs the exact same paint / hold / read-back /
    scrub loop as the CPU path — the array module (`xp`) is numpy for cpu, CuPy for
    gpu, so the detection logic is identical. Compare + scrub run as GPU kernels
    (`cp.where` on-device) with a `sync()` per pass; only the capped handful of
    flagged bytes are copied to the host — **keeps CPU workload minimal**. CuPy is
    imported lazily; records carry a `target` field; a `--target {cpu,gpu}` CLI
    flag overrides config. Start record now uses generic `mem_total_mb` /
    `mem_avail_mb` (were `ram_*`).
  - `config/mem_check_gpu.json` (**new**): `target:"gpu"`, own log
    (`mem_check_gpu.jsonl`) + heartbeat, `auto_fraction:0.50` (lower than the CPU
    0.70 to leave GPU DRAM headroom for `cuda_particles` §1a running concurrently).
    `config/mem_check.json` gains explicit `target:"cpu"` + `log_name`.
  - `mem_check_gpu.service` (**new**): runs `mem_check.py --config
    mem_check_gpu.json`, sets `HOME` so `python3` finds CuPy in `~/.local`, shares
    the `memory/ARMED` flag with the (undeployed) CPU unit.
  - **GPU-only pivot:** memory testing is now GPU-only to minimize CPU workload.
    The §2a CPU tester stays in the repo (code + `mem_check.service`) as
    reference, but is no longer deployed: `scripts/setup-board.sh` installs
    `mem_check_gpu.service` (not `mem_check.service`) and now also installs the
    CuPy deps; `docs/SERVICES.md` documents the GPU unit + the disable-CPU swap.
  - **Verified on the Orin:** `mem_check.py --self-test --target gpu` allocated a
    GPU buffer, caught the injected flip at address `0x3039` (`xor:0x01`,
    `target:"gpu"`), emitted schema-v1 records, and exited 2.

### deps: install CuPy on the DUT for §2b GPU memory test + new `DEPENDENCIES.md`

- **`573a9ff` — docs/DEPENDENCIES.md (new) + docs/CHANGELOG.md**
  - Installed CuPy on the Jetson to enable the §2b GPU-memory tester (the CuPy
    extension of `mem_check.py`). `pip`/`ensurepip` are stripped from JetPack's
    base Python, so `sudo apt-get install -y python3-pip` was run first (by the
    board operator), then `python3 -m pip install --user "cupy-cuda12x==13.*"
    "numpy>=1.22,<1.25"`.
  - **Version pins matter:** CuPy 14 requires numpy `>=2.0`, which shadows the
    system numpy 1.21.5 in `~/.local` and breaks the JetPack SciPy (built against
    numpy 1.x) — `import cupy` then crashes via `cupyx` → SciPy. Pinning CuPy
    `==13.*` + numpy `1.24.4` (inside SciPy's `<1.25` range) keeps the whole
    board consistent. Verified: 256 MB GPU allocation + injected-flip detection
    via `cp.where` both work on the Orin GPU.
  - **`docs/DEPENDENCIES.md` (new):** single catalog of everything the project
    downloads/installs — toolchain (Python, CUDA 12.6, `python3-pip`), DUT Python
    packages (numpy, cupy-cuda12x, fastrlock), arbiter packages (pyserial), and
    vendored third-party — each with purpose and pin rationale. To be updated in
    the same commit as any future dependency change.

## 2026-07-30

### fleet: one-shot `setup-board.sh` + per-board (git-ignored) golden

- **`40e48fb` — scripts/setup-board.sh + untrack golden_hashes.txt**
  - `scripts/setup-board.sh` (**new**, fully commented): one interactive command
    per board does the whole bring-up — set hostname (`orin-nano-0N`, feeds
    `jetson_id:"auto"`), clone/pull, build `cuda_particles`, generate this board's
    golden, arm both channels, install+enable+start services.
  - `.gitignore`: ignore `golden_hashes.txt`; `git rm --cached` the previously
    tracked copy. The golden table is device+build specific, so it is **generated
    per board**, not shared — matches the README's own warning and avoids
    `git pull` conflicts from a locally regenerated table.
  - Docs updated: `docs/DEPLOYMENT.md` (points to the script), cuda_particles
    `README.md` and `docs/BUILD_PLAN.md` §1a (golden is per-board / git-ignored,
    no longer "committed").

### services: repoint both units to the git clone (`~/see-testsuite`)

- **`7f8c335` — cuda_particles/mem_check .service → clone paths**
  - `jetson/compute/cuda_particles/cuda_particles.service` and
    `jetson/memory/mem_check.service`: `WorkingDirectory`, `ExecStart`, and
    `ConditionPathExists` repointed from the old standalone dirs
    (`~/cuda_particles`, `~/mem_check`) to the clone
    (`~/see-testsuite/jetson/compute/cuda_particles`, `.../jetson/memory`). Same
    `~/see-testsuite` path on every DUT (all `melagen`), so one committed unit
    fits the whole fleet. Fixed the stale mem_check comment (clone resolves
    `event_log.py` via `../../shared`, no copy needed). Retires the drift-prone
    scp deployment. `docs/SERVICES.md` install paths updated to match.

### fleet deployment: git-clone model + `jetson_id:"auto"` + docs

- **`a8fd27f` — fleet: hostname jetson_id, fleet script, DEPLOYMENT.md**
  - `jetson/memory/mem_check.py` and `jetson/compute/cuda_particles/particles_main.cpp`:
    `jetson_id: "auto"` now resolves to the board **hostname** (`socket.gethostname`
    / `gethostname()`), so one config fits all 7 DUTs. Both `config` files default
    to `"auto"`.
  - `scripts/fleet.sh` (**new**): one-command fleet updater (`pull`/`build`/
    `restart`/`status` over SSH to `orin-nano-01..07`).
  - `docs/DEPLOYMENT.md` (**new**): the 7-DUT model — git-clone for dev
    (`git pull`, no more per-board scp), one hashed master image for the frozen
    campaign; per-DUT hostname + own golden table. Linked from README.
  - `.gitignore`: ignore per-board `ARMED` flag.
  - Context: campaign scales to **7 Jetson Orin Nano DUTs**.
  - **Verified on-target:** cloned the repo to `~/see-testsuite`, built
    `cuda_particles` in the clone (BUILD OK), and both tools logged
    `jetson_id:"ubuntu"` (the hostname) from `"auto"` — confirming the fleet
    identity works end-to-end. Notably, the *old standalone* `~/cuda_particles`
    deployment logged the stale `orin-nano-01` because its `config.cpp` had
    **drifted** from the repo (10 vs 9 `getStr`) — a live demonstration of the
    scp-drift the clone model removes.

### docs: record DRAM-ECC check + memory check-frequency rationale (§2a)

- **`8ee7360` — BUILD_PLAN §2a: DRAM ECC detection-scope note**
  - `docs/BUILD_PLAN.md` §2a: documented that hardware DRAM ECC would hide
    single-bit upsets from `mem_check`, and the on-target check (2026-07-30)
    showing ECC appears **OFF** (empty `/sys/devices/system/edac/mc/`, no DRAM
    EDAC driver, full 8 GB usable) — so single-bit upsets are visible. Noted the
    sudo `dmesg` confirmation step, and why memory re-check cadence can be lazy
    (persistent upsets; same-bit double-hit odds ≈ `(rate·interval)²/(2·N_bits)`).

### `mem_check.py`: fix OOM at auto coverage (chunked verify)

- **`2af8d77` — mem_check: verify in chunks to avoid a 2x-RAM temporary**
  - `jetson/memory/mem_check.py`: the read-back verify did
    `np.where(buf != val)` over the **whole** buffer, which allocates a full-size
    boolean mask — so at the auto buffer size (70% of free RAM) the transient
    footprint hit ~2× the buffer and the OOM killer SIGKILL'd the process. Now
    scans in 64 MB `VERIFY_CHUNK_BYTES` slices (views, vectorized scrub), so peak
    extra memory is one chunk, not one buffer.
  - **Verified on-target:** auto run resolved to 4,335 MB (57% coverage), **no
    OOM**, peak child RSS 4,430 MB (≈ buffer + ~95 MB); self-test still detects
    (exit 2). Measured cadence: ~2.1 s to check every byte once.

### docs: add `docs/SERVICES.md` (systemd install + ARMED arming)

- **`6688d90` — docs: SERVICES.md**
  - `docs/SERVICES.md` (**new**): how to install `cuda_particles`/`mem_check` as
    systemd services; the ARMED arming model (one-time `touch`, persists across
    reboots, `rm` to disarm); stop/disarm; and a section clarifying the two
    "heartbeats" — DUT-local `heartbeat.txt` (liveness/counter snapshot) vs the
    §3 external UDP heartbeat. Linked from `README.md` docs layout.

### `cuda_particles.service`: add ARMED boot gate (match mem_check)

- **`fabf307` — cuda_particles.service: ConditionPathExists ARMED gate**
  - `jetson/compute/cuda_particles/cuda_particles.service`: added
    `ConditionPathExists=/home/melagen/cuda_particles/ARMED` so `enable` wires it
    to boot but it only runs while the persistent `ARMED` flag exists (`touch`
    once to arm — survives reboots; `rm` once to disarm). Mirrors the mem_check
    gate so both channels arm identically.

### `mem_check.py`: auto-max memory coverage + boot arming flag

- **`6c3d478` — mem_check: auto buffer sizing, coverage logging, ARMED gate**
  - `jetson/memory/mem_check.py`: `buffer_mb: "auto"` now sizes the buffer to
    `auto_fraction` (0.70) of free RAM from `/proc/meminfo`, maximizing DRAM under
    test while leaving OS/compute headroom. `start` record now logs `buffer_mb`,
    `ram_total_mb`, `ram_avail_mb`, `coverage_pct`. Added optional `mlock: true`
    (best-effort pin into physical RAM). Verified on-target: auto-resolved to
    3,845 MB ≈ 50.5% of the 7.6 GB board; self-test still detects (exit 2), clean
    run exits 0.
  - `jetson/memory/config/mem_check.json`: `buffer_mb` → `"auto"`, add
    `auto_fraction: 0.70`, `mlock: false`.
  - `jetson/memory/mem_check.service`: added
    `ConditionPathExists=…/mem_check/ARMED` — `enable` wires it to boot but it
    only runs when the `ARMED` flag exists (`touch ARMED` for a campaign so
    crash/watchdog reboots restart it; `rm ARMED` so normal power-ons don't).
    `Restart=always` still covers process crashes.
  - `docs/BUILD_PLAN.md` §2a updated (coverage + arming).

### Memory channel §2a: `mem_check.py` CPU/system-RAM tester (built & verified)

- **`9d48a42` — memory §2a: add project-owned `mem_check.py` + config + service**
  - `jetson/memory/mem_check.py` (**new**): CPU/system-RAM pattern tester. Paints
    a numpy `uint8` buffer with `0x00/0xFF/0x55/0xAA`, read-back-verifies over
    `hold_sweeps` with a dwell, emits schema-v1 `memory` records
    (`mem_upset`: `test, address, pattern, expected, actual, xor`) via
    `shared/event_log.py`, scrubs each detected byte (count-once), heartbeats
    each sweep, `checkpoint`/`start`/`stop` records, clean SIGTERM (exit 2 on
    upset). `--self-test` injects a bit flip to prove detection.
  - `jetson/memory/config/mem_check.json` (**new**): buffer size, patterns,
    dwell, log paths, run metadata.
  - `jetson/memory/mem_check.service` (**new**): systemd unit, `User=melagen`,
    deployed-layout paths.
  - **Decision:** built project-owned rather than vendoring NASA SMRT (emits the
    frozen schema directly; SMRT method kept as reference). `docs/BUILD_PLAN.md`
    §2a rewritten to reflect this (old SMRT plan collapsed into a `<details>`);
    root `README.md` status table + layout + reference table updated.
  - **Verified on the Orin Nano:** `--self-test` flipped byte `0x3039` → logged
    `mem_upset` (expected `0x00`, actual `0x01`, xor `0x01`), exit 2; clean
    bounded run on the full 2 GB buffer → 0 anomalies, exit 0, `start`/2×
    `checkpoint`/`stop`, **all records validate against schema v1** (0 invalid).
    Fixed one bug pre-commit: `g_stop` needed a `global` decl in `main()`.

### `cuda_particles` README: document epoch-length tuning for SEE pile-up

- **`69dfbc2` — README: add the "when to change it" trigger for `epoch_iterations`**
  - `jetson/compute/cuda_particles/README.md`: the epoch-tuning section now states
    the decision rule — change `epoch_iterations` when SEEs are detected **more
    often than ~1 per ~30 s** (SEE-affected epochs < ~50 apart, from the live
    `see_events` rate). Corrected the threshold from ">30 epochs" to **">~50
    epochs (~30 s)"** for a <1% undercount (undercount ≈ `SEE_rate × epoch_s / 2`).
    Docs only.

- **`a0d1dcb` — README: how/where to tune `epoch_iterations`**
  - `jetson/compute/cuda_particles/README.md`: added a "Tuning the epoch length"
    section — lower **`epoch_iterations`** in `config/particles.json` to shorten
    the ~0.66 s epoch window and cut the odds of two SEEs per epoch. Flags that
    changing `epoch_iterations`/`checksum_interval` **requires regenerating the
    golden table** (one hash per `epoch_iterations ÷ checksum_interval`). Docs only.

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
