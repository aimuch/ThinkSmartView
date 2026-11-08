#!/usr/bin/env bash
# Fails when UI rendering sources changed without any committed snapshot baseline update.
# Local drift triage answers "were these mismatches already here", never "does this UI change
# owe a new baseline" (#123: the macos job surfaced nine stale baselines after a green local story).
#
# Usage:
#   scripts/check_snapshot_baselines.sh [base] [head]
#   scripts/check_snapshot_baselines.sh --paths <file>
#
# base defaults to origin/main, head to HEAD. --paths reads a newline-separated changed-file list
# instead of asking git, so a CI job can feed an API result.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

SOURCE_PREFIX="Sources/MenuBarMijiaApp/"
# Files under SOURCE_PREFIX whose changes cannot reach any snapshot: AppKit event and key routing
# that no snapshot test renders. Add a file only when nothing it defines draws into
# `SnapshotTests`; a missed baseline is worse than one more `no-snapshot-change` label.
NON_RENDERING=(
    "Sources/MenuBarMijiaApp/MenuBarPanelController.swift"
    "Sources/MenuBarMijiaApp/HotkeyAndNotificationManager.swift"
)
BASELINE_PREFIX="Tests/MenuBarMijiaAppTests/__Snapshots__/"
PATHS_FILE=""
POSITIONAL=()
while [ $# -gt 0 ]; do
    case "$1" in
        --paths)
            PATHS_FILE="${2:-}"
            shift 2
            ;;
        -h|--help)
            awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
            exit 0
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

if [ -n "$PATHS_FILE" ]; then
    [ -f "$PATHS_FILE" ] || { echo "❌ 找不到改动文件列表：$PATHS_FILE" >&2; exit 1; }
    CHANGED="$(sed '/^[[:space:]]*$/d' "$PATHS_FILE")"
else
    BASE="${POSITIONAL[0]:-origin/main}"
    HEAD_REF="${POSITIONAL[1]:-HEAD}"
    if ! git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null; then
        echo "❌ 无法解析基线 ref：$BASE（先 git fetch origin）" >&2
        exit 1
    fi
    if [ "$HEAD_REF" = "HEAD" ]; then
        # Default mode is the local pre-PR check: compare base against the working tree so
        # uncommitted edits count, and add untracked files because `git diff` omits them.
        CHANGED="$(git diff --name-only "$BASE")
$(git ls-files --others --exclude-standard)"
    else
        CHANGED="$(git diff --name-only "$BASE...$HEAD_REF")"
    fi
fi

SOURCES="$(printf '%s\n' "$CHANGED" | grep -E "^${SOURCE_PREFIX}" | grep -vxF -f <(printf '%s\n' "${NON_RENDERING[@]}") || true)"
BASELINES="$(printf '%s\n' "$CHANGED" | grep -E "^${BASELINE_PREFIX}" || true)"

if [ -z "$SOURCES" ]; then
    echo "✅ 未改动面板渲染源码，无需刷新快照基线。"
    exit 0
fi
if [ -n "$BASELINES" ]; then
    echo "✅ 面板渲染源码与快照基线一并更新。"
    exit 0
fi

cat >&2 <<'MSG'
❌ 改了面板渲染源码，却没有更新 UI 快照基线。

改动到的渲染文件：
MSG
printf '%s\n' "$SOURCES" | sed 's/^/  - /' >&2
cat >&2 <<'MSG'

重录（在维护者这台 Mac 上，它也是 CI runner）：
  bash scripts/record_snapshots.sh --dry-run   # 只列出会变的 PNG
  bash scripts/record_snapshots.sh             # 写入变化的 PNG，审阅后提交
新增只在某个状态下出现的视图（横幅、提示、空状态）时，先给该状态加快照用例再录制。
确实不影响渲染时：这个文件永远画不进截图，就把它加进本脚本的 NON_RENDERING 名单；
只是这一次不影响，就给 PR 加 no-snapshot-change 标签跳过本检查。
MSG
exit 1
