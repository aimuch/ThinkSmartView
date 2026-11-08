#!/usr/bin/env bash
# update_cask.sh 的离线自检（issue #61）。
#
# 全部使用本地合成 fixture，不联网、不需要任何凭据，也不写仓库里的 Casks/ 目录：
#   - 正常路径能按 (version, sha256) 渲染出符合 cask 约束的文件；
#   - SHA-256 不符、缺少独立期望值、版本号非法、未知参数一律失败；
#   - 模板被塞进 quarantine 绕过、缺 app stanza、缺最低系统版本时拒绝生成；
#   - --check 能发现漂移，且对刚生成的文件通过。
#
# 用法：bash packaging/homebrew/self_test.sh
set -euo pipefail
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK=""

cleanup() {
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT

passed=0
failed=0

ok() {
    passed=$((passed + 1))
    echo "  ✅ $*"
}

bad() {
    failed=$((failed + 1))
    echo "  ❌ $*" >&2
}

# 期望失败：命令必须返回非零；want 给定时还要求退出码与之相等。
expect_fail() {
    local label="$1"
    local want="$2"
    shift 2
    local status=0
    "$@" >"$WORK/out.log" 2>&1 || status=$?
    if [ "$status" -eq 0 ]; then
        bad "$label（命令意外成功）"
        sed 's/^/     /' "$WORK/out.log" >&2
        return
    fi
    if [ -n "$want" ] && [ "$status" != "$want" ]; then
        bad "$label（期望退出码 $want，实际 $status）"
        sed 's/^/     /' "$WORK/out.log" >&2
        return
    fi
    ok "$label（exit=$status）"
}

expect_ok() {
    local label="$1"
    shift
    if "$@" >"$WORK/out.log" 2>&1; then
        ok "$label"
    else
        bad "$label"
        sed 's/^/     /' "$WORK/out.log" >&2
    fi
}

sha256_of() {
    local file="$1"
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 < "$file" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$file" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 < "$file" | awk '{print $NF}'
    else
        echo "缺少 SHA-256 工具" >&2
        exit 1
    fi
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/menubarmijia-cask-selftest.XXXXXX")"

# 每个场景用一份独立拷贝，这样改坏模板只影响那一个场景。
prepare_copy() {
    local name="$1"
    local dir="$WORK/$name"
    mkdir -p "$dir"
    cp "$SCRIPT_DIR/update_cask.sh" "$dir/update_cask.sh"
    cp "$SCRIPT_DIR/menubarmijia.rb.tmpl" "$dir/menubarmijia.rb.tmpl"
    printf '%s\n' "$dir"
}

# 合成一个“DMG”：脚本只把它当字节流处理，签名字样与大小都不影响生成逻辑。
FIXTURE_DMG="$WORK/MenuBarMijia-1.0.6.dmg"
printf 'synthetic dmg payload for self test\n' > "$FIXTURE_DMG"
FIXTURE_SHA="$(sha256_of "$FIXTURE_DMG")"

echo "▶ [1/5] 正常路径"
base="$(prepare_copy base)"
out="$WORK/out"
expect_ok "fixture 生成成功" \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" \
        --cask-dir "$out" --github-output "$WORK/gh-out.txt"
generated="$out/menubarmijia.rb"
if [ -f "$generated" ]; then
    ok "生成了 menubarmijia.rb"
else
    bad "没有生成 menubarmijia.rb"
fi
if grep -qx '  version "1.0.6"' "$generated"; then
    ok "version 行正确"
else
    bad "version 行不正确"
fi
if grep -qx "  sha256 \"$FIXTURE_SHA\"" "$generated"; then
    ok "sha256 行正确"
else
    bad "sha256 行不正确"
fi
if grep -qF 'depends_on macos: :sonoma' "$generated" \
    && grep -qF 'app "MenuBarMijia.app"' "$generated" \
    && grep -qF 'verified: "github.com/jryang1997/MenuBarMijia/"' "$generated"; then
    ok "保留最低系统版本、app artifact 与 verified 域名"
else
    bad "关键 stanza 缺失"
fi
if ! grep -v '^[[:space:]]*#' "$generated" | grep -Eq 'xattr|spctl|postflight|preflight'; then
    ok "生成结果不含 quarantine 绕过"
else
    bad "生成结果出现了 quarantine 绕过"
fi
if grep -qx "version=1.0.6" "$WORK/gh-out.txt" \
    && grep -qx "sha256=$FIXTURE_SHA" "$WORK/gh-out.txt" \
    && grep -qx 'source_mode=fixture' "$WORK/gh-out.txt"; then
    ok "GITHUB_OUTPUT 风格输出正确"
else
    bad "GITHUB_OUTPUT 风格输出不正确"
fi
if command -v ruby >/dev/null 2>&1; then
    if ruby -c "$generated" >/dev/null 2>&1; then
        ok "生成的 cask 通过 ruby -c 语法检查"
    else
        bad "生成的 cask 语法错误"
        ruby -c "$generated" >&2 || true
    fi
else
    echo "  ⏭ 跳过 ruby -c（本机没有 ruby；CI 的 ubuntu-latest 自带）"
fi

echo "▶ [2/5] SHA-256 与 fixture 前置条件"
expect_fail "SHA-256 不符必须失败" 1 \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" \
        --expected-sha256 "0000000000000000000000000000000000000000000000000000000000000000" \
        --cask-dir "$WORK/neg-sha"
expect_fail "fixture 缺少期望 SHA 必须失败" 1 \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --cask-dir "$WORK/neg-nosha"
expect_fail "fixture 的期望 SHA 格式错误必须失败" 1 \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "nothex" --cask-dir "$WORK/neg-badsha"
if [ -e "$WORK/neg-sha" ] || [ -e "$WORK/neg-nosha" ] || [ -e "$WORK/neg-badsha" ]; then
    bad "失败的运行不应写出任何 cask 目录"
else
    ok "失败路径没有留下输出"
fi

echo "▶ [3/5] 版本号与参数把关"
for bad_version in "v1.0.6" "1.0" "1.0.6.7" "1.0.x" "0.0.0" "1.0.6/../x" "1.0.6; rm -rf /" ""; do
    expect_fail "版本 '$bad_version' 必须被拒绝" 2 \
        bash "$base/update_cask.sh" --version "$bad_version" \
            --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" \
            --cask-dir "$WORK/neg-version"
done
expect_fail "未知参数必须被拒绝" 2 \
    bash "$base/update_cask.sh" --version 1.0.6 --repo evil/fork --tag v9.9.9
expect_fail "缺少 --version 必须被拒绝" 2 bash "$base/update_cask.sh"

echo "▶ [4/5] 模板被改坏时必须拒绝"
postflight="$(prepare_copy postflight)"
printf '\n  postflight do\n    system_command "/usr/bin/xattr", args: ["-cr", "#{appdir}/MenuBarMijia.app"]\n  end\n' \
    >> "$postflight/menubarmijia.rb.tmpl"
expect_fail "模板带 xattr postflight 必须被拒绝" 1 \
    bash "$postflight/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" --cask-dir "$WORK/neg-hook"

noapp="$(prepare_copy noapp)"
sed -i.bak 's|^  app "MenuBarMijia.app"$|  # app stanza removed|' "$noapp/menubarmijia.rb.tmpl"
expect_fail "模板缺 app stanza 必须被拒绝" 1 \
    bash "$noapp/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" --cask-dir "$WORK/neg-noapp"

nosonoma="$(prepare_copy nosonoma)"
sed -i.bak 's|^  depends_on macos: :sonoma$||' "$nosonoma/menubarmijia.rb.tmpl"
expect_fail "模板缺最低系统版本必须被拒绝" 1 \
    bash "$nosonoma/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" --cask-dir "$WORK/neg-nosonoma"

echo "▶ [5/5] --check 漂移检测"
expect_ok "--check 对刚生成的内容通过" \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" \
        --cask-dir "$out" --check
drift="$WORK/drift"
mkdir -p "$drift"
sed 's/^  sha256 ".*"$/  sha256 "deadbeef"/' "$generated" > "$drift/menubarmijia.rb"
expect_fail "--check 能发现被手改的 cask" 1 \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" \
        --cask-dir "$drift" --check
if cmp -s "$drift/menubarmijia.rb" <(sed 's/^  sha256 ".*"$/  sha256 "deadbeef"/' "$generated"); then
    ok "--check 不修改已有文件"
else
    bad "--check 修改了已有文件"
fi
expect_fail "--check 在目录缺少 cask 时失败" 1 \
    bash "$base/update_cask.sh" --version 1.0.6 \
        --source-file "$FIXTURE_DMG" --expected-sha256 "$FIXTURE_SHA" \
        --cask-dir "$WORK/neg-check-missing" --check

echo
if [ "$failed" -ne 0 ]; then
    echo "❌ 自检失败：$failed 项（通过 $passed 项）" >&2
    exit 1
fi
echo "✅ 自检通过：$passed 项"
