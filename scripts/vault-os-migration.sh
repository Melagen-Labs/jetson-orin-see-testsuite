#!/usr/bin/env bash
# vault-os-migration.sh -- move the flash host's OS from the PNY stick onto
# new partitions carved from the 256GB SanDisk vault drive (one-time surgery).
#
# Layout after (MBR kept -- p1 starts at sector 32, too early for GPT):
#   sda1  ext4  ~122GiB  JETSON_BACKUP vault (shrunk in place, data preserved)
#   sda2  vfat   512MiB  ESP (removable path: EFI/BOOT/BOOTX64.EFI shim)
#   sda3  ext4  ~110GiB  new OS root (copied from the running PNY system)
#
# Run DETACHED as root:  setsid nohup bash vault-os-migration.sh &
# Log: /home/ubuntu/migration.log  Progress marker: /home/ubuntu/migration.stage
set -euo pipefail
LOG=/home/ubuntu/migration.log
STAGE=/home/ubuntu/migration.stage
exec >>"$LOG" 2>&1

mark() { echo "$1" > "$STAGE"; echo "=== [$(date +%H:%M:%S)] $1"; }

DISK=/dev/sda
P1=/dev/sda1; P2=/dev/sda2; P3=/dev/sda3
NEWROOT=/mnt/newroot

# Sector math (512B sectors, disk total 488878080):
#   p1: start 32,        size 255852544  (122 GiB)
#   p2: start 255854592, size 1048576    (512 MiB, 1MiB-aligned)
#   p3: start 256903168, size 231974912  (~110.6 GiB)

mark "0-preflight"
[ "$(cat /sys/class/power_supply/AC*/online 2>/dev/null || echo 1)" = "1" ] \
    || { echo "ABORT: on battery"; exit 1; }
systemctl stop flash-monitor 2>/dev/null || true
umount /media/ubuntu/JETSON_BACKUP 2>/dev/null || true
mountpoint -q /media/ubuntu/JETSON_BACKUP && { echo "ABORT: vault still mounted"; exit 1; }

mark "1-fsck"
e2fsck -f -y "$P1"

mark "2-shrink-fs"
resize2fs "$P1" 120G

mark "3-repartition"
sfdisk --no-reread "$DISK" <<'EOF'
label: dos
unit: sectors
/dev/sda1 : start=32,        size=255852544, type=c
/dev/sda2 : start=255854592, size=1048576,   type=ef
/dev/sda3 : start=256903168, size=231974912, type=83
EOF
partprobe "$DISK"; sleep 3
lsblk "$DISK"

mark "4-fsck-after-shrink"
e2fsck -f -y "$P1"    # confirm the shrunk fs is healthy inside the new bounds

mark "5-mkfs"
mkfs.vfat -F32 -n FLASHESP "$P2"
mkfs.ext4 -F -L flashroot "$P3"

mark "6-copy-os"
mkdir -p "$NEWROOT"
mount "$P3" "$NEWROOT"
rsync -aAXH --info=stats1 \
    --exclude=/proc/* --exclude=/sys/* --exclude=/dev/* --exclude=/run/* \
    --exclude=/tmp/* --exclude=/mnt/* --exclude=/media/* --exclude=/lost+found \
    --exclude=/home/ubuntu/migration.log --exclude=/home/ubuntu/staging.log \
    / "$NEWROOT"/

mark "7-boot-config"
U_ROOT=$(blkid -s UUID -o value "$P3")
U_EFI=$(blkid -s UUID -o value "$P2")
cat > "$NEWROOT/etc/fstab" <<EOF
UUID=${U_ROOT}  /          ext4  defaults,noatime  0 1
UUID=${U_EFI}  /boot/efi  vfat  umask=0077,nofail  0 1
LABEL=JETSON_BACKUP /media/ubuntu/JETSON_BACKUP ext4 defaults,nofail 0 2
EOF
mkdir -p "$NEWROOT/boot/efi"
mount "$P2" "$NEWROOT/boot/efi"
for d in dev dev/pts proc sys run; do mount --bind /$d "$NEWROOT/$d"; done
chroot "$NEWROOT" grub-install --target=x86_64-efi --efi-directory=/boot/efi \
    --no-nvram --removable --recheck
chroot "$NEWROOT" update-grub
grep -m1 -o 'root=UUID=[a-f0-9-]*' "$NEWROOT/boot/grub/grub.cfg" || true

mark "8-cleanup"
for d in run sys proc dev/pts dev; do umount "$NEWROOT/$d" 2>/dev/null || true; done
umount "$NEWROOT/boot/efi"; umount "$NEWROOT"
mount "$P1" /media/ubuntu/JETSON_BACKUP 2>/dev/null || true
sync
mark "9-DONE-ready-for-reboot"
