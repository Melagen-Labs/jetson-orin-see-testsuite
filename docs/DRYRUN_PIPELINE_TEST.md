# Full-Pipeline Dry-Run (synthetic SEEs) — Ethernet

A step-by-step to run a **normal GUI test that produces SEEs**, so you can watch
the whole pipeline work before beam day: **DUT workload → arbiter log-pull → live
SEE panel → results CSV**. The SEEs are synthetic (config-driven "chaos"), clearly
tagged, and turned back off at the end — then a second, clean test proves they're
off. **Everything here is over Ethernet — no Tailscale.**

> **Addresses** (the scripts' defaults — change both together if your segment
> differs): **DUT = `192.168.1.20`**, **your computer / arbiter = `192.168.1.10`**,
> control port `6000`, heartbeat `5555`.

> **Beam-energy note:** the coordinator GUI shows the campaign energies
> `50, 63, 125, 200` MeV — all accepted by the DUT, so **pick any of them**. (The
> coordinator was synced to the DUT's campaign plan; the retired `53`/`100` values
> are gone from the dropdown.)

## 0. Before you start
- The DUT (`orin-nano-01`) is set up, and its `cuda_particles` binary supports
  config chaos (repo commit `bd829ae` / rebuilt on the board 2026-08-01).
- **Both repos are cloned side-by-side in one folder**, which the launcher expects:
  ```
  radtest-arbiter/
    jetson-orin-see-testsuite/   <- this repo (start_arbiter.py + heartbeat listener)
    melagen-test-coordinator/    <- the GUI
  ```
- The `radpull` pull key works to the DUT (default `~/.ssh/id_ed25519`; pass
  `--ssh-key` otherwise).
- You can `ssh melagen@192.168.1.20` (the `melagen` password is needed for the
  chaos-toggle steps; nothing here needs `sudo`).

## 1. Connect Ethernet and set static IPs
1. Plug the Ethernet cable between the DUT and your computer (or both into a switch).
2. Set static IPs on the wired interface: your computer **`192.168.1.10`** (mask
   `255.255.255.0`); DUT **`192.168.1.20`** (already configured on the board).
3. Confirm the link **both ways**:
   ```bash
   ping -c2 192.168.1.20                                 # you -> DUT
   ssh melagen@192.168.1.20 "ping -c2 192.168.1.10"      # DUT -> you
   ```

## 2. Power on the DUT and check the stack
```bash
ssh melagen@192.168.1.20 "for s in test_control cuda_particles mem_check_gpu heartbeat_sender boot_state_logger; do printf '%-18s %s\n' \$s \$(systemctl is-active \$s.service); done; ss -ltnp | grep :6000"
```
`test_control` must be `active` and **listening on `:6000`**. ARMED workloads may
show inactive when idle — fine; `Start Test` arms and starts them.

## 3. Turn chaos ON in the DUT config (the dry-run knob)
Makes the *next* test emit synthetic SEEs. No `sudo`:
```bash
ssh melagen@192.168.1.20 "python3 -c \"import json,pathlib; p=pathlib.Path.home()/'see-testsuite/jetson/compute/cuda_particles/config/particles.json'; c=json.loads(p.read_text()); c['chaos']=True; c['chaos_prob']=0.0002; p.write_text(json.dumps(c,indent=2)+chr(10)); print('chaos =', c['chaos'], 'prob =', c['chaos_prob'])\""
```
`chaos_prob 0.0002` ≈ **1 SEE per ~50 epochs** (sparse, beam-like). Use `0.001` for
denser/faster SEEs.

## 4. Start the whole arbiter + GUI — one command
From the `jetson-orin-see-testsuite` repo folder:
```bash
cd .../jetson-orin-see-testsuite
python arbiter/start_arbiter.py --host 192.168.1.20
```
This single command launches, in order:
1. the **heartbeat listener** in its own window — watch `seq` climb (DUT → arbiter
   heartbeat working),
2. a **live scp log-pull loop** every 3 s → `melagen-test-coordinator/arbiter_logs/`
   (feeds the panel; scp ships with Windows OpenSSH, no rsync needed),
3. the **coordinator GUI** in the foreground.

*(If the coordinator repo isn't a sibling, add `--coordinator-dir <path>`. If your
pull key isn't `~/.ssh/id_ed25519`, add `--ssh-key <path>`.)*

## 5. Run the CHAOS test (should produce SEEs)
1. In the GUI pick a **Beam Energy** (any campaign value — `50 / 63 / 125 / 200`),
   Shielding / Thickness, and a **Test Duration** (e.g. 120 s), click **Start Test**
   → confirm. The activity log must show `"status":"ACCEPTED"`.
2. **Watch the Live SEEs panel** — within a few seconds it shows
   `... orin-nano-01  CUDA sim: golden-hash mismatch (epoch N)` lines. That's the
   mid-run, real-time SEE feed.
3. Let it auto-stop (or click **Stop Test**) → summary popup + `results/test_<N>.csv`
   (under `melagen-test-coordinator/`). **Note this file — call it the chaos run.**

## 6. Turn chaos OFF
```bash
ssh melagen@192.168.1.20 "python3 -c \"import json,pathlib; p=pathlib.Path.home()/'see-testsuite/jetson/compute/cuda_particles/config/particles.json'; c=json.loads(p.read_text()); c['chaos']=False; p.write_text(json.dumps(c,indent=2)+chr(10)); print('chaos =', c['chaos'])\""
```
Must print `chaos = False`.

## 7. Run a SECOND test to confirm chaos is really off (expect 0 SEEs)
Don't take the flag on faith — prove it. Run another test **exactly like step 5**
(same beam energy, Start → let it run → Stop). With chaos off, a clean workload flags
**no epochs**, so you should see:
- the **Live SEEs panel stays empty** for this run (no new SEE lines);
- the **new CSV shows `total_sees = 0`** (`results/test_<N+1>.csv`);
- the DUT's new records carry **no** `chaos:true` and **no** `synthetic_run` marker:
  ```bash
  ssh melagen@192.168.1.20 "grep -c synthetic_run /var/log/radtest/compute/cuda_particles.jsonl"
  ```
  (the count should be the same as after step 5 — i.e. it did **not** increase).

**If this second test shows 0 SEEs, chaos is confirmed off and the board is safe for
a real run.** If it still shows SEEs, chaos didn't take — redo step 6, confirm it
printed `chaos = False`, and repeat step 7.

## 8. Verify the whole thing worked
You now have two CSVs — the contrast is the proof:

| | Chaos run (step 5) | Clean run (step 7) |
|---|---|---|
| `total_sees` in CSV | **> 0** (mostly `cuda_golden_mismatch`) | **0** |
| Live panel during run | SEE lines streamed | stayed empty |
| DUT log tag | `chaos:true` / `synthetic_run` present | absent |

Also confirm the transport itself:
- **Heartbeat window** (step 4) showed `seq` climbing throughout both runs.
- **Arbiter mirror matches the DUT** (proves the pull):
  ```bash
  grep -c '"chaos":true' melagen-test-coordinator/arbiter_logs/compute/cuda_particles.jsonl
  ```

**Optional — SEE dumps / triage:** the launcher pulls **JSONL only**. To also pull
the ~10 MB dumps from the chaos run and triage them:
```bash
PULL_MODE=full DUT_HOST=192.168.1.20 LOCAL_LOG_DIR=melagen-test-coordinator/arbiter_logs bash jetson-orin-see-testsuite/arbiter/pull_logs.sh
python3 jetson-orin-see-testsuite/jetson/compute/cuda_particles/tools/see_dump_triage.py --logs melagen-test-coordinator/arbiter_logs/compute
```

## 9. Done
- Confirm **step 6 printed `chaos = False`** and **step 7 showed 0 SEEs**.
- **Close the GUI** — that stops the pull loop. Close the heartbeat window yourself.
- Synthetic records from the chaos run stay in the DUT log tagged `chaos:true` /
  `synthetic_run`; wipe before a real campaign if you want a clean log
  (operator/sudo: `sudo truncate -s0 /var/log/radtest/compute/cuda_particles.jsonl`).

---

### Quick reference
| Thing | Value |
|---|---|
| DUT IP / port | `192.168.1.20` : `6000` |
| Your computer (arbiter) IP | `192.168.1.10` |
| Start everything | `python arbiter/start_arbiter.py --host 192.168.1.20` |
| Beam energy to pick | any campaign value (`50 / 63 / 125 / 200`) |
| Enable chaos | step 3 (`chaos:true`, `chaos_prob 0.0002`) |
| **Disable chaos** | **step 6 (`chaos:false`)** |
| **Confirm off** | **step 7 — second test must show 0 SEEs** |
| Pass = | chaos run CSV `total_sees > 0`, clean run CSV `total_sees = 0` |
