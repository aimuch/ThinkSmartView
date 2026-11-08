#!/usr/bin/env bash
# Record the UI snapshot baselines on this Mac and list the PNGs that changed.
#
#   bash scripts/record_snapshots.sh [--dry-run]
#
# This Mac is the reference renderer: the GitLab runner that compares the baselines in CI is the
# same machine (see .gitlab-ci.yml), so a baseline recorded here is the one CI checks. Review the
# changed PNGs as a normal diff before committing.
#   --dry-run  record into a scratch copy and list what would change, leaving the baselines alone
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT_DIR="$(git rev-parse --show-toplevel)"
SNAPSHOT_DIR="$ROOT_DIR/Tests/MenuBarMijiaAppTests/__Snapshots__/SnapshotTests"
DRY_RUN=0
case "${1:-}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    "") ;;
    *) echo "❌ 未知参数：$1（可用：--dry-run）" >&2; exit 2 ;;
esac

backup=""
if [ "$DRY_RUN" = "1" ]; then
    backup="$(mktemp -d "${TMPDIR:-/tmp}/snapshot-baselines.XXXXXX")"
    cp -R "$SNAPSHOT_DIR/." "$backup/"
fi

cd "$ROOT_DIR"
# Record mode reports every snapshot as an issue by design, so the exit status is not a verdict.
SNAPSHOT_RECORD=1 swift test --filter SnapshotTests >/dev/null 2>&1 || true

changed="$(git status --porcelain -- "$SNAPSHOT_DIR" | sed 's#^...##')"
if [ "$DRY_RUN" = "1" ]; then
    rm -rf "$SNAPSHOT_DIR"
    mkdir -p "$SNAPSHOT_DIR"
    cp -R "$backup/." "$SNAPSHOT_DIR/"
    rm -rf "$backup"
fi

if [ -z "$changed" ]; then
    echo "✅ 基线无变化"
    exit 0
fi
printf '%s\n' "$changed" | sed 's#^#  #'
if [ "$DRY_RUN" = "1" ]; then
    echo "ℹ️ 共 $(printf '%s\n' "$changed" | wc -l | tr -d ' ') 张会变化（dry run，未写入）"
else
    echo "✅ 已更新 $(printf '%s\n' "$changed" | wc -l | tr -d ' ') 张基线，审阅后提交"
fi
