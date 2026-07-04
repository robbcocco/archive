#!/bin/sh
# NOTES, not a runnable script — commands from the proxmox host setup,
# kept for reference. wipefs/zpool lines are destructive; run selectively.

zpool create -o ashift=12 media \
  /dev/disk/by-id/ata-WDC_WD40EFRX-68N32N0_WD-WCC7K0CKC489 \
  /dev/disk/by-id/ata-WDC_WD40EFRX-68N32N0_WD-WCC7K1VRAAK9

pct set 150 -mp1 /media/data,mp=/mnt/media
pct set 150 -mp0 /media/usb,mp=/mnt/usb
chown -R 101000:101000 /media/data

curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --accept-dns=true --ssh

docker run --detach --name watchtower --volume /var/run/docker.sock:/var/run/docker.sock containrrr/watchtower


wipefs -a /dev/disk/by-id/ata-TP_TP1000G_357BPEHBT
wipefs -a /dev/disk/by-id/ata-WDC_WD10SPZX-22Z10T1_WD-WXG1A88D0TS8

scp -r root@192.168.1.100:/media/data/cloud/files/ /data/cloud/ncAdmin/files/
