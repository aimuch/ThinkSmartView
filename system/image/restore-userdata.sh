#!/bin/bash
# Put the owner's data back on a freshly flashed device (the release image ships none of it):
#   TS_HOST=<device IP> image/restore-userdata.sh USERDATA_TAR [--host-keys]
# USERDATA_TAR is the uncompressed tarball taken before flashing, paths relative to / :
#   home/ubuntu/xiaozhi-hub-server/main/xiaozhi-server/data  (hub keys, Grok login, chat.db, admin password)
#   home/ubuntu/.claude home/ubuntu/.claude.json            (Claude Code login, if any)
#   home/ubuntu/.config/thinksmart-client                    (device id, sleep hours, brightness, screen-off)
#   home/ubuntu/.local/share/thinksmart-kiosk                (kiosk browser storage)
#   home/ubuntu/.ssh                                         (authorized_keys)
#   etc/NetworkManager/system-connections etc/netplan        (Wi-Fi profiles)
#   etc/thinksmart-kiosk.conf etc/hostname                   (kiosk settings, host name)
#   etc/ssh                                                  (host keys: only with --host-keys)
# Whatever the tarball lacks is left as the image has it. Nothing on the device changes unless the
# whole tarball unpacks on the device first. Needs SSH to the device already working (ts.sh: TS_HOST,
# the key in ~/.ssh/thinksmart_ed25519, host keys in ~/.ssh/known_hosts_thinksmart; on a fresh image
# first run, password thinksmart:
#   ssh-copy-id -i ~/.ssh/thinksmart_ed25519 -o UserKnownHostsFile=~/.ssh/known_hosts_thinksmart ubuntu@<ip>
# With --host-keys the device gets its old key back: ssh-keygen -R <ip> -f ~/.ssh/known_hosts_thinksmart).
set -euo pipefail
TAR=${1:?userdata tarball}; HOSTKEYS=${2:-}
: "${TS_HOST:?set TS_HOST=<device IP>}"
TS="$(cd "$(dirname "$0")/.." && pwd)/ts.sh"
[ "$(head -c 262 "$TAR" | tail -c 5)" = ustar ] || { echo "$TAR is not an uncompressed tar (decompress it first)"; exit 1; }

HUB_DATA=home/ubuntu/xiaozhi-hub-server/main/xiaozhi-server/data
KIOSK_STORE=home/ubuntu/.local/share/thinksmart-kiosk
members=($HUB_DATA home/ubuntu/.claude home/ubuntu/.claude.json home/ubuntu/.config/thinksmart-client
         $KIOSK_STORE home/ubuntu/.ssh etc/NetworkManager/system-connections etc/netplan
         etc/thinksmart-kiosk.conf etc/hostname)
[ "$HOSTKEYS" = --host-keys ] && members+=(etc/ssh/ssh_host_ecdsa_key etc/ssh/ssh_host_ecdsa_key.pub
    etc/ssh/ssh_host_ed25519_key etc/ssh/ssh_host_ed25519_key.pub etc/ssh/ssh_host_rsa_key etc/ssh/ssh_host_rsa_key.pub)
present=()
for m in "${members[@]}"; do tar tf "$TAR" "$m" >/dev/null 2>&1 && present+=("$m"); done
[ ${#present[@]} -gt 0 ] || { echo "nothing to restore in $TAR"; exit 1; }
echo "restoring: ${present[*]}"

# The tarball holds keys: only ubuntu can read it on the device (0600), and it goes away in any case
REMOTE_TAR=/tmp/restore-userdata.tar
"$TS" "umask 077; cat > $REMOTE_TAR" < "$TAR"
trap '"$TS" "rm -f $REMOTE_TAR" || true' EXIT

"$TS" "sudo bash -s -- $REMOTE_TAR ${present[*]}" <<'EOF'
set -euo pipefail
tarball=$1; shift
stage=$(mktemp -d /root/restore.XXXXXX)
# removed here too: with --host-keys the device's key changes and the trap above can't log in again
trap 'rm -rf "$stage" "$tarball"' EXIT
# Unpack everything first; the device stays as it is if this fails
tar xpf "$tarball" --numeric-owner -C "$stage" "$@"
for m in "$@"; do [ -e "$stage/$m" ] || { echo "missing after unpack: $m"; exit 1; }; done
runuser -u ubuntu -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user stop xiaozhi-hub || true
systemctl stop thinksmart-client thinksmart-kiosk || true
restart() {
    systemctl start thinksmart-client thinksmart-kiosk
    runuser -u ubuntu -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start xiaozhi-hub
}
# copies first: a service that fails to start ends the trap (set -e), the user data must not stay
trap 'rm -rf "$stage" "$tarball"; restart' EXIT
# The hub's data/ and the kiosk storage are replaced as a whole (the image's fresh data/ has no
# keys); everything else is merged over what the image has
for m in "$@"; do
    case "$m" in
        home/ubuntu/xiaozhi-hub-server/main/xiaozhi-server/data|home/ubuntu/.local/share/thinksmart-kiosk)
            rm -rf "/$m" ;;
    esac
done
tar cf - -C "$stage" "$@" | tar xpf - --numeric-owner -C /
case " $* " in *" etc/netplan "*) netplan generate || echo "netplan generate failed (Wi-Fi profiles may need a look)";; esac
case " $* " in *" etc/hostname "*) hostname "$(cat /etc/hostname)";; esac
case " $* " in *" etc/ssh/"*) systemctl restart ssh || true;; esac
echo "restored on the device"
EOF
echo "done; the hub needs about a minute to load its speech model"
