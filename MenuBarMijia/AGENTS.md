## Agent skills

### Issue tracker

GitLab (`https://gitlab.com/jryang1997/MenuBarMijia`, git remote `origin`) via `python3 scripts/forge.py`; the GitHub account is suspended, so never use `gh`. The NAS Forgejo (remote `nas`) is only a backup. Migrated issues were renumbered and cite old GitHub numbers. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`GLOSSARY.md` + `docs/adr/` at the repo root). See `docs/agents/domain.md`.

### Coding standards

Read `CODING_STANDARDS.md` when reviewing diffs (`/code-review`) or modifying synthesis, optimistic locking, hotkeys, or grid layout.

### Build & verification

Run `bash scripts/check.sh` for secret scan, mechanical lints, and `swift test`, snapshots included. The pre-commit hook and GitLab CI run the same gate. `--skip-snapshots` is only for a Mac other than the maintainer's, whose renderer is not the reference. Run `bash scripts/install_local_app.sh` to build, verify, and install into the bundle the user launches (it keeps the previous version for rollback); `bash scripts/build_app.sh` only packages the bundle inside the worktree. Run `swift scripts/generate_app_icon.swift` when modifying `Resources/AppIcon/AppIcon-*.svg` to regenerate macOS `.icns`, `Assets.car`, README logos, and Windows Tauri icons.

快照基线以维护者这台 Mac 的渲染为准；它同时是 GitLab CI 的 runner，所以本地与 CI 比对的是同一份基线。改到 `Sources/MenuBarMijiaApp/` 而界面有变化时，`bash scripts/record_snapshots.sh` 重录，审阅变化的 PNG 后随改动一起提交（`--dry-run` 只列出会变的图）。改动没碰界面、快照却突然失败时，先在最近一次通过的提交上重跑同一组快照：那边也失败，说明是渲染环境变了。差异时有时无（如系统滚动条样式、并行测试残留的状态）就修测试工具，不要重录基线；系统升级后差异稳定复现，逐张审阅后再重录。`check.sh` 会跑完 Swift 与 Windows 两端再汇总失败。

Windows 上没有 Swift 工具链：本机跑 `bash scripts/check.sh --lint-only`，Swift 测试交给 CI 或局域网 Mac（`bash scripts/remote-swift-test.sh --host <user>@<mac>`，一次配置见脚本头）。

### Tooling

本仓库的 Swift 与 Rust 没有语言服务器：引用与定义检索用 `grep` 或 `xd://ast_grep`，不要反复尝试 `xd://lsp`。

CI 失败先看 `bash scripts/ci_errors.sh <pipeline-id|log-file>`（只打失败 job、错误与失败用例）；盘点未合并分支和工作树用 `bash scripts/pending-work.sh`；挑票用 `bash scripts/issue-frontier.sh`（一次列出所有 `ready-for-agent` 票能不能做：blocker、assignee、`claimed:*` 标签、关联的 open PR 和分支；`--doable` 只输出可做编号）。

### Commit & PR policy

本约定仅适用于此项目。在用户已授权提交的范围内，按改动风险选择直接 push 或 PR，不要求每次代码修改都开 PR。

- 文案、文档、图标、样式微调，以及范围明确的局部交互修复，完成相应检查后可直接 push 到 `main`。交互修复应有针对性验证和用户实机确认；按影响范围判断风险，不按代码行数判断。
- 登录、凭据与钥匙串、安全权限、设备控制、撤销、定时任务、数据迁移、自动更新、签名、打包发布、跨平台逻辑，以及牵涉多个模块的改动，走 PR，检查通过后再合并。
- 检查失败原因尚未确认时，走 PR 排查，解决后再合并。已复现并确认与本轮无关的既有失败，需记录证据及例外原因；跳过检查不是默认流程。
- 一个完整问题对应一次提交或一个 PR；同一问题的后续调整继续使用原 PR。
- 多个代理并行时按顺序合并：上一个合并进 `main` 的流水线变绿后，再合下一个。连续合并时，后一次 push 会取消前一次的流水线，`main` 变红后就查不出是哪次合并引入的。
- 直接 push 后，有 runner 时确认云端检查的最终结果；交付时区分“已推送”与“检查已通过”。直接 push 不等于发布新版本。
- 合并请求（MR，即本文的 PR）用 `python3 scripts/forge.py pr create` 开，`pr checks <n>` 看流水线。`.gitlab-ci.yml` 在维护者 Mac 上的项目 runner（shell executor，标签 `macos`）跑 `bash scripts/check.sh`，含快照比对、Windows 前端测试与 `vite build` 打包；Rust 由 `windows` 作业在 GitLab.com 托管 Windows runner（标签 `saas-windows-medium-amd64`）上跑 `npm run build` 与 `cargo test`，仅在 `windows/src-tauri/` 或 `.gitlab-ci.yml` 变更时触发。首次查询为空时按退避重试。`.github/workflows`、Codex 审查与 `pr-wait`/babysit 一类依赖 GitHub 的技能都已不可用。

### Worktrees

- `git worktree list` 里登记给其他会话的分支（如 Codex 的 `wt/dev` 工作树）归该代理所有：审计只读，不改它的文件。
- 接管前先确认对方已安静：无新提交且 `git status` 干净。对方仍在迭代时，在自己的分支副本上完成改动再推回。
- 对方提交在你审计途中落地，就按最新提交重读后再下结论，不基于旧快照。

## Cursor Cloud specific instructions

Linux 云代理的构建、检查与端口约定见 `docs/agents/cursor-cloud.md`（在 Cursor Cloud 或 Linux 容器里工作时读）。

## Cursor skills

Before coding, read `.cursor/skills/poteto-mode/SKILL.md` and follow its playbook. Then use any fitting `tstack-`, `mp-`, or `principle-` skill. Sources and refresh steps are in `.cursor/skills/README.md`.

