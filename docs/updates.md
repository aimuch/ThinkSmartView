# 更新：我们自己的版本，跟进原作者

设备和网关都**不直接装原作者的更新**：原作者的改动先合并进本仓库，再由我们发布。

## 1. 合并原作者的新提交

```bash
scripts/sync-upstream.sh            # 全部
scripts/sync-upstream.sh server     # 只看一个目录
```

需要工作区干净。脚本按 [`UPSTREAM`](../UPSTREAM) 拉取每个上游，把记录的提交到上游最新之间的改动三方合并进对应目录（`client/`、`server/`、`system/`、`reference/menubar-mijia/`），我们自己的改动会保留。有冲突就停下，由你解决后提交；没冲突就暂存好，`git diff --cached` 看过后提交。之后跑测试：

```bash
cd client && DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run --no-project --with numpy --with soxr --with websockets --with aiohttp --with pytest --with pytest-asyncio python -m pytest -q tests
cd system && uv run --no-project --with pytest python -m pytest -q update kiosk
```

网关的测试要在服务器上用网关的 Python 跑（把 `server/` 同步到 `~/hub-test`，pytest 装在单独的 `~/hub-test-deps`，不进生产环境）：

```bash
rsync -a --exclude .venv --exclude data/ --exclude models --exclude tmp/ server/ andy@10.10.10.224:hub-test/
ssh andy@10.10.10.224 'cd ~/hub-test/main/xiaozhi-server && PYTHONPATH=$HOME/hub-test-deps ~/xiaozhi-hub-server/main/xiaozhi-server/.venv/bin/python -m pytest -q tests --ignore=tests/core/utils/test_current_time.py'
```

已知：`test_missing_packages_real_providers`（测试环境缺 psutil）和 `system/update/test_system_layer.py::test_make_release_carries_the_system_layer`（它把 system/ 当独立仓库克隆）在原作者的代码上也失败。

## 2. 发布客户端（小麦）和系统层（小麦系统）

设备在「设置 → 软件更新」里看到，点一下装好；装完自检，不过就自动退回。

- 客户端版本号 = **上游补丁号 × 100 + 本仓库序号**：上游 0.1.6 → 0.1.601、0.1.602……；合并上游 0.1.7 后从 0.1.701 开始。设备的更新程序只认 `x.y.z`，这样我们的版本永远比对应的上游大。
- 系统层版本在 `system/VERSION`；系统层文件变了就加一。`make-release.py` 会在本仓库打本地标签 `system-v<版本>`，防止同一个版本号对应两份文件。
- 改版本号、提交，然后：

```bash
scripts/publish-release.sh --note "客户端这次改了什么" --system-note "系统层改了什么"
```

依赖环境按 `client/requirements.lock` 的哈希缓存在 `~/.cache/thinksmart-release/envs`。lock 变了要在 arm64 容器里重新构建，这台 Mac 需要装 Docker。

## 3. 部署网关（小麦网关）

```bash
scripts/deploy-server.sh
```

每次部署在服务器 `~/repos/xiaozhi-hub-server.git` 的 `main` 上加一个提交：内容是本仓库已提交的 `server/`，说明里列出上次部署以来的改动（并记下来源提交）。服务器上网关的上游就是它，所以脚本会接着跑网关自己的 `update.sh`：依赖变了就补装，然后重启，起不来自动退回。推送永远是快进，服务器上的历史只增不改；本仓库里目录改名也不影响它。只推不装用 `--push`，之后在后台点「更新网关」也能装。
