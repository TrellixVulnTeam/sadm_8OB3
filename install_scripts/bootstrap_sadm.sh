#!/bin/bash

# Run this script if you are installing from an Arch Linux install media.
# It does a standalone clone and setup of sadm from the main git repository. If
# you are developping on SADM, directly use install_scripts/setup_sadm.sh

if mount | grep --quiet archiso; then
  # When running on the Arch Linux install media, increase the overlay work dir
  # size
  echo '[+] Increasing the size of /run/archiso/cowspace'
  mount -o remount,size=2G /run/archiso/cowspace
fi

echo '[+] Installing dependencies'
pacman -S --needed --noconfirm git

echo '[+] Cloning sadm'
cd /root
git clone https://github.com/prologin/sadm

echo '[+] Starting Arch Linux bootstrap script'
./sadm/install_scripts/bootstrap.sh

echo '[+] Copying SADM install'
cp -r sadm /mnt/root/sadm
