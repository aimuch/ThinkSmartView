#!/usr/bin/env bash
# Publish a signed release to GitLab: packages go to the project's generic package registry, the
# release entry links them, and the app downloads from the registry URL it builds itself
# (UpdateChecker.releaseAssetURL), never from the links.
#
#   bash scripts/publish_gitlab_release.sh <version> [dist-dir]
#
# Before: bash scripts/release.sh (writes dist/MenuBarMijia-<v>.dmg and .sha256), then
#   swift scripts/sign_update.swift --min-from <x.y.z> dist/MenuBarMijia-<v>.dmg   # macOS-only
# and push tag v<version>. A manifest with three files also needs the Windows EXE/MSI and their
# .sha256 in the same directory.
# Auth: $GITLAB_TOKEN, or git's credential for gitlab.com (this clone: keychain item menubarmijia-gitlab).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?用法：bash scripts/publish_gitlab_release.sh <version> [dist-dir]}"
DIR="${2:-dist}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "❌ 版本必须是 x.y.z：$VERSION" >&2; exit 2; }
PROJECT="$(sed -n 's/.*gitLabProjectID = "\([0-9]*\)".*/\1/p' Sources/MijiaCore/UpdateChecker.swift)"
[ -n "$PROJECT" ] || { echo "❌ 读不到 UpdateChecker.gitLabProjectID" >&2; exit 1; }
API="https://gitlab.com/api/v4/projects/$PROJECT"
TAG="v$VERSION"

if [ -z "${GITLAB_TOKEN:-}" ]; then
    GITLAB_TOKEN="$(printf 'protocol=https\nhost=gitlab.com\n\n' | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null | sed -n 's/^password=//p')"
fi
[ -n "$GITLAB_TOKEN" ] || { echo "❌ 没有 GitLab 令牌：设置 GITLAB_TOKEN 或存进钥匙串 menubarmijia-gitlab" >&2; exit 1; }
auth=(-H "PRIVATE-TOKEN: $GITLAB_TOKEN")

echo "▶ [1/5] 核对版本、标签与文件"
[ "$(tr -d '[:space:]' < VERSION)" = "$VERSION" ] || { echo "❌ VERSION 文件不是 $VERSION" >&2; exit 1; }
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null || { echo "❌ origin 上没有标签 $TAG，先推送标签" >&2; exit 1; }
manifest="$DIR/MenuBarMijia-$VERSION.update.json"
files=("MenuBarMijia-$VERSION.dmg" "MenuBarMijia-$VERSION.dmg.sha256")
count="$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))["files"]))' "$manifest")"
if [ "$count" = 3 ]; then
    files+=("MenuBarMijia-$VERSION-windows-x64-setup.exe" "MenuBarMijia-$VERSION-windows-x64-setup.exe.sha256"
            "MenuBarMijia-$VERSION-windows-x64.msi" "MenuBarMijia-$VERSION-windows-x64.msi.sha256")
fi
files+=("MenuBarMijia-$VERSION.update.json" "MenuBarMijia-$VERSION.update.json.ed25519.sig")
for name in "${files[@]}"; do
    [ -s "$DIR/$name" ] || { echo "❌ 缺少 $DIR/$name" >&2; exit 1; }
done

echo "▶ [2/5] 清单对上安装包，签名对上 App 内置公钥"
python3 scripts/update_manifest.py check-fields "$VERSION" "$DIR"
swift scripts/sign_update.swift --verify "$manifest" "$manifest.ed25519.sig"

echo "▶ [3/5] 确认 $TAG 还没发布"
status="$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$API/releases/$TAG")"
[ "$status" = 404 ] || { echo "❌ $TAG 已存在（HTTP $status）；已发布的版本不覆盖，请升版本号" >&2; exit 1; }

echo "▶ [4/5] 上传 ${#files[@]} 个文件到通用包仓库，并用匿名下载核对字节"
links="[]"
for name in "${files[@]}"; do
    url="$API/packages/generic/MenuBarMijia/$VERSION/$name"
    curl -sS --fail-with-body "${auth[@]}" --upload-file "$DIR/$name" "$url" >/dev/null
    expected="$(shasum -a 256 "$DIR/$name" | cut -d' ' -f1)"
    actual="$(curl -sSL --fail "$url" | shasum -a 256 | cut -d' ' -f1)"
    [ "$expected" = "$actual" ] || { echo "❌ $name 匿名下载的字节不一致（$actual ≠ $expected）" >&2; exit 1; }
    links="$(python3 -c 'import json, sys; l = json.loads(sys.argv[1]); l.append({"name": sys.argv[2], "url": sys.argv[3], "link_type": "package" if sys.argv[2].endswith((".dmg", ".exe", ".msi")) else "other"}); print(json.dumps(l))' "$links" "$name" "$url")"
    echo "   ✓ $name"
done

echo "▶ [5/5] 创建 GitLab Release $TAG"
notes="$(awk -v v="## $VERSION" '$0 == v {on = 1; next} on && /^## / {exit} on' CHANGELOG.md)"
if [ "$count" = 1 ]; then
    notes="$notes"$'\n\n'"> 本版只发布 macOS 安装包。"
fi
payload="$(python3 -c 'import json, sys; print(json.dumps({"tag_name": sys.argv[1], "name": sys.argv[1], "description": sys.argv[2], "assets": {"links": json.loads(sys.argv[3])}}))' "$TAG" "$notes" "$links")"
curl -sS --fail-with-body "${auth[@]}" -H 'Content-Type: application/json' -X POST "$API/releases" -d "$payload" \
    | python3 -c 'import json, sys; print("✅ 已发布：" + json.load(sys.stdin)["_links"]["self"])'
