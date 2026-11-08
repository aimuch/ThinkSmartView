#!/usr/bin/env bash
# 生成 / 校验 Homebrew cask（issue #61）。
#
# 用法：
#   packaging/homebrew/update_cask.sh --version 1.0.6                 # 下载已发布 DMG、校验后写入 Casks/
#   packaging/homebrew/update_cask.sh --version 1.0.6 --check         # 只校验现有 cask 是否与已发布版本一致
#   packaging/homebrew/update_cask.sh --version 1.0.6 --cask-dir out  # 写到别处（CI dry-run 的 artifact）
#   packaging/homebrew/update_cask.sh --version 1.0.6 \
#       --source-file MenuBarMijia-1.0.6.dmg --expected-sha256 <hex>  # 离线 fixture（不联网）
#
# 输入只有「版本号」一个可变量，其余全部由固定常量推导：
#   - 源仓库固定为 jryang1997/MenuBarMijia，不接受 --repo / --tag / --url 覆盖；
#   - 版本号必须严格是 X.Y.Z 十进制，且对应 release 必须已发布（非 draft、非 prerelease）；
#   - 附件名固定为 MenuBarMijia-<version>.dmg / .dmg.sha256，附件下载地址必须逐字等于推导出的 URL。
# 因此脚本无法把一个任意仓库、任意 tag 或任意 URL 的内容写进 cask。
#
# SHA-256 交叉校验（任一不符即失败，绝不只信一个来源）：
#   1. 本地对整个 DMG 计算的 SHA-256；
#   2. GitHub Releases API 给该附件的服务端 digest；
#   3. release 里发布的 MenuBarMijia-<version>.dmg.sha256 附件内容（若存在）。
# 三者必须一致；(1) 与 (2)(3) 至少要有一个可比，否则视为无法验证并失败。
#
# 依赖：bash、curl、python3（解析 release JSON）、shasum 或 sha256sum 或 openssl。
# 可选：GH_TOKEN（只提高 GitHub API 速率上限，缺失也能读公开仓库）。
set -euo pipefail
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 唯一允许的源：改这里等于改发布源，属于需要 review 的改动。
SOURCE_REPO="jryang1997/MenuBarMijia"
SOURCE_HOST="github.com"
CASK_NAME="menubarmijia"
APP_NAME="MenuBarMijia"
TEMPLATE="$SCRIPT_DIR/${CASK_NAME}.rb.tmpl"
DEFAULT_CASK_DIR="$REPO_ROOT/packaging/homebrew/Casks"

TMP_DIR=""

fail() {
    echo "❌ $*" >&2
    exit 1
}

info() {
    echo "▶ $*"
}

cleanup() {
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

usage() {
    cat >&2 <<'EOF'
用法：packaging/homebrew/update_cask.sh --version <X.Y.Z> [选项]

选项：
  --version <X.Y.Z>        要写进 tap 的 MenuBarMijia 版本（不带 v 前缀，例如 1.0.6）
  --cask-dir <目录>        写入目录（默认 packaging/homebrew/Casks）
  --check                  不写文件；生成的 cask 与 --cask-dir 中的不一致时以非零码退出
  --source-file <DMG>      离线 fixture：用本地 DMG 代替下载（必须同时给 --expected-sha256）
  --expected-sha256 <hex>  离线 fixture 的独立期望 SHA-256（64 位十六进制）
  --github-output <文件>   把 version / sha256 / mode 追加写入 $GITHUB_OUTPUT 风格文件
  -h, --help               显示本帮助
EOF
}

die_usage() {
    usage
    exit 2
}

VERSION=""
CASK_DIR="$DEFAULT_CASK_DIR"
CHECK_ONLY=0
SOURCE_FILE=""
EXPECTED_SHA=""
GITHUB_OUTPUT_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            [ $# -ge 2 ] || die_usage
            VERSION="$2"
            shift 2
            ;;
        --cask-dir)
            [ $# -ge 2 ] || die_usage
            CASK_DIR="$2"
            shift 2
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --source-file)
            [ $# -ge 2 ] || die_usage
            SOURCE_FILE="$2"
            shift 2
            ;;
        --expected-sha256)
            [ $# -ge 2 ] || die_usage
            EXPECTED_SHA="$(printf '%s' "$2" | tr 'A-F' 'a-f')"
            shift 2
            ;;
        --github-output)
            [ $# -ge 2 ] || die_usage
            GITHUB_OUTPUT_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "❌ 未知参数：$1" >&2
            die_usage
            ;;
    esac
done

[ -n "$VERSION" ] || die_usage

# 只接受 X.Y.Z：既排除 "v1.0.6" 之类，也排除任何可以逃出 URL 路径的字符。
if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$'; then
    echo "❌ 版本号必须是 X.Y.Z 形式的十进制版本（当前：'$VERSION'）" >&2
    exit 2
fi
if [ "$VERSION" = "0.0.0" ]; then
    echo "❌ 0.0.0 不是有效发布版本" >&2
    exit 2
fi

# 只有下面这两个固定推导出来的 URL 会被访问。
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
SHA_NAME="${DMG_NAME}.sha256"
DMG_URL="https://${SOURCE_HOST}/${SOURCE_REPO}/releases/download/v${VERSION}/${DMG_NAME}"
SHA_URL="https://${SOURCE_HOST}/${SOURCE_REPO}/releases/download/v${VERSION}/${SHA_NAME}"

[ -f "$TEMPLATE" ] || fail "缺少 cask 模板：$TEMPLATE"

sha256_of() {
    local file="$1"
    # 用 stdin 喂给校验工具：这样输出里只有摘要本身，不会被文件名里的特殊字符（例如 Windows
    # 路径的反斜杠会让 GNU coreutils 整行转义）污染成 "\abc…" 这种形态。
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 < "$file" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$file" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 < "$file" | awk '{print $NF}'
    else
        fail "缺少 SHA-256 工具（需要 shasum / sha256sum / openssl 之一）"
    fi
}

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    fail "缺少 python3（用于解析 release JSON）"
fi

# 所有联网请求都走这一个函数：只允许 HTTPS，带连接与整体超时，避免 CI 被一个挂住的连接拖死。
# 不接受 4xx/5xx，也不跟随到非 HTTPS 的跳转。
http_get() {
    local url="$1"
    local output="$2"
    shift 2
    curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 300 \
        --retry 3 --retry-delay 2 --retry-connrefused \
        "$@" -o "$output" "$url"
}

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/menubarmijia-cask.XXXXXX")"
DOWNLOADED="$TMP_DIR/$DMG_NAME"

API_DIGEST=""
SHA_ASSET_DIGEST=""
EXPECTED_SIZE=""
SOURCE_MODE="published"

if [ -n "$SOURCE_FILE" ]; then
    # 离线 fixture：只有一个显式给出的独立期望值，不声称验证过任何 release 元数据。
    SOURCE_MODE="fixture"
    [ -f "$SOURCE_FILE" ] || fail "缺少本地 fixture：$SOURCE_FILE"
    if ! printf '%s' "$EXPECTED_SHA" | grep -Eq '^[0-9a-f]{64}$'; then
        fail "--source-file 必须同时提供 --expected-sha256 <64 位十六进制>（离线模式不能拿下载内容自证）"
    fi
    cp "$SOURCE_FILE" "$DOWNLOADED"
else
    command -v curl >/dev/null 2>&1 || fail "缺少 curl（下载已发布附件与读取 release 元数据）"

    info "读取已发布 release：${SOURCE_REPO} v${VERSION}"
    api_json="$TMP_DIR/release.json"
    api_headers=(-H "Accept: application/vnd.github+json")
    if [ -n "${GH_TOKEN:-}" ]; then
        api_headers+=(-H "Authorization: Bearer ${GH_TOKEN}")
    fi
    if ! http_get "https://api.github.com/repos/${SOURCE_REPO}/releases/tags/v${VERSION}" "$api_json" "${api_headers[@]}"; then
        echo "❌ 读不到 ${SOURCE_REPO} 的 v${VERSION} release（tag 必须存在且已正式发布）" >&2
        exit 2
    fi

    # 单次解析出全部需要的元数据，并在同一次运行里完成结构校验，后续只消费这些标量。
    metadata="$TMP_DIR/metadata.env"
    "$PYTHON_BIN" - "$api_json" "$VERSION" "$DMG_NAME" "$SHA_NAME" "$DMG_URL" > "$metadata" <<'PY' || exit 1
import json
import sys

json_path, version, dmg_name, sha_name, dmg_url = sys.argv[1:6]
with open(json_path, encoding="utf-8") as handle:
    data = json.load(handle)

expected_tag = f"v{version}"
if data.get("tag_name") != expected_tag:
    sys.exit(f"❌ release 的 tag 是 {data.get('tag_name')!r}，不是 {expected_tag!r}")
if data.get("draft") is not False:
    sys.exit("❌ 该 release 仍是 draft：draft 附件对外不可下载，不能写进 cask")
if data.get("prerelease") is not False:
    sys.exit("❌ 该 release 标记为 prerelease，不进入默认 tap")

assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
dmg = assets.get(dmg_name)
if dmg is None:
    sys.exit(f"❌ release 里没有名为 {dmg_name} 的附件（现有：{sorted(assets)}）")
if dmg.get("state") not in (None, "uploaded"):
    sys.exit(f"❌ {dmg_name} 的 state 是 {dmg.get('state')!r}，附件未上传完成")
url = dmg.get("browser_download_url") or ""
if url != dmg_url:
    sys.exit(f"❌ 附件下载地址 {url!r} 与推导出的固定地址 {dmg_url!r} 不一致")
size = int(dmg.get("size") or 0)
if size <= 0:
    sys.exit(f"❌ {dmg_name} 的大小是 {dmg.get('size')!r}")

digest = dmg.get("digest") or ""
if digest and not digest.startswith("sha256:"):
    sys.exit(f"❌ 附件 digest 不是 sha256：{digest!r}")

sha_asset = assets.get(sha_name)
if sha_asset is not None and sha_asset.get("state") not in (None, "uploaded"):
    sys.exit(f"❌ {sha_name} 的 state 是 {sha_asset.get('state')!r}，附件未上传完成")

print(f"API_DIGEST={digest[len('sha256:'):] if digest else ''}")
print(f"SHA_ASSET_PRESENT={1 if sha_asset else 0}")
print(f"EXPECTED_SIZE={size}")
PY
    # shellcheck disable=SC1090
    . "$metadata"

    if ! printf '%s' "$API_DIGEST" | grep -Eq '^[0-9a-f]{64}$'; then
        [ "${SHA_ASSET_PRESENT:-0}" = "1" ] \
            || fail "release 既没有服务端 digest 也没有 ${SHA_NAME} 附件，无法独立校验 SHA-256"
        echo "::warning::${DMG_NAME} 没有服务端 digest，改用发布的 ${SHA_NAME} 附件作为期望值。" >&2
        API_DIGEST=""
    fi

    info "下载 ${DMG_URL}"
    http_get "$DMG_URL" "$DOWNLOADED" || fail "下载失败：$DMG_URL"

    actual_size="$(wc -c < "$DOWNLOADED" | tr -d '[:space:]')"
    if [ "$EXPECTED_SIZE" != "$actual_size" ]; then
        fail "下载字节数与 release 元数据不一致：元数据 ${EXPECTED_SIZE}，实际 ${actual_size}"
    fi

    if [ "${SHA_ASSET_PRESENT:-0}" = "1" ]; then
        info "下载并核对发布的校验文件 ${SHA_NAME}"
        sha_file="$TMP_DIR/$SHA_NAME"
        http_get "$SHA_URL" "$sha_file" || fail "下载失败：$SHA_URL"
        SHA_ASSET_DIGEST="$(awk 'NR==1 {print tolower($1)}' "$sha_file")"
        if ! printf '%s' "$SHA_ASSET_DIGEST" | grep -Eq '^[0-9a-f]{64}$'; then
            fail "${SHA_NAME} 内容不是有效的 SHA-256：$(head -n 1 "$sha_file")"
        fi
    fi

    # tag 指向的提交里 VERSION 必须就是这个版本，避免给“给别的版本打的 tag”生成 cask。
    info "核对 v${VERSION} 的 VERSION 文件"
    version_file="$TMP_DIR/VERSION"
    if ! http_get "https://raw.githubusercontent.com/${SOURCE_REPO}/v${VERSION}/VERSION" "$version_file"; then
        fail "读不到 v${VERSION} 的 VERSION 文件（tag 是否存在？）"
    fi
    tag_version="$(tr -d '[:space:]' < "$version_file")"
    if [ "$tag_version" != "$VERSION" ]; then
        fail "v${VERSION} 的 VERSION 文件内容为 '${tag_version}'，与版本号不一致"
    fi

    if [ -n "$SHA_ASSET_DIGEST" ] && [ -n "$API_DIGEST" ] && [ "$SHA_ASSET_DIGEST" != "$API_DIGEST" ]; then
        fail "服务端 digest ${API_DIGEST} 与发布的 ${SHA_NAME} 内容 ${SHA_ASSET_DIGEST} 不一致"
    fi
    EXPECTED_SHA="${API_DIGEST:-$SHA_ASSET_DIGEST}"
fi

[ -s "$DOWNLOADED" ] || fail "DMG 文件为空：$DOWNLOADED"

actual_sha="$(sha256_of "$DOWNLOADED" | tr 'A-F' 'a-f')"
if ! printf '%s' "$actual_sha" | grep -Eq '^[0-9a-f]{64}$'; then
    fail "无法计算 SHA-256（得到 '${actual_sha}'）"
fi
if [ "$actual_sha" != "$EXPECTED_SHA" ]; then
    fail "SHA-256 不匹配：期望 ${EXPECTED_SHA}，实际 ${actual_sha}"
fi
if [ -n "$SHA_ASSET_DIGEST" ] && [ "$SHA_ASSET_DIGEST" != "$actual_sha" ]; then
    fail "与发布的 ${SHA_NAME} 不一致：文件写 ${SHA_ASSET_DIGEST}，实际 ${actual_sha}"
fi
info "SHA-256 一致：${actual_sha}"

RENDERED="$TMP_DIR/${CASK_NAME}.rb"
sed -e "s/@VERSION@/${VERSION}/g" -e "s/@SHA256@/${actual_sha}/g" "$TEMPLATE" > "$RENDERED"

# 渲染结果自检：占位符必须全部替换，关键约束必须仍在（防止模板被改成绕过 quarantine 之类）。
if grep -q '@[A-Z_]*@' "$RENDERED"; then
    fail "渲染后的 cask 仍有未替换的占位符：$(grep -o '@[A-Z_]*@' "$RENDERED" | sort -u | tr '\n' ' ')"
fi
grep -qx "  version \"${VERSION}\"" "$RENDERED" || fail "渲染后的 cask 缺少 version \"${VERSION}\""
grep -qx "  sha256 \"${actual_sha}\"" "$RENDERED" || fail "渲染后的 cask 的 sha256 行不正确"
grep -qF "https://${SOURCE_HOST}/${SOURCE_REPO}/releases/download/v#{version}/${APP_NAME}-#{version}.dmg" "$RENDERED" \
    || fail "渲染后的 cask 的 URL 不是由 version 推导的固定 release 地址"
grep -qF "verified: \"${SOURCE_HOST}/${SOURCE_REPO}/\"" "$RENDERED" || fail "cask 缺少 verified 域名绑定"
grep -qF 'depends_on macos: :sonoma' "$RENDERED" || fail "cask 缺少 macOS 14 (Sonoma) 最低版本"
grep -qF "  app \"${APP_NAME}.app\"" "$RENDERED" || fail "cask 缺少 app \"${APP_NAME}.app\""
# 只在非注释行里拒绝 quarantine 绕过：注释不执行，但代码行里出现这些就没人能相信 cask 不带钩子。
if grep -v '^[[:space:]]*#' "$RENDERED" \
    | grep -Eq 'xattr|spctl|postflight|preflight|com\.apple\.quarantine'; then
    fail "cask 里不允许出现 quarantine 绕过（xattr / spctl / postflight / preflight）"
fi

TARGET_DIR="$CASK_DIR"
TARGET_FILE="$TARGET_DIR/${CASK_NAME}.rb"

if [ "$CHECK_ONLY" = "1" ]; then
    [ -f "$TARGET_FILE" ] || fail "${TARGET_FILE} 不存在：tap 还没有这个 cask"
    if ! diff -u "$TARGET_FILE" "$RENDERED" >&2; then
        echo "❌ ${TARGET_FILE} 与已发布 v${VERSION}（sha256 ${actual_sha}）不一致" >&2
        exit 1
    fi
    echo "✅ cask 已与已发布 v${VERSION} 一致"
else
    mkdir -p "$TARGET_DIR" || fail "无法创建目录：$TARGET_DIR"
    tmp_target="$(mktemp "$TARGET_DIR/.${CASK_NAME}.rb.XXXXXX")"
    cp "$RENDERED" "$tmp_target"
    chmod 644 "$tmp_target"
    mv "$tmp_target" "$TARGET_FILE"
    echo "✅ 已写入 ${TARGET_FILE}（v${VERSION}，sha256 ${actual_sha}）"
fi

if [ -n "$GITHUB_OUTPUT_FILE" ]; then
    {
        echo "version=${VERSION}"
        echo "sha256=${actual_sha}"
        echo "cask=${CASK_NAME}.rb"
        echo "source_mode=${SOURCE_MODE}"
    } >> "$GITHUB_OUTPUT_FILE"
fi
