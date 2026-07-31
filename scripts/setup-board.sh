#!/usr/bin/env bash
# setup-board.sh -- one-shot bring-up for a single Jetson Orin Nano DUT.
#
# Run ONCE on each new board, interactively (so sudo can prompt for a password):
#   ./scripts/setup-board.sh 03        # brings up this board as orin-nano-03
#
# It gives the board its identity, syncs the code, builds the compute channel,
# generates this board's own golden table, arms both channels, and installs the
# systemd services. Re-running is safe: it just updates, rebuilds, and re-arms.

set -euo pipefail   # exit on any error / unset variable / failed pipe stage

# ---- inputs ----------------------------------------------------------------
# $1 is the two-digit board number; the ":?" prints the usage and aborts if missing.
NN="${1:?usage: setup-board.sh <NN>   (two-digit board number, e.g. 03)}"
HOSTNAME_NEW="orin-nano-${NN}"                       # human-readable board name
REPO_URL="https://github.com/Reece122/jetson-orin-see-testsuite.git"
REPO="${HOME}/see-testsuite"                         # clone lives here on every board
CUDA_BIN="/usr/local/cuda/bin"                       # JetPack CUDA toolchain (nvcc)
COMPUTE="${REPO}/jetson/compute/cuda_particles"      # GPU compute channel dir
MEMORY="${REPO}/jetson/memory"                       # CPU/RAM memory channel dir

# ---- 1. identity -----------------------------------------------------------
# Every log line's jetson_id is derived from the hostname (jetson_id:"auto"),
# so naming the board is what makes its logs identifiable in the fleet.
echo "[1/6] set hostname -> ${HOSTNAME_NEW}"
sudo hostnamectl set-hostname "${HOSTNAME_NEW}"

# ---- 2. code ---------------------------------------------------------------
# Clone the repo on first run; on later runs just fast-forward to the latest commit.
echo "[2/6] sync repo at ${REPO}"
if [ -d "${REPO}/.git" ]; then
  git -C "${REPO}" pull --ff-only
else
  git clone "${REPO_URL}" "${REPO}"
fi

# ---- 3. build --------------------------------------------------------------
# The compute channel is CUDA C++ and must be compiled on the board.
echo "[3/6] build cuda_particles"
export PATH="${CUDA_BIN}:${PATH}"                    # put nvcc on PATH for cmake
cmake -S "${COMPUTE}" -B "${COMPUTE}/build" -DCMAKE_BUILD_TYPE=Release   # configure
cmake --build "${COMPUTE}/build" -j                  # compile (all cores)

# ---- 4. golden table -------------------------------------------------------
# The golden hashes are device+build specific, so each board generates its OWN
# reference here, once, with no beam present. (This file is git-ignored.)
echo "[4/6] generate this board's golden table"
( cd "${COMPUTE}" && ./build/cuda_particles --config config/particles.json --generate-golden )

# ---- 5. arm ----------------------------------------------------------------
# Both services are gated on a persistent ARMED flag so they only run during a
# campaign. Creating it now means every future boot (incl. a crash reboot) starts
# them; delete these files to stand the board down. (See docs/SERVICES.md.)
echo "[5/6] arm both channels"
touch "${COMPUTE}/ARMED" "${MEMORY}/ARMED"

# ---- 6. services -----------------------------------------------------------
# Install the unit files, reload systemd, and enable+start both channels.
echo "[6/6] install + start services"
sudo cp "${COMPUTE}/cuda_particles.service" /etc/systemd/system/cuda_particles.service
sudo cp "${MEMORY}/mem_check.service"       /etc/systemd/system/mem_check.service
sudo systemctl daemon-reload                         # re-read unit files
sudo systemctl enable --now cuda_particles.service mem_check.service   # boot-start + start now

echo "done -- status:"
systemctl status cuda_particles.service mem_check.service --no-pager || true
echo "NOTE: log out and back in for the new hostname to appear in your shell prompt."
