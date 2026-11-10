#!/usr/bin/env bash
# Build a client (+ system layer) release from this repository and publish it to the release
# server on 10.10.10.224 (~/thinksmart-releases, served on its 127.0.0.1:16690); the device sees it
# under 设置 → 软件更新 and installs it with one tap.
#   scripts/publish-release.sh --note "客户端改了什么" [--note ...] [--system-note "系统层改了什么"]
# Before: raise __version__ in client/thinksmart_client/__init__.py (upstream
# patch × 100 + our build: upstream 0.1.6 → 0.1.601, 0.1.602 …), commit everything.
# The Python environment is built in an arm64 container (Docker) unless the cache already has the
# one for this requirements.lock (~/.cache/thinksmart-release/envs).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HUB="${HUB:-andy@10.10.10.224}"
HUB_KEY="${HUB_KEY:-$HOME/.ssh/id_ed25519_btrouter}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

[[ -z "$(git -C "$ROOT" status --porcelain -- client system)" ]] \
  || { echo "commit the client and system changes first" >&2; exit 1; }

python3 "$ROOT/system/update/make-release.py" "$ROOT/client" "$OUT" \
  --system "$ROOT/system" "$@"

ssh -i "$HUB_KEY" "$HUB" 'mkdir -p ~/thinksmart-releases/download ~/thinksmart-releases/latest/download'
# the packages first, latest.json last: a device checking meanwhile never sees a manifest whose files are missing
rsync -a -e "ssh -i $HUB_KEY" "$OUT/download/" "$HUB:thinksmart-releases/download/"
rsync -a -e "ssh -i $HUB_KEY" "$OUT/latest/download/latest.json" "$HUB:thinksmart-releases/latest/download/latest.json.new"
ssh -i "$HUB_KEY" "$HUB" 'mv ~/thinksmart-releases/latest/download/latest.json.new ~/thinksmart-releases/latest/download/latest.json &&
  curl -s http://127.0.0.1:16690/latest/download/latest.json | python3 -c "import json,sys; m=json.load(sys.stdin); print(\"published:\", m[\"version\"], (m.get(\"system\") or {}).get(\"version\", \"\"))"'
