#!/usr/bin/env bash
# Deploy this repository's server/ (小麦网关) to 10.10.10.224.
#   scripts/deploy-server.sh            push and update now (update.sh: deps, restart, rolls back if it doesn't come up)
#   scripts/deploy-server.sh --push     only push; install later with 更新网关 in the admin page
# How: the hub's checkout (~/xiaozhi-hub-server) tracks a bare repository on the hub
# (~/repos/xiaozhi-hub-server.git) instead of upstream's GitHub, so its own update.sh and the admin
# page's 更新网关 install our version. Each deploy adds ONE commit on top of that repository's main:
# the tree is server/ as committed here, the message lists what changed since the last deploy (and
# records the source commit). Pushes are always fast-forwards: nothing on the hub is rewritten, and
# renaming server/ here never breaks the hub's history (it did once with git subtree split).
# Upstream changes come in through scripts/sync-upstream.sh first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HUB="${HUB:-andy@10.10.10.224}"
HUB_KEY="${HUB_KEY:-$HOME/.ssh/id_ed25519_btrouter}"
BARE='repos/xiaozhi-hub-server.git'
DIR=server
export GIT_SSH_COMMAND="ssh -i $HUB_KEY -o BatchMode=yes"
hub() { ssh -i "$HUB_KEY" -o BatchMode=yes "$HUB" "$@"; }

cd "$ROOT"
[[ -z "$(git status --porcelain -- "$DIR")" ]] || { echo "commit $DIR/ first" >&2; exit 1; }

hub "[ -d ~/$BARE ] || git init -q --bare -b main ~/$BARE"
remote="$(git ls-remote "$HUB:$BARE" refs/heads/main | cut -f1)"
tree="$(git rev-parse "HEAD:$DIR")"
if [[ -n "$remote" ]]; then
  git cat-file -e "$remote" 2>/dev/null || git fetch -q "$HUB:$BARE" main
  if [[ "$(git rev-parse "$remote^{tree}")" == "$tree" ]]; then
    echo "the hub already has this $DIR/ (${remote:0:12})"
  else
    # what changed since the source commit the last deploy recorded (none recorded: the last 10)
    src="$(git log -1 --format=%B "$remote" | sed -n 's/^Source: //p' | tail -1)"
    if [[ -n "$src" ]] && git cat-file -e "$src" 2>/dev/null; then range="$src..HEAD"; else range="-10"; fi
    msg="$(printf '%s\n\n%s\n\nSource: %s\n' "$(git log -1 --format=%s HEAD -- "$DIR")" \
      "$(git log --format='- %s (%h)' $range -- "$DIR")" "$(git rev-parse HEAD)")"
    commit="$(git commit-tree "$tree" -p "$remote" -m "$msg")"
    git push -q "$HUB:$BARE" "$commit:refs/heads/main"
    echo "pushed ${commit:0:12} on top of ${remote:0:12}"
  fi
else
  commit="$(git commit-tree "$tree" -m "$(git log -1 --format=%s HEAD -- "$DIR")"$'\n\n'"Source: $(git rev-parse HEAD)")"
  git push -q "$HUB:$BARE" "$commit:refs/heads/main"
  echo "pushed ${commit:0:12} (first deploy)"
fi

# the hub's checkout follows our repository (once; idempotent)
hub "cd ~/xiaozhi-hub-server && { git remote get-url ours >/dev/null 2>&1 || git remote add ours ~/$BARE; } \
     && git config branch.main.remote ours && git config branch.main.merge refs/heads/main"

[[ " $* " == *" --push "* ]] && { echo "install it with 更新网关 in the admin page"; exit 0; }
hub 'cd ~/xiaozhi-hub-server && HUB_READY_TIMEOUT=180 bash scripts/update.sh' 2>&1 | tail -15
hub 'cd ~/xiaozhi-hub-server && git log -1 --format="hub now at %h %s"; systemctl --user is-active xiaozhi-hub'
