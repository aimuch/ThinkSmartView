#!/bin/sh
# 把形象库（scripts/build-avatar-pack.py 生成的 local/avatar-pack）装到设备上，设计画布第 41 块。
#   scripts/push-avatar-pack.sh 10.10.10.115 [10.10.10.192 ...]
# 装到 ~/.local/share/thinksmart-client/avatar-pack：先解到旁边的新目录，再整个换过去，正在用的页面不会读到半张图。
# 客户端发现 catalog.json 变了就重读，不用重启。设备上没有 rsync，用 tar 经 ssh。
set -eu
cd "$(dirname "$0")/.."
PACK=local/avatar-pack
KEY=${THINKSMART_KEY:-$HOME/.ssh/thinksmart_ed25519}
KNOWN=${THINKSMART_KNOWN_HOSTS:-$HOME/.ssh/known_hosts_thinksmart}
[ -f "$PACK/catalog.json" ] || { echo "先运行 scripts/build-avatar-pack.py" >&2; exit 1; }
[ $# -gt 0 ] || { echo "用法：$0 设备IP ..." >&2; exit 1; }
for host in "$@"; do
  echo "→ $host"
  COPYFILE_DISABLE=1 tar -C "$PACK" -cf - catalog.json img | ssh -i "$KEY" -o BatchMode=yes -o UserKnownHostsFile="$KNOWN" "ubuntu@$host" '
    set -e
    base=$HOME/.local/share/thinksmart-client
    mkdir -p "$base"
    rm -rf "$base/avatar-pack.new" "$base/avatar-pack.old"
    mkdir "$base/avatar-pack.new"
    tar -C "$base/avatar-pack.new" -xf -
    [ -d "$base/avatar-pack" ] && mv "$base/avatar-pack" "$base/avatar-pack.old"
    mv "$base/avatar-pack.new" "$base/avatar-pack"
    rm -rf "$base/avatar-pack.old"
    echo "  $(ls "$base/avatar-pack/img" | wc -l) 张图，$(du -sh "$base/avatar-pack" | cut -f1)"'
done
