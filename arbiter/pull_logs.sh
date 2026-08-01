#!/usr/bin/env bash
# pull_logs.sh -- arbiter-side log puller (build plan section 0 step 3, section 4 step 6)
#
# Rsyncs the DUT's memory/, compute/, and boot_state/ log directories plus
# /sys/fs/pstore/* to the arbiter's local log tree over SSH key auth. Safe to run
# unattended and repeatedly -- from arbiter_main.py on a timer, or from cron, or
# by hand after the DUT's ethernet reconnects. It never blocks: if the DUT is
# down/rebooting, the failing rsync is logged and the script still exits 0 so the
# next scheduled pull just tries again.
#
# ---- DUT-side one-time setup (the low-privilege pull user) -------------------
#   On the DUT, create a dedicated read-only user for log pulls:
#     sudo useradd -m -s /bin/bash radpull
#     sudo usermod -aG adm,systemd-journal radpull    # read /var/log
#   On the arbiter, generate a dedicated key pair (no passphrase, unattended):
#     ssh-keygen -t ed25519 -f ~/.ssh/radtest_pull -N ''
#   Install the public key on the DUT:
#     ssh-copy-id -i ~/.ssh/radtest_pull.pub radpull@<DUT_IP>
#   Confirm passwordless pull works even right after a DUT reboot:
#     ssh -i ~/.ssh/radtest_pull radpull@<DUT_IP> true
#
#   Note on pstore: /sys/fs/pstore is typically root-readable only. If you want
#   the panic/console dumps pulled by this low-priv user, either add a narrow
#   sudoers rule for a copy step, or a udev/tmpfiles rule that relaxes read
#   perms on /sys/fs/pstore. Otherwise pull pstore in a separate root-authorized
#   step. The pstore rsync below is best-effort and won't fail the run.
#
# ---- Pull modes: keep the DUT light DURING a run ----------------------------
#   PULL_MODE=live (default) -- the periodic in-run pull. Transfers ONLY the
#     structured JSONL event logs: every SEE is already fully *reported* there
#     (see_event / checksum / mem_upset records, a few hundred bytes each), which
#     is exactly what the coordinator's live SEE panel tails. Explicitly EXCLUDES
#     see_dumps/ -- each SEE state dump is ~10 MB (dump_checkpoints x
#     floats_per_checkpoint x 4 B) and is post-processing data, not something the
#     operator needs mid-beam. Also skips pstore + the golden/config sidecars.
#     Net effect: a live pull moves ~kB and costs the DUT almost no CPU, so the
#     workload under test isn't perturbed by log traffic.
#   PULL_MODE=full -- the end-of-run pull. Everything: the dumps, pstore records,
#     and the per-board golden table + particles.json that see_dump_triage.py
#     needs. Run this ONCE after a test finishes (beam off), e.g.
#         PULL_MODE=full bash pull_logs.sh
#     arbiter_main.py also fires one automatically when it shuts down.
#
# ---- Configuration (environment variables, with defaults) -------------------
set -euo pipefail

PULL_MODE="${PULL_MODE:-live}"

DUT_HOST="${DUT_HOST:-192.168.1.20}"
DUT_USER="${DUT_USER:-radpull}"
DUT_LOG_DIR="${DUT_LOG_DIR:-/var/log/radtest}"
DUT_REPO_DIR="${DUT_REPO_DIR:-/home/melagen/see-testsuite}"
LOCAL_LOG_DIR="${LOCAL_LOG_DIR:-./arbiter_logs}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/radtest_pull}"
PSTORE_DIR="${PSTORE_DIR:-/sys/fs/pstore}"

SSH_OPTS="-i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new"

mkdir -p "${LOCAL_LOG_DIR}"

# Transfer backend: rsync if present (incremental --append-verify, the canonical
# choice on a Linux arbiter); otherwise scp, which ships with Windows OpenSSH so a
# Windows arbiter needs no extra install. scp re-copies whole files -- negligible
# for the small JSONL event logs; the large see_dumps only move once, in the full
# pull. Set FORCE_SCP=1 to use scp even where rsync exists.
HAVE_RSYNC="$(command -v rsync 2>/dev/null || true)"
if [ -n "${FORCE_SCP:-}" ]; then HAVE_RSYNC=""; fi

# In live mode, skip the heavy SEE state dumps -- the JSONL still reports every
# SEE, so live monitoring loses nothing; only the offline-analysis payload waits.
LIVE_EXCLUDES=()
if [ "${PULL_MODE}" != "full" ]; then
    LIVE_EXCLUDES=(--exclude=see_dumps/ --exclude='*.bin')
fi

# Pull one channel dir. rsync syncs the whole dir (with live excludes); the scp
# fallback pulls only *.jsonl in live mode (skipping dumps) and the whole dir in
# full mode -- same net effect as the rsync excludes.
pull_channel() {   # $1 = sub-dir (memory|compute|boot_state)
    local sub="$1"
    mkdir -p "${LOCAL_LOG_DIR}/${sub}"
    if [ -n "${HAVE_RSYNC}" ]; then
        rsync -az --append-verify \
            "${LIVE_EXCLUDES[@]+"${LIVE_EXCLUDES[@]}"}" \
            -e "ssh ${SSH_OPTS}" \
            "${DUT_USER}@${DUT_HOST}:${DUT_LOG_DIR}/${sub}/" \
            "${LOCAL_LOG_DIR}/${sub}/" \
            || echo "pull_logs: rsync of ${sub} failed (DUT may be down/rebooting)" >&2
    elif [ "${PULL_MODE}" = "full" ]; then
        # /* so scp copies the dir CONTENTS into dst (not the dir into dst, which
        # would double-nest as ${sub}/${sub}/). The glob is expanded remotely.
        scp -q -r ${SSH_OPTS} \
            "${DUT_USER}@${DUT_HOST}:${DUT_LOG_DIR}/${sub}/*" \
            "${LOCAL_LOG_DIR}/${sub}/" 2>/dev/null \
            || echo "pull_logs: scp of ${sub} failed (DUT may be down/rebooting)" >&2
    else
        scp -q ${SSH_OPTS} \
            "${DUT_USER}@${DUT_HOST}:${DUT_LOG_DIR}/${sub}/*.jsonl" \
            "${LOCAL_LOG_DIR}/${sub}/" 2>/dev/null \
            || echo "pull_logs: scp of ${sub} jsonl failed (DUT may be down/rebooting)" >&2
    fi
}

# Pull one remote file, best-effort (missing file must not fail the run).
pull_remote_file() {   # $1 = remote path, $2 = local dest
    if [ -n "${HAVE_RSYNC}" ]; then
        rsync -az -e "ssh ${SSH_OPTS}" "${DUT_USER}@${DUT_HOST}:$1" "$2" \
            || echo "pull_logs: $(basename "$1") not pulled (missing or DUT unreachable)" >&2
    else
        scp -q ${SSH_OPTS} "${DUT_USER}@${DUT_HOST}:$1" "$2" 2>/dev/null \
            || echo "pull_logs: $(basename "$1") not pulled (missing or DUT unreachable)" >&2
    fi
}

for sub in memory compute boot_state; do pull_channel "${sub}"; done

if [ "${PULL_MODE}" != "full" ]; then
    echo "pull_logs: live mode -- JSONL only (see_dumps/pstore/sidecars deferred to PULL_MODE=full)"
    echo "pull_logs: completed at $(date -Iseconds) [backend: ${HAVE_RSYNC:+rsync}${HAVE_RSYNC:-scp}]"
    exit 0
fi

# Post-processing sidecars: the compute triage tool (see_dump_triage.py) hashes
# each dumped checkpoint against the board's PER-BOARD golden table, which lives
# in the repo tree on the DUT (data/ is git-ignored) -- not under /var/log. Pull
# it (and the active particles config, for epoch/checksum parameters) next to the
# compute logs so a pulled tree is self-sufficient for offline analysis.
pull_remote_file "${DUT_REPO_DIR}/jetson/compute/cuda_particles/data/golden_hashes.txt" \
                 "${LOCAL_LOG_DIR}/compute/golden_hashes.txt"
pull_remote_file "${DUT_REPO_DIR}/jetson/compute/cuda_particles/config/particles.json" \
                 "${LOCAL_LOG_DIR}/compute/particles.json"

# pstore records are small one-shot panic/console dumps; copy them out too.
# Best-effort: absence of records or an unreachable DUT must not fail the run.
mkdir -p "${LOCAL_LOG_DIR}/pstore"
if [ -n "${HAVE_RSYNC}" ]; then
    rsync -az -e "ssh ${SSH_OPTS}" \
        "${DUT_USER}@${DUT_HOST}:${PSTORE_DIR}/" \
        "${LOCAL_LOG_DIR}/pstore/" 2>/dev/null \
        || echo "pull_logs: no pstore records pulled (empty, perms, or DUT unreachable)" >&2
else
    scp -q -r ${SSH_OPTS} \
        "${DUT_USER}@${DUT_HOST}:${PSTORE_DIR}/*" \
        "${LOCAL_LOG_DIR}/pstore/" 2>/dev/null \
        || echo "pull_logs: no pstore records pulled (empty, perms, or DUT unreachable)" >&2
fi

echo "pull_logs: completed at $(date -Iseconds) [backend: ${HAVE_RSYNC:+rsync}${HAVE_RSYNC:-scp}]"
