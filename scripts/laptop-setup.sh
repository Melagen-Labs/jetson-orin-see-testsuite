#!/usr/bin/env bash
# laptop-setup.sh -- restore the flash laptop's working state after a reboot.
#
# The flash host is a LIVE Ubuntu 22.04 USB session (32 GB SanDisk stick).
# Unless it was booted with the 'persistent' kernel parameter, the root
# filesystem is a RAM overlay: every package, service, and login installed in
# a session is lost at shutdown -- including SSH, Tailscale, and the NFS server
# the NVIDIA flash tooling needs. This script rebuilds all of it in one command.
#
# Run (from the JETSON_BACKUP drive, as the live 'ubuntu' user):
#     bash /media/ubuntu/JETSON_BACKUP/laptop-setup.sh
# Then for a flashing session specifically:
#     bash /media/ubuntu/JETSON_BACKUP/laptop-setup.sh flash
#
# PERSISTENCE (the real fix -- makes this script one-time instead of per-boot):
# the live stick's 4th partition is already labeled 'writable', which is the
# label casper looks for in 20.04+ (see /usr/share/initramfs-tools/scripts/
# casper-helpers on the stick). The GRUB config sits on the read-only ISO9660
# partition, so the flag must be typed at boot:
#     reboot -> GRUB menu -> press 'e' -> append the word  persistent  to the
#     line starting with 'linux' -> Ctrl-X to boot.
# A session booted that way keeps everything this script installs.

set -euo pipefail

echo "== flash-laptop setup =="

# 0. Persistence check ---------------------------------------------------------
# Three possible hosts: the installed PNY system (normal disk root -- always
# persistent), the live stick booted WITH 'persistent', or the live stick
# without it (everything below is lost at shutdown).
if ! grep -q ' / overlay' /proc/mounts; then
    echo "[ok] installed system (melagen-flash-host) -- persistent by nature"
elif grep -qw persistent /proc/cmdline; then
    echo "[ok] live session booted with 'persistent' -- changes survive reboot"
else
    echo "[warn] NON-PERSISTENT live session: everything below is lost at"
    echo "       shutdown. Either boot the installed PNY stick instead, or"
    echo "       reboot and add 'persistent' at the GRUB 'linux' line"
    echo "       (press 'e', append the word, Ctrl-X)."
fi

# 1. Password for the live 'ubuntu' user (SSH password login needs one) --------
if sudo grep -q '^ubuntu:[^!*:]' /etc/shadow; then
    echo "[1/5] ubuntu password already set"
else
    echo "[1/5] set a password for user 'ubuntu' (used for SSH logins):"
    sudo passwd ubuntu
fi

# 2. Host packages the NVIDIA flash flow + remote access need ------------------
echo "[2/5] install packages (openssh-server, nfs-kernel-server, lbzip2, curl)"
sudo add-apt-repository -y universe >/dev/null 2>&1 || true
sudo apt-get update -qq
sudo apt-get install -y openssh-server nfs-kernel-server lbzip2 curl \
    sshpass abootimg libxml2-utils zstd binutils
sudo systemctl enable --now ssh nfs-kernel-server

# 3. Tailscale (remote access for the team) ------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
    echo "[3/5] install tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "[3/5] tailscale already installed"
fi
if tailscale status >/dev/null 2>&1; then
    echo "      tailscale already up ($(tailscale ip -4 2>/dev/null | head -1))"
else
    echo "      enrolling -- open the URL this prints and approve the machine:"
    sudo tailscale up
fi

# 4. Flash-session prep (only with the 'flash' argument) -----------------------
if [ "${1:-}" = "flash" ]; then
    echo "[4/5] flash prep"
    # Automounting a half-written target mid-restore corrupts it; keep udisks
    # out of the way for the session (FLASH_AND_BRINGUP.md §2 prerequisites).
    sudo systemctl stop udisks2.service 2>/dev/null || true
    echo "      udisks2 stopped (automount off until reboot)"
    echo "      reminders: ONE Jetson in recovery mode at a time;"
    echo "                 confirm with:  lsusb | grep -i nvidia"
    echo "      if the flash aborts with 'another rpcbind is already running':"
    echo "                 sudo systemctl stop rpcbind rpcbind.socket"
else
    echo "[4/5] (skipped flash prep -- rerun with 'flash' before flashing)"
fi

# 5. Status summary ------------------------------------------------------------
echo "[5/5] status"
echo "      ssh:              $(systemctl is-active ssh)"
echo "      nfs-kernel-server: $(systemctl is-active nfs-kernel-server)"
echo "      tailscale IP:     $(tailscale ip -4 2>/dev/null | head -1 || echo 'not up')"
echo "      JETSON_BACKUP:    $(findmnt -no TARGET /dev/sda1 2>/dev/null || echo 'NOT MOUNTED -- plug in / remount the 256GB drive')"
echo "done."
