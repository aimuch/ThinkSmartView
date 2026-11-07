#!/bin/sh
# Install thinksmart-update on the device (as root, from a copy of this directory):
#   sudo sh install.sh [CLIENT_DIR]
# Puts the updater and its units in place, then takes CLIENT_DIR (default
# /home/ubuntu/thinksmart-client, with its .venv) as the installed client version unless one is
# already installed. The client service then runs from /opt/thinksmart/client/current.
set -e
cd "$(dirname "$0")"
install -m 755 thinksmart-update /usr/local/sbin/thinksmart-update
# releases are no longer signed (2026-11-01): drop the public keys earlier versions installed
rm -rf /usr/local/share/thinksmart-update
[ -e /etc/thinksmart-update.conf ] || install -m 644 thinksmart-update.conf /etc/thinksmart-update.conf
for u in thinksmart-update-check.service thinksmart-update-check.timer thinksmart-update-request.path \
         thinksmart-update-run.service thinksmart-update-boot.service; do
  install -m 644 "$u" /etc/systemd/system/
done
systemctl daemon-reload
[ -L /opt/thinksmart/client/current ] || /usr/local/sbin/thinksmart-update adopt "${1:-/home/ubuntu/thinksmart-client}"
systemctl enable --now thinksmart-update-check.timer thinksmart-update-request.path
systemctl enable thinksmart-update-boot.service
systemctl restart thinksmart-client.service
