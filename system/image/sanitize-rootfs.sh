#!/bin/sh
# Strip everything personal or machine-specific from an extracted root filesystem before it goes
# into a release image (checklist: 开源发布规划与版权审查 3.1). Run as root in the image-tools
# container, on a copy, never on a running device:
#   sanitize-rootfs.sh ROOT HUB_TEMPLATE
# HUB_TEMPLATE is xiaozhi-hub-server's scripts/config.template.yaml of the shipped commit; the hub's
# data/ starts over from it, the way its install script does it.
# What stays on purpose (same as upstream): user ubuntu with password thinksmart, passwordless sudo,
# SSH with password login. The README tells people to change the password.
set -eu
R=${1:?root}; TEMPLATE=${2:?hub config template}
[ -d "$R/etc" ] && [ -d "$R/home/ubuntu" ] || { echo "$R does not look like the root filesystem"; exit 1; }
H=$R/home/ubuntu
HUB=$H/xiaozhi-hub-server
SRV=$HUB/main/xiaozhi-server

say() { echo "sanitize: $*"; }

# Leftovers the device setup didn't remove: the restored partition originals, temp files
say "originals and temp files"
# the first chat page and py-xiaozhi left these in the kiosk's folder (vendor logos, an Edge TTS sample)
rm -rf "$R"/usr/local/share/thinksmart-kiosk/chat.html "$R"/usr/local/share/thinksmart-kiosk/logos \
    "$R"/usr/local/share/thinksmart-kiosk/wake.wav
[ -L "$R/etc/systemd/system/ModemManager.service" ] && rm -f "$R/etc/systemd/system/ModemManager"
find "$R/lib/modules" -name '*.orig' -delete
rm -rf "$R"/tmp/* "$R"/tmp/.[!.]* "$R"/var/tmp/* 2>/dev/null || true

# tar on a Mac adds AppleDouble files (._name, magic 00 05 16 07) unless COPYFILE_DISABLE=1, and
# Finder leaves .DS_Store; deploys from the Mac brought them in. A ._ file without the magic stays.
say "macOS leftovers: ._* AppleDouble files, .DS_Store, __MACOSX"
find "$R" -xdev \( -name .DS_Store -type f -o -name __MACOSX -type d -prune \) -exec rm -rf {} +
find "$R" -xdev -name '._*' -type f | while IFS= read -r f; do
    [ "$(head -c 4 "$f" | od -An -tx1 | tr -d ' \n')" = 00051607 ] && rm -f "$f"
done || true

say "networks: Wi-Fi profiles, leases, Bluetooth pairings"
rm -f "$R"/etc/netplan/90-NM-*.yaml
rm -rf "$R"/etc/NetworkManager/system-connections/*
rm -f "$R"/var/lib/NetworkManager/*.lease "$R"/var/lib/NetworkManager/secret_key \
    "$R"/var/lib/NetworkManager/timestamps "$R"/var/lib/NetworkManager/seen-bssids
rm -rf "$R"/var/lib/dhcpcd/* "$R"/var/lib/bluetooth/*

say "SSH: authorized keys and host keys (regenerated on first boot)"
rm -rf "$H/.ssh" "$R/root/.ssh"
install -d -m 700 -o 1000 -g 1000 "$H/.ssh"
rm -f "$R"/etc/ssh/ssh_host_*

say "machine identity: machine-id, random seed"
: > "$R/etc/machine-id"
rm -f "$R/var/lib/dbus/machine-id"
ln -s /etc/machine-id "$R/var/lib/dbus/machine-id"
rm -f "$R/var/lib/systemd/random-seed" "$R/var/lib/systemd/credential.secret"

say "logs, crash dumps, apt caches"
find "$R/var/log" -type f -delete
rm -rf "$R"/var/log/journal/* "$R"/var/crash/* "$R"/var/lib/systemd/coredump/*
rm -rf "$R"/var/cache/apt/*.bin "$R"/var/cache/apt/archives/*.deb "$R"/var/cache/apt/archives/partial/*
rm -rf "$R"/var/lib/apt/lists/* "$R"/var/cache/debconf/*-old "$R"/var/backups/*

say "home: only what the voice terminal uses"
for f in "$H"/* "$H"/.[!.]*; do
    case "${f##*/}" in
        .bashrc|.profile|.bash_logout|.config|.local|.ssh|xiaozhi-hub-server|xiaozhi-workspace) ;;
        Desktop|Documents|Downloads|Music|Pictures|Public|Templates|Videos) ;;
        *) rm -rf "$f" ;;
    esac
done
for f in "$H"/.config/* "$H"/.config/.[!.]*; do
    case "${f##*/}" in
        pipewire|wireplumber|systemd|user-dirs.dirs|user-dirs.locale) ;;
        *) rm -rf "$f" ;;
    esac
done
for f in "$H"/.local/share/* "$H"/.local/state/*; do
    case "${f##*/}" in
        uv) ;;          # the Python the hub's .venv runs on
        *) rm -rf "$f" ;;
    esac
done
for d in Desktop Documents Downloads Music Pictures Public Templates Videos; do
    [ -d "$H/$d" ] && find "$H/$d" -mindepth 1 -delete
done
find "$H/xiaozhi-workspace" -mindepth 1 -delete
# restore.*: what restore-userdata.sh unpacks the owner's data into, left if it was interrupted
rm -rf "$R"/root/restore.* "$R"/root/.bash_history "$R"/root/.cache "$R"/root/.lesshst "$R"/root/.wget-hsts \
    "$R"/root/.python_history "$R"/root/.viminfo "$R"/root/.local "$R"/root/.gitconfig

say "hub: fresh data/ from the template, no logs, git on main"
rm -rf "$SRV/data" "$SRV/tmp"
install -d -o 1000 -g 1000 "$SRV/data" "$SRV/tmp"
# Same as the hub's install script on a machine without Claude Code: Claude API, key set in the admin page
sed 's/^\(  LLM: \)ClaudeCLI.*/\1ClaudeAPI          # 在管理后台填写 API Key/' "$TEMPLATE" > "$SRV/data/.config.yaml"
: > "$SRV/data/.install-complete"
chown 1000:1000 "$SRV/data/.config.yaml" "$SRV/data/.install-complete"
chmod 600 "$SRV/data/.config.yaml"
rm -rf "$SRV/config/assets/wakeup_words"
find "$HUB" -name __pycache__ -type d -prune -exec rm -rf {} +

say "backup copies: *.bak*, *.orig, *.old, *.save, *~, dpkg/ucf leftovers"
# hand-made copies can hold keys (a .config.yaml.bak with the API key sat in the hub's data/);
# nothing the system needs is named like this in these places (Python packages are left alone)
find "$R/etc" "$R/home" "$R/root" "$R/opt" "$R/usr/local" -xdev \
    \( -name '*.bak*' -o -name '*.orig' -o -name '*.old' -o -name '*.save' -o -name '*~' \
       -o -name '*.dpkg-old' -o -name '*.dpkg-dist' -o -name '*.ucf-old' \) \
    -not -path '*/site-packages/*' -not -path '*/.venv/*' -exec rm -rf {} + 2>/dev/null || true

say "updater: no download leftovers or past check results"
rm -rf "$R"/var/lib/thinksmart-update/download/* "$R"/var/lib/thinksmart-update/requests/*
cur=$(readlink "$R/opt/thinksmart/client/current" 2>/dev/null || true)
[ -n "$cur" ] && printf '{"current": "%s"}\n' "$cur" > "$R/var/lib/thinksmart-update/status.json"

say "done"
