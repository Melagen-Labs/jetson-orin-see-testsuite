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

## 2026-08-01

### compute + memory: concise "SEE Detected" operator line during a run

- **`_pending_` — jetson/compute/cuda_particles/particles_main.cpp, jetson/memory/mem_check.py**
  - Both deployed detectors now print a **one-line human-readable `SEE Detected`
    summary to the journal** (systemd `StandardError`) at each detection, so an
    operator watching `journalctl -f` sees each event live without parsing JSONL.
  - **Compute:** classifies the subtype — `nonfinite` (NaN/Inf), `out_of_bounds`
    (|pos| > 2.0), or `golden_mismatch` (hash divergence) — and the CUDA-fault path
    reports `sim_fault (<cuda error>)`. Each line states whether the post-processing
    state dump was written (`post-processing dump saved -> see_dumps/…` vs
    `NOT saved`). Synthetic runs (`--inject`/`--chaos`) are tagged `[SYNTHETIC]`.
  - **Memory:** one concise summary per sweep with upsets (`mem_upset x<N>`, pattern,
    target), noting how many per-byte records were saved — memory's post-processing
    data is those inline `mem_upset` records (no binary dump), and the line flags when
    the per-sweep report cap truncated them.
  - **Schema-v1 JSONL records are unchanged** — these are additive stderr lines the
    arbiter never parses; the frozen `see_event`/`sim_fault`/`mem_upset` contracts the
    arbiter and result CSV depend on are untouched.
  - **Not yet rebuilt on hardware** — `cuda_particles` recompiles on the Jetson
    (`setup-board.sh` or `fleet.sh build`); pending after the current spike test.

### Fix: heartbeat + boot-state services never installed on a scripted board

- **`_pending_` — jetson/heartbeat/heartbeat_sender.service, jetson/boot_state/boot_state_logger.service, jetson/boot_state/boot_state_logger-boot.service, scripts/setup-board.sh, scripts/fleet.sh, docs/SERVICES.md, docs/FLASH_AND_BRINGUP.md, docs/DEPLOYMENT.md, jetson/systemd/README.md, README.md**
  - **Root cause:** these three units still carried the legacy `/opt/radtest/...`
    deploy path (in both `ExecStart` and `Documentation=`), but the fleet actually
    runs from the git clone at `/home/melagen/see-testsuite` — the path the proven
    `cuda_particles` / `mem_check_gpu` / `test_control` units already use. Worse,
    `setup-board.sh` only ever installed those three; it never copied or enabled the
    heartbeat sender (channel 3b) or the boot-state loggers (channel 4). Net effect:
    a board brought up purely by the script **never ran the heartbeat or boot-state
    loggers** — so the arbiter had no 1 Hz liveness signal to detect a hung/latched
    board (a primary SEL/SEFI signal) and no autonomous-reboot evidence.
  - **Units:** repointed all three `ExecStart` script paths and `Documentation=` URLs
    from `/opt/radtest/...` to `/home/melagen/see-testsuite/...`. No behavioural or
    log-schema change — only the launch/doc paths.
  - **`setup-board.sh` [7/7]:** now copies + `enable --now`s all five deployed units
    (added `heartbeat_sender`, `boot_state_logger`, `boot_state_logger-boot`). Added
    `HEARTBEAT`/`BOOT` path vars alongside the existing channel vars, and an
    `ARBITER_IP` var (default `192.168.1.10`, override `ARBITER_IP=x.x.x.x
    ./setup-board.sh NN`) that `sed`-patches `--arbiter-ip` in the *installed* copy of
    the heartbeat unit only, leaving the repo unit at its documented default.
  - **`/var/log/radtest/boot_state`:** unchanged — still provisioned by the one-time
    operator step (FLASH_AND_BRINGUP.md 1b, `melagen:radlog` setgid mode 2750), which
    a setgid dir hands to `radpull` for log pull. Boot-state units run as root and the
    logger also `os.makedirs()` the dir if absent, so a fresh board still logs.
  - **`fleet.sh` fixed alongside:** its `restart`/`status` targeted `cuda_particles
    mem_check` — the wrong memory unit (`mem_check` is the non-deployed CPU/2a tester;
    the deployed one is `mem_check_gpu`) and omitted `test_control`, `heartbeat_sender`,
    and `boot_state_logger`. Both now cover the full deployed set (`restart` excludes
    the oneshot boot logger so it can't append a spurious boot record).
  - **Docs synced:** `SERVICES.md` now documents all five deployed units in two
    classes (ARMED-gated workloads vs. always-on monitors) with an always-on install
    block, plus arbiter-IP guidance for the heartbeat (fleet-wide, not per-board:
    beam-line `192.168.1.10` vs. the arbiter laptop's Tailscale IP for remote testing); `FLASH_AND_BRINGUP.md` ("five services" not three, heartbeat now a service
    in the §4 checks, master verified-state box flags the two newly-added services as
    pending a `setup-board.sh` re-run); `DEPLOYMENT.md` service/arming section; and
    `jetson/systemd/README.md` (deploy path `/opt/radtest` → `/home/melagen/see-testsuite`,
    points at `setup-board.sh` as the real installer). Top-level `README.md`
    component-status rows for heartbeat + boot-state updated from "🟠 tentative,
    untested" to "🟡 installed by `setup-board.sh`" (heartbeat sender verified
    streaming 1 Hz; on-hardware service enable still pending).
  - **Verified on `orin-nano-01` (non-sudo):** staged the corrected files into the
    board's clone; confirmed no `/opt/radtest` remains, `ExecStart`/`Documentation`
    resolve to real on-board scripts, `setup-board.sh` passes `bash -n`, and the
    board already had only `cuda_particles`/`mem_check_gpu`/`test_control` installed
    (reproducing the gap). Confirmed `/var/log/radtest/boot_state` exists mode 2750.
    The sudo `cp`+`enable --now` install is operator-run (handed off separately).

### Chaos mode: continuous random GPU bit-flips (test only) — verified on hardware

- **`8b7c527` — jetson/compute/cuda_particles/particles_main.cpp**
  - New **TEST-ONLY `--chaos`** (+ `--chaos-prob` default 0.01, `--chaos-seed`
    default 1): each step, with probability `chaos-prob`, flips a random bit of a
    random float in the device pos buffer — the random-in-time-and-place cousin of
    `--inject`. Stresses the detect→dump→report chain with mixed, randomly-placed
    upsets, and the CUDA-fault recovery path if a corrupted value derails a kernel
    (surfaces as `sim_fault` → service restart, the existing path). Refuses to run
    with `--generate-golden`; validates `chaos-prob ∈ (0,1]`.
  - **Poison-pill parity with `--inject`:** every synthetic run (inject *or* chaos)
    now writes a loud **`synthetic_run`** marker at the top of the log, and
    `see_event` records carry both `"injected"` and `"chaos"` booleans — so injected
    or chaos data can never be confused with real campaign events, even at a glance.
  - **Ceiling documented:** flipping bits in *valid* GPU buffers corrupts values
    (detected) but rarely triggers an illegal access, so chaos won't reboot/hang the
    SoC — the GPU MMU protects the rest of the system. Whole-board crash stays the
    beam's domain; chaos validates detection + CUDA-fault recovery.
  - **Verified on `orin-nano-01`:** `--chaos --chaos-prob 0.02 --chaos-seed 7` over 3
    epochs → 3 SEEs, all `"chaos":true`, `synthetic_run` marker present. Full battery
    (inject bitflip/nan/oob + chaos) run against an isolated `/tmp` log dir; the real
    golden table (20 lines) and real logs were confirmed untouched.

### Fault injection: induce every compute SEE type on demand (no beam) — verified on hardware

- **`549a37f` — jetson/compute/cuda_particles/particles_main.cpp**
  - New **TEST-ONLY `--inject {bitflip,nan,oob}`** flag (+ `--inject-at`,
    `--inject-bit`, `--inject-index`), default off. Fires once at `--inject-at`,
    corrupting one float of the **device** particle buffer so the fault propagates
    through the rest of the epoch exactly like a real upset and is caught by the same
    final-checkpoint detector, with a real state dump written. `bitflip` →
    `cuda_golden_mismatch`, `nan` → `cuda_nonfinite`, `oob` (1e6) → out-of-bounds.
  - **Poison-pill safety:** every injected run writes an `inject` record and tags the
    resulting `see_event` `"injected":true`, so injected events can never be confused
    with — or silently pollute — real campaign data. Refuses to run with
    `--generate-golden` (would bake corruption into the baseline).
  - **Verified end-to-end on `orin-nano-01`:** built clean; all three modes produced
    `inject` + `see_event`(`injected:true`) + a `see_dumps/*.bin`, exit 2. Subtypes
    correct (`bitflip`→mismatch, `nan`→`finite:false`). `see_dump_triage.py` on a real
    injected dump **localized the `oob` hit to steps [450,500)** — the exact 50-step
    window of the iter-500 injection — and classified it `out_of_bounds`.
- **`549a37f` — jetson/compute/cuda_particles/README.md**
  - New "Inducing SEEs without a beam" section: the `--inject` table + verified
    commands, random-placement via `--inject-index`/`--inject-bit`, the
    `systemctl kill` path for shutdown/restart types, golden-corruption to flag every
    epoch, and the **subtype note** (panel/CSV label by the final checkpoint with
    `mismatch` first, so a renormalizing `oob` reads as `golden_mismatch`;
    `see_dump_triage.py` is the authoritative subtype). States plainly that whole-SoC
    random corruption is blocked from userspace (`CONFIG_STRICT_DEVMEM`) and *is* the
    beam — `--inject` validates detectors, the beam validates system response.

### Full post-processing data after EVERY test; live panel stops replaying history

- Paired coordinator change (teammate repo, `ac95dca`):
  - `coordinator/ui.py` + `app_local_tcp.py`: new **`--pull-script`** (opt-in). Once
    a STOP is accepted — manual *or* the §6a duration auto-stop — and the CSV is
    written, the GUI runs `pull_logs.sh` with **`PULL_MODE=full`** in a daemon
    thread, fetching the ~10 MB per-SEE state dumps, pstore and golden table that
    the in-run `live` pulls skip. This closes the gap where dumps only arrived at
    *arbiter* shutdown: now every test ends with its CSV **and** its complete
    offline-analysis payload. Off the Tk thread so a multi-minute rsync can't freeze
    the GUI; missing `bash`, non-zero rc and timeouts are reported into the activity
    log, never raised. Requires the GUI to run on the arbiter box (bash+rsync+key);
    omit the flag and nothing is attempted.
  - `coordinator/see_monitor.py`: **`SeeLogTailer` now primes to end-of-file on its
    first poll.** Previously the panel replayed *every* historical SEE in the mirror
    on GUI start — prior runs, or hand-seeded demo lines — as though they had just
    occurred, which would make old events look like live ones during a beam run.
    Now only events appended after the monitor starts are shown; `from_start=True`
    restores replay for tests/forensics.
  - Tests +1 (**63 pass**). Verified headless: history suppressed, a newly appended
    event still shown, and the pull invoked with `PULL_MODE=full` plus the correct
    `DUT_HOST`/`LOCAL_LOG_DIR`.
- **`5ac9f25` — docs/FLASH_AND_BRINGUP.md**
  - Tailscale enrollment step now also sets the **node name**
    (`sudo tailscale set --operator=melagen --hostname=orin-nano-0N`) — a clone
    otherwise reports the image's old hostname, so all 7 boards appear as `ubuntu`
    with colliding MagicDNS names. Notes that `tailscale status` shows the old name
    briefly (propagation lag) and to confirm via `--json` `Self.DNSName`.
  - Documented the **known-benign connmark health warning** seen on every board:
    the L4T kernel ships no `xt_connmark` module, so Tailscale can't install its
    packet-mark rules. Those matter only for subnet routers / exit nodes, which this
    campaign doesn't use — plain node-to-node SSH/rsync is unaffected (verified on
    the master). Explicitly warns *against* "fixing" it by switching to
    `iptables-nft` before an image freeze, and notes the message is inherited by all
    clones rather than being a per-board fault.

### Pull modes: light JSONL-only pulls during a run, heavy dumps at the end

Requested by the team: *"only send whether or not an SEE was detected (less data),
then send the full post processing data at the end — I don't want to send over
heavy workload mid test."*

- **`cd55ef6` — arbiter/pull_logs.sh**
  - New **`PULL_MODE`** env (default **`live`**). In `live` the periodic pull moves
    **only the structured JSONL event logs** — every SEE is still fully *reported*
    there (`see_event` / anomalous `checksum` / `mem_upset`, a few hundred bytes
    each), which is exactly what the coordinator's live panel tails, so live
    monitoring loses nothing. It **excludes `see_dumps/` and `*.bin`** (each SEE
    state dump is ~10 MB = 20 checkpoints × 512 KB) and skips pstore + the
    golden/particles sidecars, then exits early. Mid-test transfer drops from
    tens of MB to ~kB, and DUT CPU (rsync read + SSH encrypt + `-z` gzip on 10 MB
    binaries) drops with it — the workload under test is no longer perturbed by
    log traffic.
  - `PULL_MODE=full` is the end-of-run pull: dumps, pstore, golden table, config —
    everything `see_dump_triage.py` needs. Run once after a test: `PULL_MODE=full
    bash pull_logs.sh`.
  - Verified both paths (live early-exits with JSONL only, exit 0; full attempts
    sidecars + pstore) and that a failed pull still exits 0 and never blocks.
- **`cd55ef6` — arbiter/arbiter_main.py**
  - `--pull-mode {live,full}` (default `live`) for the periodic pull; the mode is
    passed through to the script and recorded on every `PULL` correlator record.
  - **Automatic end-of-run `full` pull on shutdown** (after the pull thread stops,
    so it can't race a periodic pull), preceded by a `FINAL_PULL_START` record —
    this is what actually retrieves the SEE dumps for offline analysis.
    `--no-final-pull` opts out; `--final-pull-timeout` (default 900 s) allows for
    many 10 MB dumps. Refactored the pull into reusable `run_pull()` / `_pull_env()`.
  - **Caveat:** the arbiter stays up across many tests, so the automatic full pull
    fires at *arbiter* shutdown, not after each run. Between runs in one session,
    the operator runs `PULL_MODE=full bash pull_logs.sh` by hand (documented in the
    script header).

### SEE post-processing: offline dump triage tool + pull the per-board golden table

- **`c57e53e` — jetson/compute/cuda_particles/tools/see_dump_triage.py** (new)
  - First actual post-processing code for the SEE state dumps (previously the
    offline reconstruction existed only as a README concept). Stdlib-only CLI, runs
    on a laptop against a pulled `arbiter_logs/compute/` tree. Per dumped epoch it:
    re-hashes every dumped checkpoint (FNV-1a 64 over pos-then-vel bytes, exactly
    `checksum.cpp::hashState`) against the board's golden table → **localises the
    upset to the first divergent checkpoint** (a `dump_stride`-iteration window,
    vs. detection's "somewhere this epoch"); scans NaN/Inf + max|pos| per checkpoint
    → classifies the upset as `silent_bit_corruption` / `numeric_blowup` /
    `out_of_bounds`; flags truncated/missing dumps and dump-less records. Human
    report + `--json`. Verified on a synthetic dump (bit-flip at checkpoint 2 →
    correctly reported first-divergence=2, window [100,150), silent corruption).
  - **Documented limitation:** 1-vs-2+ upsets in one epoch (grouped SEEs) still
    needs a reference-board replay (same build, bit-exact determinism); the dump +
    golden + config carry everything that replay needs.
- **`c57e53e` — arbiter/pull_logs.sh**
  - The golden table (`data/golden_hashes.txt`) is **per-board and git-ignored** —
    it lived only in the repo tree on the DUT, so a pulled log tree could not be
    post-processed off-board. Now best-effort rsyncs `golden_hashes.txt` **and** the
    active `config/particles.json` into `${LOCAL_LOG_DIR}/compute/` (new
    `DUT_REPO_DIR` env, default `/home/melagen/see-testsuite`), making the pulled
    tree self-sufficient for `see_dump_triage.py`. Missing files never fail the run.
- Paired coordinator change (live panel, teammate repo, `660ab7d`):
  `see_monitor.py` now surfaces `see_event` records as a **"Post-processing dump"**
  line (`epoch N -> see_dumps/epoch_N_iter_M.bin`, or `NO dump saved`) and appends
  the dump path to `sim_fault` lines — the operator sees in real time whether each
  SEE has offline-analysis data. Live-panel-only key `see_dump_saved` (never in the
  summary/CSV counts). Tests +3 (**62 total pass**).

### §6a: test runs auto-last a configurable duration (default 100 s), DUT-owned timer

- **`72a7b00` — jetson/control/test_control.py**
  - START_TEST now accepts an optional **`duration_s`** (default `default_duration_s`,
    100). `validate()` rejects a non-positive / non-numeric / out-of-range value
    (bool is rejected explicitly since it is an `int` subclass; cap `max_duration_s`
    = 86400 s).
  - **DUT owns the run timer** (robust to network blips): on START the receiver
    (re)starts the channels, then a daemon `threading.Timer` fires after `duration_s`
    and runs `auto_stop()` — the same work a manual STOP does (disarm ARMED,
    `systemctl stop` each channel, `summarize_run()`, log an `auto_stop` control-log
    record). A manual STOP_TEST still works and **cancels** the pending timer; a new
    START **replaces** any timer still pending. `auto_stop()` guards against racing a
    manual STOP via the `auto_stop_run_id` marker, so a stale callback is a no-op.
    The START ack now echoes `duration_s`.
  - The **summary/CSV path is unchanged**: the coordinator's STOP (manual or the
    auto-STOP below) re-scans the persisted logs via `summarize_run()`, so it returns
    the same summary whether or not the DUT's own timer already stopped the services
    (`systemctl stop` is idempotent).
- Paired coordinator change (teammate repo `melagen-test-coordinator`, `660ab7d`):
  - `coordinator/constants.py`: add `DEFAULT_DURATION_S` (100) and `MAX_DURATION_S`
    (86400).
  - `coordinator/request.py`: `TestRequest` gains a `duration_s` field;
    `TestRequest.create(..., duration_s=DEFAULT_DURATION_S)` validates it positive and
    within the cap (same rules as the DUT). It ships in the START payload.
  - `coordinator/ui.py`: new **"Test Duration (s)"** entry (default 100, editable only
    while IDLE, validated on Start). On START-accepted the GUI arms a **mirror
    auto-STOP** via `master.after(duration_s*1000)` that reuses the normal STOP path
    (no operator click) so it collects the summary/CSV and returns to IDLE; the DUT
    timer remains the authoritative stop. Manual Stop / a new run cancels the mirror.
    Status line and Start/Stop dialogs now show the duration.
  - `receiver/test_receiver.py` (local mock DUT): `duration_s` added to
    `START_REQUIRED_FIELDS` and validated, so the exact-field check accepts the new
    payload for local GUI testing.
  - Tests: +8 (`tests/test_request.py`, `tests/test_receiver.py`) covering duration
    default/custom/reject cases. DUT `validate()` + timer schedule/cancel/replace
    verified in isolation.

### §6b: live SEE panel on the coordinator via log-tailing (no new DUT push)

- Paired coordinator change (teammate repo `melagen-test-coordinator`, `660ab7d`):
  - New `coordinator/see_monitor.py`: `SeeLogTailer` tails the arbiter's local
    `arbiter_logs/{compute,memory}/*.jsonl` mirror (the existing **radpull** rsync,
    `arbiter/pull_logs.sh`) — **no network access in the coordinator**. Per-file byte
    offsets mean each poll returns only NEW events (no re-printing); a shrunk file
    (rotation) resets its offset; a partial trailing line is held back so a mid-append
    record is never split. `classify_see()` maps each record to the stable
    `SEE_TYPE_LABELS` keys **by field** (mirroring the DUT `summarize_run`, so the
    live tally agrees with `test_N.csv`): anomalous final-checkpoint `checksum` →
    `cuda_golden_mismatch`/`cuda_nonfinite`/`cuda_anomaly`; `sim_fault` →
    `cuda_shutdown`; `mem_upset` → `gpu_mem_upset`; `status:error` → `fatal_error`.
    Keying on the one-per-epoch checksum (not the paired `see_event` marker) counts
    each epoch once while keeping the subtype.
  - `coordinator/ui.py`: new **"Live SEEs"** panel that polls every **2.5 s**
    (`SEE_POLL_MS`, interval stated in the panel header) via `master.after` and
    appends new events as `ts  jetson_id  <label>  (detail)`. Reads from
    `see_log_root` (default `./arbiter_logs`).
  - `app_local_tcp.py`: `--see-log-root` flag (default `arbiter_logs`) points the
    panel at the arbiter's mirror.
  - `tests/test_see_monitor.py`: +11 tests (classify + tailer new-only / partial-line
    / rotation / missing-root). **All 59 coordinator unit tests pass**; headless GUI
    construction + live-poll smoke test passes.
  - **Accepted caveat** (documented in `see_monitor.py`): near-real-time — latency =
    the poll interval, and an SEE that crashes the board isn't flushed+pulled until
    after reboot (reconstruct from pstore/boot logs then). Sub-second per-event pops
    would need a DUT→arbiter UDP/TCP push instead.

## 2026-07-31

### coordinator: GUI can target a real DUT (--host); manual §4 uses it

- **`5cfd652` — docs/FLASH_AND_BRINGUP.md**
  - §4 launch step now uses the real `--host` flag instead of "edit the launcher":
    `python app_local_tcp.py --host 192.168.1.20` (or the board's Tailscale
    IP/name). Notes that the GUI status line shows the live target so the operator
    verifies which board a run hits, and that `app.py` is faked mock mode.
- Paired coordinator change (teammate repo `melagen-test-coordinator`,
  `b42ef77`): `app_local_tcp.py` gained `--host/--port/--timeout` (default host
  `192.168.1.20`) — it was hardcoded to `127.0.0.1`, so the GUI previously could
  not reach a real Jetson at all. `coordinator/ui.py` now surfaces the transport
  target (`host:port`) in the status line + activity log. All 40 coordinator unit
  tests pass. **This is what makes the §4 GUI acceptance test actually reach a
  board** — before, the only working paths were mock (faked ACCEPTED) or localhost.

### docs: §4 acceptance test is now GUI-driven with a concrete CSV expected-result

- **`3c32b7b` — docs/FLASH_AND_BRINGUP.md**
  - Reframed §4 so the **acceptance test mirrors beam day**: drive the run from the
    real **coordinator GUI** (Start → run → Stop → read result), with the four
    interface checks demoted to fault-isolation diagnostics and the bare-laptop
    Python snippets demoted to a fallback (were previously the lead).
  - Documented launching the GUI against the DUT: `app_local_tcp.py` ships
    hardcoded to `127.0.0.1:6000`, so the operator sets the TcpTransport `host` to
    the board (`192.168.1.20` Ethernet / `100.x.y.z` Tailscale); `app.py` is
    mock-mode and must not be used. Noted the DUT's `test_control.service` is the
    real listener that starts/stops workloads.
  - Added an explicit **step 5 — verify the result artifact**: open
    `results/test_<N>.csv` and check `jetson_id` = the board, `run_id` matches, and
    the SEE counts are present. Included an example CSV matching the coordinator's
    actual `_save_result_csv` writer (field/value rows + a `see_type,label,count`
    by-type block). 0 counts are a pass with no beam (proving plumbing, not upsets).

### docs: Appendix A installs Tailscale on a from-scratch board; spelling

- **`c42d841` — docs/FLASH_AND_BRINGUP.md**
  - Appendix A (flash from scratch) assumed `tailscale` already existed — true
    only for clones, which inherit it from the master image; a freshly flashed
    board has no `tailscale` binary. Added the install step
    (`curl -fsSL https://tailscale.com/install.sh | sh`) + `tailscale up` to the
    "First boot + full setup" section so the rebuild-master path is complete.
  - Standardized spelling to US "enroll/enrollment" (was mixed with the British
    single-l "enrol/enrolment").

### docs: manual §3 — full Tailscale per-board enrollment procedure

- **`80783cd` — docs/FLASH_AND_BRINGUP.md**
  - §3 previously only *mentioned* Tailscale ("each board needs its own
    `tailscale up`"). Added a dedicated, clearly-flagged subsection with the real
    procedure and the clone gotcha: because the master (board 1) is already
    authenticated, its Tailscale identity (`/var/lib/tailscale/tailscaled.state`)
    is baked into the image, so every clone must **reset that state first**
    (`stop tailscaled → rm state → start`) before `tailscale up`, or two boards
    collide on one node identity.
  - Bolded the two values that are **manual and unique per board**: the per-board
    **login URL** printed by `tailscale up`, and the **Tailscale IP** (`tailscale
    ip -4`) that must be looked up and recorded (it's the SSH target). Documented
    the reusable-auth-key shortcut that removes the browser step (key is a secret,
    not committed).
  - Updated the "what differs per board" table + prose: there are **two**
    per-board actions — `setup-board.sh NN` (number only) **and** Tailscale
    enrollment — not one.

### setup: fold SSH-host-key + machine-id regen into setup-board.sh; manual to match

- **`a668bc8` — scripts/setup-board.sh**
  - Step 1 (identity) now regenerates the two remaining per-clone items that need
    no operator input: **SSH host keys** (`rm -f /etc/ssh/ssh_host_* && ssh-keygen
    -A && systemctl restart ssh`, step 1b) and the **machine-id** (`rm -f
    /etc/machine-id /var/lib/dbus/machine-id && systemd-machine-id-setup`, step 1c),
    alongside the existing hostname set. Finalizing a clone is now truly **one
    command** — no manual hygiene block. CLI contract unchanged (`setup-board.sh
    <NN>`). Re-running rotates the SSH host keys again (harmless; re-accept on next
    connect). Does not touch `boot_id`, so the science logs are unaffected.
- **`a668bc8` — docs/FLASH_AND_BRINGUP.md**
  - §3 rewritten to match: the single `setup-board.sh NN` now covers hostname, SSH
    host keys, machine-id, and golden — the "Clone hygiene the script does NOT do"
    section is gone. Difference table updated (SSH host keys + machine-id now
    `setup-board.sh`, not manual); only Tailscale enrollment remains outside it.
  - §0 checklist + §1 state block: board 1 marked **master-ready and
    Ethernet-tested** (named `orin-nano-01`, hardened, §4 checks pass per operator)
    — it's now the validated master to clone from.

### docs: manual §3 — one command per clone + full per-board difference list

- **`1462788` — docs/FLASH_AND_BRINGUP.md**
  - Rewrote §3 to make the per-board finalize explicit: **one** `setup-board.sh NN`
    per clone (operator supplies the number → hostname + own golden + re-arm), and
    added the **clone-hygiene** steps the script does *not* do — regenerating each
    clone's **SSH host keys** and **machine-id** (a raw clone shares the master's;
    harmless to the logs, which key off hostname + per-boot `boot_id`, but untidy
    for SSH). Added a table of everything that differs per board (hostname, golden,
    SSH host keys, machine-id, Tailscale enrollment) vs. what's identical from the
    master image, and a note on reaching headless clones over the network.

### control: include jetson_id (hostname) in the post-test summary/CSV

- **`eb11bc4` — test_control.py**
  - `summarize_run()` now adds `jetson_id` (the board hostname) to the summary
    block returned on STOP, so the coordinator's per-test CSV records which board
    produced the run. Additive/backward-compatible; the top-level reply already
    carried `jetson_id`, this puts it in the summary the CSV writer consumes.
  - Paired coordinator change (teammate repo `melagen-test-coordinator`,
    `coordinator/ui.py`): `_save_result_csv` writes a `jetson_id` row at the top
    of `results/test_N.csv`.

### docs: correct bring-up manual §1 with board 1's verified state

- **`c3afebc` — docs/FLASH_AND_BRINGUP.md**
  - Checked board 1 (`100.122.15.91`) directly and corrected §1 to match: it is
    **already on the clone model** (all units point at `~/see-testsuite`; the
    scp-layout "cut it over first" caveat and the stale `DEPLOYMENT.md` 2026-07-30
    snapshot are dropped). Remaining master-prep work is just the **hostname**
    (still `ubuntu` → `orin-nano-01`) and **beam hardening** (`kernel.panic=0`, no
    watchdog, graphical target). Replaced the assumed current-state note with a
    verified one.

### docs: reframe the bring-up manual around the existing master board

- **`92e8759` — docs/FLASH_AND_BRINGUP.md**
  - Restructured so **board 1 already being flashed/working is the starting
    point**, not a from-scratch flash. Main path is now: (§1) bring the existing
    dev board fully to spec as the **master** — clone-model software, `radpull` +
    logs, beam hardening — including the note to cut it off the legacy scp layout
    first; (§2) **clone the master** to boards 2–7 via `l4t_backup_restore.sh`;
    (§3) **finalize each clone** (hostname, regenerate its own golden, re-arm);
    (§4) **test each over Ethernet**.
  - Flashing a board from bare metal moved to **Appendix A** (rebuild the master
    or replace a dead board only). Added a per-board fleet-status checklist and
    kept the error table + sources.

### docs: full flash → bring-up → test manual (FLASH_AND_BRINGUP.md)

- **`d2482b8` — docs/FLASH_AND_BRINGUP.md (new)**
  - Expanded the team's Jetson cloning reference into a full-lifecycle manual:
    bare board → **flash** (SDK Manager, JetPack 6.2.2, NVMe, Force Recovery, host
    prep) → **one-time OS setup** (`radpull` user + arbiter key, `/var/log/radtest`
    tree, beam hardening via CRASH_RECOVERY, pstore, direct-Ethernet static IP) →
    **deploy our software** (`setup-board.sh NN`: clone, CuPy, build, golden, arm,
    services) → **test over Ethernet** (the 4 interface checks + full dry run) →
    **clone to units 2–7** (the original `l4t_backup_restore.sh` / `flash.sh -G`
    research, kept intact).
  - Includes an end-to-end "campaign-ready" checklist, the known-errors table
    (flash/clone host-environment gotchas + the CuPy numpy pin), team
    recommendations, and all original NVIDIA/forum sources.
  - Ties together existing docs (DEPLOYMENT, SERVICES, INTEGRATION_TEST,
    CRASH_RECOVERY, PSTORE_SETUP, DEPENDENCIES) as the ordered path through them;
    no code touched, no live board changes.

### docs: crash-recovery runbook — arm watchdog, fast panic reboot, headless

- **`c102490` — docs/CRASH_RECOVERY.md (new)**
  - Measured the board's recovery posture (`systemd-analyze`, watchdog/panic
    state) and found two "hang forever" gaps: **no hardware watchdog is running**
    (a hard board hang/latchup never auto-reboots) and **`kernel.panic=0`** (a
    panic hangs instead of rebooting). Boot itself is fine at 15.5 s.
  - New runbook documents the one-time board settings that close both, plus
    going headless now that control is confirmed remote-only:
    1. Arm `/dev/watchdog` via systemd (`RuntimeWatchdogSec=10s` drop-in) — no
       need to build the vendored `watchdogd`; hard hang → reset in ~10 s.
    2. `kernel.panic=1` sysctl — panic → reboot in ~1 s, evidence preserved via
       pstore + `boot_log.jsonl`.
    3. `set-default multi-user.target` (drop the unused on-board desktop) and
       `mask apt-daily*` (stop unattended package changes mid-campaign).
  - No live board changes and no code touched — procedure + exact `sudo` steps
    for the operator, like `PSTORE_SETUP.md`. Process-crash recovery
    (`Restart=always`) and post-reboot re-arm (`ARMED` flags) were already in
    place; this covers the whole-board and panic paths.

### control: STOP reply returns a per-run SEE summary (popup + CSV data)

- **`0e93b47` — test_control.py, config/test_control.json, CONTROL_INTERFACE.md**
  - On STOP_TEST the receiver now scans each channel's JSONL log for the finishing
    run and returns a `summary` block in the ack: `duration_s`, `total_sees`,
    `sees_per_s`, and a per-type breakdown. Added a `log` path per channel in the
    config so the receiver knows where each channel's `.jsonl` is.
  - SEE taxonomy (each SEE attributed to exactly one type, so `by_type` partitions
    `total_sees`): `cuda_golden_mismatch` (compute `mismatch:true`), `cuda_nonfinite`
    (`finite:false`), `cuda_anomaly` (`anomaly:true`), `gpu_mem_upset` (one per
    `mem_upset` record), `cuda_shutdown`/`mem_tester_restart` (extra `start` records
    = systemd restarted a crashed service mid-run), `fatal_error` (`status:error`).
    Duration = first→last log timestamp for the run_id. Best-effort: a log failure
    yields `summary.error` and never fails STOP.
  - Verified with a deterministic synthetic-log test (counts, run filtering,
    restart-as-shutdown, duration, rate all correct). Consumed by the coordinator
    GUI (teammate repo) for a post-test popup + auto-incrementing `test_N.csv`.
    Requires redeploying test_control on the board.

### control: reply status must be ACCEPTED/REJECTED for the coordinator GUI

- **`030ba5b` — test_control.py, CONTROL_INTERFACE.md**
  - **Bug found by running the real coordinator GUI against the board:** START
    came back "Receiver rejected the command". Root cause — `coordinator/ui.py::
    _validate_response` accepts a reply **only if `status == "ACCEPTED"`** and
    otherwise shows the reply's `error` field; our receiver was replying
    `status: "ok"`/`"error"`, so the GUI treated every reply as a rejection.
  - Our headless round-trip (using the same `TcpTransport`) had passed because
    `TcpTransport.send` only validates that `request_id` echoes — the `ACCEPTED`
    check lives in the UI layer, above the transport. Lesson: a raw-socket test is
    necessary but not sufficient; the GUI is the real acceptance gate.
  - Fix (ours — we adapt to the coordinator's contract): reply `status: "ACCEPTED"`
    on success and `status: "REJECTED"` + an `error` string on failure, across all
    paths (invalid JSON, validation errors, start, stop; duplicate = ACCEPTED). Doc
    updated with the vocabulary and the why. Requires redeploying test_control on
    the board (`git pull` + `systemctl restart test_control.service`).

### docs: mark Phase 4 (log pull) verified from the operator laptop

- **`c74e766` — docs/INTEGRATION_TEST.md**
  - Phase 4 auth + transfer **verified on 2026-07-31**: the operator's Windows
    laptop ed25519 pubkey was installed in `radpull`'s `authorized_keys`, and both
    `ssh radpull@… ls /var/log/radtest` and `scp -r … /var/log/radtest` succeeded
    (pulled `cuda_particles.jsonl` ~1.09 MB, `mem_check_gpu.jsonl` ~54 KB, and both
    heartbeats). Windows has no `rsync`, so `scp` stood in for validation; the
    production incremental pull runs `rsync` from the Linux arbiter.
  - Results table row 4 → ✅. Open items note the per-board, per-machine nature of
    `authorized_keys`: when the pull moves to Daniel's machine, its pubkey must be
    appended on all 7 boards (fold into `setup-board.sh`).

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
