#!/usr/bin/env bash
# Point a flashed ThinkSmart View at the hub on 10.10.10.224 instead of its bundled one.
# Run on this computer:  DEVICE=<device IP> remote-hub/setup-device.sh
#   DEVICE_KEY   ssh key for ubuntu@DEVICE (default ~/.ssh/thinksmart_ed25519)
#   HUB_KEY      ssh key for andy@HUB      (default ~/.ssh/id_ed25519_btrouter)
set -euo pipefail

: "${DEVICE:?set DEVICE=<device IP>}"
HUB="${HUB:-10.10.10.224}"
HUB_USER="${HUB_USER:-andy}"
DEVICE_KEY="${DEVICE_KEY:-$HOME/.ssh/thinksmart_ed25519}"
HUB_KEY="${HUB_KEY:-$HOME/.ssh/id_ed25519_btrouter}"
HERE="$(cd "$(dirname "$0")" && pwd)"

dev() { ssh -i "$DEVICE_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts_thinksmart" "ubuntu@$DEVICE" "$@"; }
hub() { ssh -i "$HUB_KEY" -o BatchMode=yes "$HUB_USER@$HUB" "$@"; }

echo "==> tunnel key on the device"
dev '[ -f ~/.ssh/hub_tunnel_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -C thinksmart-hub-tunnel -f ~/.ssh/hub_tunnel_ed25519'
PUB="$(dev 'cat ~/.ssh/hub_tunnel_ed25519.pub')"

echo "==> authorize it on the hub (port forwarding to 127.0.0.1:16688/16689/16690 only, no shell)"
LINE="restrict,port-forwarding,permitopen=\"127.0.0.1:16688\",permitopen=\"127.0.0.1:16689\",permitopen=\"127.0.0.1:16690\",command=\"/bin/false\" $PUB"
hub "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -vF '$PUB' ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.new || true; echo '$LINE' >> ~/.ssh/authorized_keys.new; mv ~/.ssh/authorized_keys.new ~/.ssh/authorized_keys"

echo "==> pin the hub's host key on the device"
HOSTKEYS="$(ssh-keyscan -t ed25519 "$HUB" 2>/dev/null)"
[ -n "$HOSTKEYS" ] || { echo "ssh-keyscan $HUB failed" >&2; exit 1; }
printf '%s\n' "$HOSTKEYS" | dev 'umask 077; cat > ~/.ssh/known_hosts_hub'

echo "==> stop the bundled hub, install the tunnel"
dev 'systemctl --user disable --now xiaozhi-hub 2>/dev/null || true'
dev 'sudo tee /etc/systemd/system/xiaozhi-hub-tunnel.service >/dev/null' < "$HERE/xiaozhi-hub-tunnel.service"
dev 'sudo systemctl daemon-reload && sudo systemctl enable xiaozhi-hub-tunnel && sudo systemctl restart xiaozhi-hub-tunnel && sleep 3 && systemctl is-active xiaozhi-hub-tunnel'
dev 'curl -s -o /dev/null -w "kiosk/llm via tunnel: HTTP %{http_code}\n" http://127.0.0.1:16688/kiosk/llm'
echo "==> updates from our release server (hub 127.0.0.1:16690 through the tunnel), not upstream's GitHub"
dev 'sudo sed -i "s|^SOURCES=.*|SOURCES=\"http://127.0.0.1:16690/\"|" /etc/thinksmart-update.conf && grep ^SOURCES /etc/thinksmart-update.conf'
dev 'curl -s -o /dev/null -w "releases via tunnel: HTTP %{http_code}\n" http://127.0.0.1:16690/latest/download/latest.json'
dev 'sudo systemctl restart thinksmart-client'
echo "done"
