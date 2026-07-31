#!/usr/bin/env bash
# fleet.sh -- update the Jetson DUT fleet from git and restart the channels.
#
# Model: git is the single source of truth; each DUT holds a CLONE of this repo
# at $REPO_DIR and updates via `git pull` (no more per-board scp). See
# docs/DEPLOYMENT.md. For the frozen campaign you instead flash one hashed image
# to every board -- this script is the DEVELOPMENT-phase updater.
#
# Usage:
#   scripts/fleet.sh pull        # git pull on every board
#   scripts/fleet.sh build       # pull + rebuild cuda_particles (CUDA) on every board
#   scripts/fleet.sh restart     # restart the systemd services on every board
#   scripts/fleet.sh status      # show git HEAD + service state on every board
#   HOSTS="orin-nano-01 orin-nano-02" scripts/fleet.sh pull   # override the list
#
# Assumes passwordless SSH as $SSH_USER to each host and a clone at $REPO_DIR.
set -euo pipefail

SSH_USER="${SSH_USER:-melagen}"
REPO_DIR="${REPO_DIR:-\$HOME/see-testsuite}"   # expanded on the remote
HOSTS="${HOSTS:-orin-nano-01 orin-nano-02 orin-nano-03 orin-nano-04 orin-nano-05 orin-nano-06 orin-nano-07}"
CMD="${1:-status}"

run() { # run <remote-shell-snippet> on every host, labelled
  for h in $HOSTS; do
    echo "==== $h ===="
    # shellcheck disable=SC2029
    ssh "${SSH_USER}@${h}" "$1" || echo "  [!] $h failed"
  done
}

case "$CMD" in
  pull)
    run "cd $REPO_DIR && git pull --ff-only"
    ;;
  build)
    run "cd $REPO_DIR && git pull --ff-only && export PATH=/usr/local/cuda/bin:\$PATH && \
         cmake --build jetson/compute/cuda_particles/build 2>/dev/null || \
         cmake -S jetson/compute/cuda_particles -B jetson/compute/cuda_particles/build -DCMAKE_BUILD_TYPE=Release && \
         cmake --build jetson/compute/cuda_particles/build -j"
    ;;
  restart)
    run "sudo systemctl restart cuda_particles mem_check 2>&1 || true"
    ;;
  status)
    run "cd $REPO_DIR && git rev-parse --short HEAD && \
         systemctl is-active cuda_particles mem_check 2>/dev/null || true"
    ;;
  *)
    echo "usage: $0 {pull|build|restart|status}   (HOSTS=... to override board list)" >&2
    exit 2
    ;;
esac
