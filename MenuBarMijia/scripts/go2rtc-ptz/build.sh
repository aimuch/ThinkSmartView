#!/usr/bin/env bash
# Build the pinned go2rtc PTZ binary from the upstream v1.9.15 tag plus one patch.
# Never publishes and never touches the user's daily go2rtc binary.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_COMMIT="7c05066c90ab6ad1fccb506d5f0408aa31ebe60f"
PATCH_FILE="$ROOT_DIR/scripts/go2rtc-ptz/patches/0001-xiaomi-ptz.patch"
OUT_DIR="$ROOT_DIR/scripts/go2rtc-ptz/out"
UPSTREAM="https://github.com/AlexxIT/go2rtc"

if ! command -v go >/dev/null 2>&1; then
    echo "❌ 未找到 go（需要 Go 1.26+）。安装后重试：brew install go" >&2
    exit 1
fi
if ! command -v lipo >/dev/null 2>&1; then
    echo "❌ 未找到 lipo（Xcode Command Line Tools）" >&2
    exit 1
fi

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo "▶ 克隆 go2rtc v1.9.15 ($BASE_COMMIT)"
git clone --quiet --filter=blob:none "$UPSTREAM" "$WORK/go2rtc"
git -C "$WORK/go2rtc" checkout --quiet "$BASE_COMMIT"
git -C "$WORK/go2rtc" apply --index "$PATCH_FILE"

mkdir -p "$OUT_DIR"
build() {
    local goarch="$1" output="$2"
    echo "▶ 构建 darwin/$goarch"
    (cd "$WORK/go2rtc" && CGO_ENABLED=0 GOOS=darwin GOARCH="$goarch" \
        go build -trimpath -ldflags "-s -w" -o "$output" .)
}
build arm64 "$OUT_DIR/go2rtc-darwin-arm64"
build amd64 "$OUT_DIR/go2rtc-darwin-amd64"
lipo -create "$OUT_DIR/go2rtc-darwin-arm64" "$OUT_DIR/go2rtc-darwin-amd64" \
    -output "$OUT_DIR/go2rtc-universal"
chmod 755 "$OUT_DIR/go2rtc-universal"

echo "▶ 构建 windows/amd64（不参与 macOS 打包，供后续验证）"
(cd "$WORK/go2rtc" && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 \
    go build -trimpath -ldflags "-s -w" -o "$OUT_DIR/go2rtc-windows-amd64.exe" .)

PATCH_SHA="$(shasum -a 256 "$PATCH_FILE" | awk '{print $1}')"
printf 'patch_sha256=%s\nheader_version=v1\n' "$PATCH_SHA" > "$OUT_DIR/go2rtc-ptz.manifest"

echo "✅ 构建完成（未发布、未覆盖日用二进制）"
shasum -a 256 "$OUT_DIR"/go2rtc-* | sed "s|$OUT_DIR/||"
