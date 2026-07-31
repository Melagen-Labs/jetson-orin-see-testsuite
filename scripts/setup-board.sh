#!/usr/bin/env bash
# setup-board.sh -- one-shot bring-up for a single Jetson Orin Nano DUT.
#
# Run ONCE on each new board, interactively (so sudo can prompt for a password):
#   ./scripts/setup-board.sh 03        # brings up this board as orin-nano-03
#
# It gives the board its identity, syncs the code, installs the GPU deps, builds
# the compute channel, generates this board's own golden table, arms the channels,
# and installs the systemd services. Re-running is safe: it just updates,
# reinstalls, rebuilds, and re-arms.
#
# Memory testing is GPU-only (channel 2b, CuPy on GPU DRAM). The CPU/system-RAM
# tester (channel 2a) still exists in the repo but is NOT deployed -- the campaign
# minimizes CPU workload, so no CPU memory service is installed or enabled.

set -euo pipefail   # exit on any error / unset variable / failed pipe stage

# ---- inputs ----------------------------------------------------------------
# $1 is the two-digit board number; the ":?" prints the usage and aborts if missing.
NN="${1:?usage: setup-board.sh <NN>   (two-digit board number, e.g. 03)}"
HOSTNAME_NEW="orin-nano-${NN}"                       # human-readable board name
REPO_URL="https://github.com/Reece122/jetson-orin-see-testsuite.git"
REPO="${HOME}/see-testsuite"                         # clone lives here on every board
CUDA_BIN="/usr/local/cuda/bin"                       # JetPack CUDA toolchain (nvcc)
COMPUTE="${REPO}/jetson/compute/cuda_particles"      # GPU compute channel dir
MEMORY="${REPO}/jetson/memory"                        # memory channel dir (GPU DRAM tester, 2b)
CONTROL="${REPO}/jetson/control"                      # arbiter test-control receiver dir

# ---- 1. identity -----------------------------------------------------------
# A clone is a byte-for-byte copy of the master, so it inherits three things that
# must be unique per board: the hostname, the SSH host keys, and the machine-id.
# None of them need operator input beyond the board number, so we regenerate all
# three here.
#
# 1a. Hostname. Every log line's jetson_id is derived from the hostname
# (jetson_id:"auto"), so naming the board is what makes its logs identifiable.
echo "[1/7] set hostname -> ${HOSTNAME_NEW}"
sudo hostnamectl set-hostname "${HOSTNAME_NEW}"

# 1b. SSH host keys. A clone answers SSH with the master's host keys, so every
# board looks like the same host and connecting to a second board on the same
# direct-link IP trips a "host key changed" warning. Regenerate a unique set.
# (Re-running setup-board.sh rotates these again -- harmless, just re-accept the
# key on next connect. Does NOT affect the science logs.)
echo "      regenerate SSH host keys"
sudo rm -f /etc/ssh/ssh_host_*
sudo ssh-keygen -A                                   # fresh per-board host keys
sudo systemctl restart ssh                           # serve the new keys now

# 1c. machine-id. Also cloned from the master; regenerate so each board is a
# distinct systemd/D-Bus machine. (boot_id, which the logs actually use, is
# already random per boot -- this is hygiene, not a data-integrity fix.)
echo "      regenerate machine-id"
sudo rm -f /etc/machine-id /var/lib/dbus/machine-id
sudo systemd-machine-id-setup

# ---- 2. code ---------------------------------------------------------------
# Clone the repo on first run; on later runs just fast-forward to the latest commit.
echo "[2/7] sync repo at ${REPO}"
if [ -d "${REPO}/.git" ]; then
  git -C "${REPO}" pull --ff-only
else
  git clone "${REPO_URL}" "${REPO}"
fi

# ---- 3. GPU deps -----------------------------------------------------------
# The GPU memory tester (2b) needs CuPy. JetPack strips pip out of the base
# Python, so install python3-pip first, then CuPy into the user site (~/.local,
# no sudo). Pins matter -- see docs/DEPENDENCIES.md: CuPy 14 pulls numpy 2.x and
# breaks the system SciPy, so stay on CuPy 13 + numpy <1.25.
echo "[3/7] install GPU deps (python3-pip + CuPy)"
sudo apt-get install -y python3-pip
python3 -m pip install --user "cupy-cuda12x==13.*" "numpy>=1.22,<1.25"

# ---- 4. build --------------------------------------------------------------
# The compute channel is CUDA C++ and must be compiled on the board.
echo "[4/7] build cuda_particles"
export PATH="${CUDA_BIN}:${PATH}"                    # put nvcc on PATH for cmake
cmake -S "${COMPUTE}" -B "${COMPUTE}/build" -DCMAKE_BUILD_TYPE=Release   # configure
cmake --build "${COMPUTE}/build" -j                  # compile (all cores)

# ---- 5. golden table -------------------------------------------------------
# The golden hashes are device+build specific, so each board generates its OWN
# reference here, once, with no beam present. (This file is git-ignored.)
echo "[5/7] generate this board's golden table"
( cd "${COMPUTE}" && ./build/cuda_particles --config config/particles.json --generate-golden )

# ---- 6. arm ----------------------------------------------------------------
# Both deployed services are gated on a persistent ARMED flag so they only run
# during a campaign. Creating it now means every future boot (incl. a crash
# reboot) starts them; delete these files to stand the board down. The GPU memory
# unit shares the memory-dir ARMED flag. (See docs/SERVICES.md.)
echo "[6/7] arm compute + gpu-memory channels"
touch "${COMPUTE}/ARMED" "${MEMORY}/ARMED"

# ---- 7. services -----------------------------------------------------------
# Install the unit files, reload systemd, and enable+start the DEPLOYED channels:
# compute (cuda_particles), GPU memory (mem_check_gpu), and the arbiter
# test-control receiver (test_control, runs as root). The CPU memory unit is
# intentionally not installed. The channels are gated by their ARMED flag (armed
# in step 6); the control receiver is not gated -- it must always listen so it can
# act on the arbiter's start/stop command (docs/CONTROL_INTERFACE.md).
echo "[7/7] install + start services"
sudo cp "${COMPUTE}/cuda_particles.service" /etc/systemd/system/cuda_particles.service
sudo cp "${MEMORY}/mem_check_gpu.service"   /etc/systemd/system/mem_check_gpu.service
sudo cp "${CONTROL}/test_control.service"   /etc/systemd/system/test_control.service
sudo systemctl daemon-reload                         # re-read unit files
sudo systemctl enable --now cuda_particles.service mem_check_gpu.service test_control.service

echo "done -- status:"
systemctl status cuda_particles.service mem_check_gpu.service test_control.service --no-pager || true
echo "NOTE: log out and back in for the new hostname to appear in your shell prompt."
