# ADR-0012：发布与应用内更新迁到 GitLab，允许只发 macOS

日期：2026-11-06　状态：已接受　补充：ADR-0005

## 背景

2026-11 GitHub 账号被停用，仓库、Releases 和 Pages 都无法访问。1.1.7 及更早的版本在代码里写死了 `api.github.com` 和 `github.com/.../releases/download/`，所以它们再也收不到更新，只能由用户手动安装一次新版本。仓库已迁到 `gitlab.com/jryang1997/MenuBarMijia`。

另外，现在没有可用的 Windows 构建机器，也没有云端 CI，Windows 的 EXE 和 MSI 暂时产不出来。

## 决定

1. **macOS 从 GitLab 检查和下载更新。**
   - 检查更新读 `https://gitlab.com/api/v4/projects/88577673/releases/permalink/latest`，用数字项目 ID，项目改名后照样能用。
   - 下载地址由 App 自己拼成通用包仓库的 URL：`/projects/88577673/packages/generic/MenuBarMijia/<version>/<file>`。release JSON 里的链接只用来判断附件在不在，App 不跟随这些链接下载，所以有人篡改 release 条目也没法把下载指到别处。
   - 发布页只打开 `https://gitlab.com/` 下的地址。
   - ADR-0005 的信任链不变：Ed25519 清单签名、按清单核对 SHA-256、钉住代码签名证书。私钥仍只留在维护者本机的钥匙串里。
2. **清单允许只含 macOS 一项。**
   - `files` 要么是 3 项（DMG、EXE、MSI），要么只有 DMG 一项，顺序固定。其他数量一律判为格式错误。
   - Windows 端的解析器不改，只认 3 项。它的检查更新目前仍然指向 GitHub，等有 Windows 机器能编译和测试时再迁。
3. **发布在维护者本机完成，不经过 CI。**
   1. `scripts/release.sh` 打包。
   2. `scripts/sign_update.swift --min-from <x.y.z> <DMG>` 离线签名。
   3. 推送标签。
   4. `scripts/publish_gitlab_release.sh <version>` 发布。这个脚本会先校验清单与签名，并拒绝覆盖已发布的版本；上传后再用匿名下载核对每个文件的字节。

## 代价

- 已安装的 1.1.7 及更早版本收不到自动更新，README 已写明需要手动安装一次。
- GitLab release 没有构建来源证明（attestation）和 SBOM。用户能依靠的只有应用内的签名校验。
- GitLab 的 release 链接不带文件大小，下载进度条显示为不确定。

## 相关

- ADR-0005，`docs/security/update-signing-key-rotation.md`
- `Sources/MijiaCore/UpdateChecker.swift`、`Sources/MijiaCore/UpdateManifest.swift`、`scripts/sign_update.swift`、`scripts/update_manifest.py`、`scripts/publish_gitlab_release.sh`
