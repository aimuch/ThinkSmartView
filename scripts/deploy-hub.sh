#!/usr/bin/env bash
# Deploy this repository's xiaozhi-hub-server/ to the hub on 10.10.10.224.
#   scripts/deploy-hub.sh            push and update now (update.sh: deps, restart, rolls back if it doesn't come up)
#   scripts/deploy-hub.sh --push     only push; install later with 更新网关 in the admin page
# How: `git subtree split` turns the directory into its own history, pushed to a bare repository on
# the hub (~/repos/xiaozhi-hub-server.git). The hub's checkout tracks that instead of upstream's
# GitHub, so its own update.sh and the admin page's 更新网关 install our version. Upstream changes
# come in through scripts/sync-upstream.sh first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HUB="${HUB:-andy@10.10.10.224}"
HUB_KEY="${HUB_KEY:-$HOME/.ssh/id_ed25519_btrouter}"
BARE='repos/xiaozhi-hub-server.git'
export GIT_SSH_COMMAND="ssh -i $HUB_KEY -o BatchMode=yes"
hub() { ssh -i "$HUB_KEY" -o BatchMode=yes "$HUB" "$@"; }

cd "$ROOT"
[[ -z "$(git status --porcelain -- xiaozhi-hub-server)" ]] || { echo "commit xiaozhi-hub-server first" >&2; exit 1; }

hub "[ -d ~/$BARE ] || git init -q --bare -b main ~/$BARE"
split="$(git subtree split -q --prefix=xiaozhi-hub-server)"
git push -q "$HUB:$BARE" "$split:refs/heads/main"
echo "pushed ${split:0:12} to $HUB:~/$BARE"

# the hub's checkout follows our repository (once; idempotent)
hub "cd ~/xiaozhi-hub-server && { git remote get-url ours >/dev/null 2>&1 || git remote add ours ~/$BARE; } \
     && git config branch.main.remote ours && git config branch.main.merge refs/heads/main"

[[ "${1:-}" == "--push" ]] && { echo "install it with 更新网关 in the admin page"; exit 0; }
hub 'cd ~/xiaozhi-hub-server && HUB_READY_TIMEOUT=180 bash scripts/update.sh' 2>&1 | tail -15
hub 'cd ~/xiaozhi-hub-server && git log -1 --format="hub now at %h %s"; systemctl --user is-active xiaozhi-hub'
