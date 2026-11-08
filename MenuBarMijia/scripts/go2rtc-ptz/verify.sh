#!/usr/bin/env bash
# Re-apply the pinned patch to a clean upstream v1.9.15 checkout and run its tests.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_COMMIT="7c05066c90ab6ad1fccb506d5f0408aa31ebe60f"
PATCH_FILE="$ROOT_DIR/scripts/go2rtc-ptz/patches/0001-xiaomi-ptz.patch"
UPSTREAM="https://github.com/AlexxIT/go2rtc"

if ! command -v go >/dev/null 2>&1; then
    echo "❌ 未找到 go（需要 Go 1.26+）。安装后重试：brew install go" >&2
    exit 1
fi

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo "▶ 校验 Swift/Go 型号表与共享 fixture"
python3 "$ROOT_DIR/scripts/go2rtc-ptz/check_model_tables.py"

echo "▶ 克隆 go2rtc v1.9.15 ($BASE_COMMIT)"
git clone --quiet --filter=blob:none "$UPSTREAM" "$WORK/go2rtc"
git -C "$WORK/go2rtc" checkout --quiet "$BASE_COMMIT"

echo "▶ 应用补丁"
git -C "$WORK/go2rtc" apply --index "$PATCH_FILE"

echo "▶ go test -race"
(cd "$WORK/go2rtc" && go test -race ./internal/xiaomi ./pkg/xiaomi/miss ./pkg/xiaomi/miss/cs2 ./pkg/tutk ./pkg/onvif ./internal/onvif)

echo "▶ go build ./..."
(cd "$WORK/go2rtc" && go build ./...)

echo "✅ 补丁可重现、测试通过"
