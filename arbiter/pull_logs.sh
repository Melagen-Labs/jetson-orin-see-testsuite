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
# ---- Configuration (environment variables, with defaults) -------------------
set -euo pipefail

DUT_HOST="${DUT_HOST:-192.168.1.20}"
DUT_USER="${DUT_USER:-radpull}"
DUT_LOG_DIR="${DUT_LOG_DIR:-/var/log/radtest}"
LOCAL_LOG_DIR="${LOCAL_LOG_DIR:-./arbiter_logs}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/radtest_pull}"
PSTORE_DIR="${PSTORE_DIR:-/sys/fs/pstore}"

SSH_OPTS="-i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new"

mkdir -p "${LOCAL_LOG_DIR}"

# Structured per-channel log directories. -z compresses over the (possibly slow)
# beam-line ethernet; --append-verify is safe for append-only growing log files.
for sub in memory compute boot_state; do
    mkdir -p "${LOCAL_LOG_DIR}/${sub}"
    rsync -az --append-verify \
        -e "ssh ${SSH_OPTS}" \
        "${DUT_USER}@${DUT_HOST}:${DUT_LOG_DIR}/${sub}/" \
        "${LOCAL_LOG_DIR}/${sub}/" \
        || echo "pull_logs: rsync of ${sub} failed (DUT may be down/rebooting)" >&2
done

# pstore records are small one-shot panic/console dumps; copy them out too.
# Best-effort: absence of records or an unreachable DUT must not fail the run.
mkdir -p "${LOCAL_LOG_DIR}/pstore"
rsync -az \
    -e "ssh ${SSH_OPTS}" \
    "${DUT_USER}@${DUT_HOST}:${PSTORE_DIR}/" \
    "${LOCAL_LOG_DIR}/pstore/" 2>/dev/null \
    || echo "pull_logs: no pstore records pulled (empty, perms, or DUT unreachable)" >&2

echo "pull_logs: completed at $(date -Iseconds)"
