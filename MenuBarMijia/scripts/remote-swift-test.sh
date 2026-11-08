#!/usr/bin/env bash
# Run this worktree's Swift tests on a Mac over SSH. The local machine needs no Swift toolchain.
#
# One-time setup:
#   1. System Settings > General > Sharing > Remote Login on the Mac.
#   2. Authorize this machine's key from a real terminal (asks for the Mac password once):
#      type %USERPROFILE%\.ssh\id_ed25519.pub | ssh <user>@<mac> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
#   3. Check it works: ssh -o BatchMode=yes <user>@<mac> "swift --version"
#
# Usage:
#   SWIFT_TEST_HOST=user@mac bash scripts/remote-swift-test.sh [swift test arguments...]
#   bash scripts/remote-swift-test.sh --host user@mac --filter MiAccountsProviderTests
#
# The worktree is copied with tar (no rsync needed) to ~/.cache/menubarmijia-swift-test
# on the Mac. .build stays there between runs, so only changed sources rebuild.
set -euo pipefail

host="${SWIFT_TEST_HOST:-}"
if [ "${1:-}" = "--host" ]; then
    host="${2:-}"
    shift 2
fi
if [ -z "$host" ]; then
    echo "缺少主机：设置 SWIFT_TEST_HOST=user@mac，或用 --host user@mac" >&2
    exit 2
fi

root="$(git rev-parse --show-toplevel)"
cd "$root"

if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$host" 'command -v swift >/dev/null'; then
    echo "无法用密钥登录 $host，或远端没有 swift。先完成 ~/.ssh/authorized_keys 授权。" >&2
    exit 1
fi

remote_dir="${SWIFT_TEST_REMOTE_DIR:-\$HOME/.cache/menubarmijia-swift-test}"
echo "同步工作树 -> $host:$remote_dir"
# Mirror the local tree: a deleted or renamed file must not stay behind and compile. .build is the
# only thing the remote keeps between runs.
ssh -o BatchMode=yes "$host" \
    "mkdir -p $remote_dir && cd $remote_dir && find . -mindepth 1 -maxdepth 1 ! -name .build -exec rm -rf {} +"
tar -czf - \
    --exclude=.git \
    --exclude=.build \
    --exclude=node_modules \
    --exclude=windows/node_modules \
    . | ssh -o BatchMode=yes "$host" "tar -xzf - -C $remote_dir"

# Quote each argument so a filter regex or a space reaches Swift intact.
test_args=""
for argument in "$@"; do
    test_args="$test_args $(printf '%q' "$argument")"
done
echo "远端运行：swift test$test_args"
ssh -o BatchMode=yes "$host" "cd $remote_dir && swift test$test_args"
