#!/bin/bash

set -e

echo "This script will start the RAID1 bootstrap script and install Arch Linux."
echo
echo "Press enter to start..."
read

echo -n '[?] Type the root password for gw: ' && read -s root_password
echo

echo '[+] Detected disks:'
shopt -s nullglob  # glob pattern is removed if it has no matches
for disk in /dev/vd? /dev/sd?; do
    fdisk -l ${disk} | grep Disk | grep bytes
done
while ! [ -b "$disk_0" ]; do
    echo -n '[?] First disk (boot drive): ' && read disk_0
done
while ! [ -b "$disk_1" ]; do
    echo -n '[?] Second disk (RAID slave): ' && read disk_1
done

./bootstrap_fs_raid1.sh gw "$disk_0" "$disk_1"
./bootstrap_arch_linux.sh /mnt gw.prolo <(echo "$root_password")
echo "[+] Copying resolv.conf for gw.prolo"
cp -v ../etc/resolv.conf.gw /mnt/etc/resolv.conf
./bootstrap_fs_raid1_post.sh gw

echo "[+] You can reboot now!"
