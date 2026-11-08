# ThinkSmart View 语音终端

把联想 ThinkSmart View（CD-18781Y）刷成本地语音助手，网关部署在局域网服务器上。本仓库由几个上游项目合并而成，在它们之上加了米家直连、内容卡片、屏幕旋转、大模型费用统计，以及自建的更新通道。

| 目录 | 来源 | 说明 |
| --- | --- | --- |
| `thinksmart-ubuntu-image/` | https://github.com/justhalfbit/thinksmart-ubuntu-image | 刷机镜像、系统层、在线更新程序 |
| `xiaozhi-thinksmart-client/` | https://github.com/justhalfbit/xiaozhi-thinksmart-client | 设备上的语音客户端（本仓库改过） |
| `xiaozhi-hub-server/` | https://github.com/justhalfbit/xiaozhi-hub-server | 网关（本仓库改过） |
| `MenuBarMijia/` | https://gitlab.com/jryang1997/MenuBarMijia | 参考代码（MIT）：米家扫码登录、签名、MIoT 规格的原实现 |
| `remote-hub/` | 本仓库 | 设备连远程网关的隧道、服务器上的发布服务 |
| `scripts/` | 本仓库 | 同步上游、发布客户端、部署网关 |

每个目录同步到上游的哪个提交记在 [`UPSTREAM`](UPSTREAM)。刷机步骤见 [thinksmart-ubuntu-image/README.md](thinksmart-ubuntu-image/README.md)。

## 部署结构

```
ThinkSmart View (10.10.10.192)                       服务器 Dev (10.10.10.224)
  thinksmart-client 0.1.6xx                            xiaozhi-hub（systemd 用户服务，跟随本仓库）
    ws://127.0.0.1:16689   ─┐                            0.0.0.0:16688  管理后台 / OTA / /kiosk/*
    http://127.0.0.1:16688  ─┤ xiaozhi-hub-tunnel         0.0.0.0:16689  语音 WebSocket
    更新源 127.0.0.1:16690  ─┘ ssh -L ────────────────►  127.0.0.1:16688 / 16689
                                                         127.0.0.1:16690  thinksmart-releases（本仓库发布的客户端）
  设备自带的 xiaozhi-hub：已停用                          ~/repos/xiaozhi-hub-server.git（本仓库拆出的网关）
  米家：设备直接连小米云（扫码登录）
```

- **网关**：`andy@10.10.10.224:~/xiaozhi-hub-server`，管理后台 http://10.10.10.224:16688/admin 。
- **为什么走 SSH 隧道**：网关的 `/kiosk/*` 接口（切换模型、聊天记录、提醒语音、费用）只接受网关本机的请求，局域网直连返回 403。走隧道后设备在服务器上就是 127.0.0.1，这些不认证的接口也不会暴露给局域网。
- **隧道密钥**：设备上的 `~/.ssh/hub_tunnel_ed25519`，服务器 `authorized_keys` 里限制为 `restrict,port-forwarding,permitopen="127.0.0.1:16688",permitopen="127.0.0.1:16689",permitopen="127.0.0.1:16690",command="/bin/false"`：只能转发这三个端口，拿不到 shell。
- **重新配置设备**（重刷或换 IP 后）：`DEVICE=<设备IP> remote-hub/setup-device.sh`。会装隧道、停掉设备自带的网关，并把更新源改到我们的发布服务。需要本机能免密登录 `ubuntu@<设备IP>`（`~/.ssh/thinksmart_ed25519`）和 `andy@10.10.10.224`（`~/.ssh/id_ed25519_btrouter`）。

## 本仓库加的功能

| 功能 | 在哪里 | 怎么用 |
| --- | --- | --- |
| 米家直连 | `xiaozhi-thinksmart-client/thinksmart_client/mijia/` | 屏幕顶栏 🏠「家居」→ 选地区 → 用米家 App 扫码。按房间显示设备状态，点一下开关，点卡片调亮度、色温、温度、模式、风速、窗帘。直接说「打开书房的灯」「客厅温度多少」「哪些灯开着」也行；插座断电、加热设备开机这类会先问一句。不存密码和 passToken，令牌文件权限 0600，过期了重新扫码。 |
| 内容卡片 | `self.screen.show_card`（`mcp.py`、`web/v2/home.js`） | 问菜谱、步骤、清单、对比、流程时，模型把内容放到屏幕卡片上（Markdown、表格、Mermaid 流程图、图片），嘴上只说一句 |
| 屏幕旋转 | `thinksmart_client/rotation.py` | 设置 →「屏幕方向」：横屏、竖屏和它们的倒置，或「跟随摆放方向」（加速度计，放稳 3 秒后切换，页面会重新载入一次） |
| 大模型费用 | `xiaozhi-hub-server/main/xiaozhi-server/core/billing/` | 后台「费用」页：今日、本月、累计、近 30 天、按模型/设备/用途；单价按模型和时段（DeepSeek 高峰价和空闲价已内置，2026-11-08 核对），法定节假日和调休在「日历」里填；额度可以设每天、每月、每台设备，超了拒绝或改用便宜模型。设备设置页也能看今日/本月/累计。 |

## 更新：我们自己的版本，跟进原作者

设备和网关都**不再直接装原作者的更新**：原作者的改动先合并进本仓库，再由我们发布。

**1. 合并原作者的新提交**

```bash
scripts/sync-upstream.sh
```

需要工作区干净。脚本拉取每个上游，把 `UPSTREAM` 里记录的提交到上游最新之间的改动三方合并进对应目录，我们自己的改动会保留。有冲突就停下，由你解决后提交；没冲突就暂存好，`git diff --cached` 看过后提交。之后跑两边的测试：

```bash
cd xiaozhi-thinksmart-client && DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run --no-project --with numpy --with soxr --with websockets --with aiohttp --with pytest --with pytest-asyncio python -m pytest -q tests
```

网关的测试要在服务器上用网关的 Python 跑，具体命令见 `xiaozhi-hub-server` 的 Makefile。

**2. 发布客户端**（设备在「设置 → 软件更新」里看到，点一下装好；装完自检，不过就自动退回）

- 版本号 = **上游补丁号 × 100 + 本仓库序号**：上游 0.1.6 → 0.1.601、0.1.602……；合并上游 0.1.7 后从 0.1.701 开始。设备的更新程序只认 `x.y.z`，用这种写法我们的版本永远比对应的上游大。
- 改 `xiaozhi-thinksmart-client/thinksmart_client/__init__.py` 的 `__version__`，提交，然后：

```bash
scripts/publish-release.sh --note "这次改了什么"
```

依赖环境按 `requirements.lock` 的哈希缓存在 `~/.cache/thinksmart-release/envs`。lock 变了要在 arm64 容器里重新构建，这台 Mac 需要装 Docker。

**3. 部署网关**

```bash
scripts/deploy-hub.sh
```

把 `xiaozhi-hub-server/` 用 `git subtree split` 拆成独立历史，推到服务器的 `~/repos/xiaozhi-hub-server.git`。服务器上网关的上游就是它，所以脚本会接着跑网关自己的 `update.sh`：依赖变了就补装，然后重启，起不来自动退回。只推不装用 `--push`，之后在后台点「更新网关」也能装。

## 排查

```bash
ssh ubuntu@10.10.10.192 'systemctl status xiaozhi-hub-tunnel thinksmart-client --no-pager | head -20; journalctl -u thinksmart-client -n 50 --no-pager'
ssh andy@10.10.10.224 'tail -50 ~/xiaozhi-hub-server/main/xiaozhi-server/tmp/server.log; systemctl --user status xiaozhi-hub thinksmart-releases --no-pager | head'
```

## 刷机记录（2026-11-07）

- 原厂系统，走路线 A。整盘备份在 `flash/stock-full-emmc.bin`（7818182656 字节，SHA256 在同目录 `.sha256` 文件里），已被 `.gitignore` 排除。**请另存一份到移动硬盘，不要上传任何地方。**
- 从原厂 Android 进 EDL 可以用 `adb reboot edl`；刷完后只能按键进（拔电源 → 接 USB → 按住音量+和音量− → 插电源）。
- 这台 Mac 的直连 USB-C 口识别不到设备，EDL 要接在 CalDigit 扩展坞上。
- bootstrap（postmarketOS）里 `/sys/class/udc` 为空，USB 网络不可用，A6 改分区表是在设备触摸屏上登录 `pmos` 执行的。
- 刷机工具 edl 装在 `edl-src/` 和 `.edl-venv/`（已排除），重装：

  ```bash
  git clone https://github.com/bkerler/edl.git edl-src && (cd edl-src && git submodule update --init --recursive)
  python3.12 -m venv .edl-venv && .edl-venv/bin/pip install ./edl-src
  ```

## 第三方

- 米家扫码登录、请求签名、MIoT 规格处理参考 [MenuBarMijia](https://gitlab.com/jryang1997/MenuBarMijia)（MIT，jryang1997）改写；客户端测试用了它的 `Tests/Fixtures/miot_golden.json`。
- 二维码：qrcode.js（Kazuhiko Arase，MIT）；流程图：Mermaid 10.9.1（MIT），都在 `xiaozhi-thinksmart-client/web/v2/vendor/`。
