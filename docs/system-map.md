# 系统地图：各部分怎么连、哪里会断

写给要改这套东西的人（和 AI 助手）。整理于 2026-11-20（客户端 0.1.714、系统层 0.2.22）。
更细的：[architecture.md](architecture.md)（Mermaid 图）、[state-machines.md](state-machines.md)（隐式状态机）。

## 1. 全景

```
                 家人的手机（Tailscale）                你的电脑（浏览器）
                   │ 对讲、清单、拍照问                   │ 网关后台 /admin
                   ▼                                      ▼
 ┌──────────────────────────── 网关 10.10.10.224（小麦网关，一个 Python 进程）────────────────────────────┐
 │  16689 WebSocket：对话（识别 → 大模型 → 合成）       16688 HTTP：/admin 后台、/kiosk/* 给设备用      │
 │  data/*.json：设备设置、清单、相册、健康、截图……     同机的服务：rack-screen 8088、SmartHub 8780、    │
 │  云：DeepSeek、Edge/千问 TTS、百炼……                 repo-manager 8765、Navidrome 等在 NAS 10.10.10.88 │
 └────────────▲───────────────────────────────────────────────────────────▲──────────────────────────────┘
              │ SSH 隧道（设备的 xiaozhi-hub-tunnel）：                     │ DeskHub（Mac 上的 App）
              │ 设备 127.0.0.1:16688/16689/16690 → 网关同样的端口            │ 主动推到 16688 /kiosk/deskhub
   ┌──────────┴──────────┐                              ┌──────────┴──────────┐
   │ 书房 10.10.10.115    │                              │ 客厅 10.10.10.192    │   两台一样的软件
   │ ThinkSmart View     │◄────── 米家云（设备直连）──────►│ ThinkSmart View     │
   └─────────────────────┘                              └─────────────────────┘
```

要点：
- **设备只认 127.0.0.1**：网关上设备用的接口（/kiosk/*）只接受「本机、没有 X-Forwarded-For」的请求，设备都是经隧道来的。隧道一断，设备就是「离线」。
- **升级包也走隧道**：16690 是网关上的一个静态文件服务（~/thinksmart-releases），设备的升级程序从那里拉。
- **网关是一个进程**：对话、后台、设备接口在同一个事件循环里；在里面做慢的同步操作（读大文件、解析 YAML、等网络）会卡住正在播的回答。慢操作都要 `_blocking`（线程池）。

## 2. 一台设备里面

```
 ┌────────────────────────── ThinkSmart View（postmarketOS 内核 + Ubuntu 用户态）──────────────────────────┐
 │                                                                                                          │
 │  thinksmart-kiosk（root 起，ubuntu 跑）                 thinksmart-client（小麦客户端，Python asyncio）    │
 │  ┌─ Weston（--debug，转屏幕方向）                        ┌─ 8090 网页 + /events（SSE 推状态给页面）       │
 │  └─ thinksmart-kiosk.py（WebKitGTK 全屏）◄── HTTP ────────┤  8091/8092 小代理：机柜、智能中枢的网页 → 网关  │
 │       │  截图 socket（$XDG_RUNTIME_DIR）◄── kiosk_shot ───┤  音频线程：麦克风 → AEC → 唤醒词 → 发给网关    │
 │       │  页面卡住 → kiosk_watch 让它重开                   │  17 个常驻循环（bg.forever：出错重来）         │
 │                                                          │  MCP 工具：网关的大模型调设备（闹钟、音量……）   │
 │  PipeWire / WirePlumber ◄──────── 声音进出 ───────────────┤  混音：回答、提示音、音乐、AirPlay、对讲       │
 │  thinksmart-mic（DMIC → PipeWire 虚拟麦克风）              │  → 静音/勿扰在这一处把关（audio.muted）         │
 │  tas5782m-preinit（开机放 0.3 秒静音，功放初始化）          └─ config.json、alarms.json、notes…（写前落盘） │
 │                                                                                                          │
 │  idle-blanker（背光）◄── 提示文件 thinksmart-screen.json ── 客户端（想要什么：亮、暗、熄、几分钟）          │
 │  vcnl4200 接近/光线守护 ─► thinksmart-proximity.json ─► idle-blanker（走近亮屏、关灯熄屏）               │
 │  screen-wake（碰屏幕亮）     gpu-watchdog（GPU 出错重启浏览器）                                           │
 │                                                                                                          │
 │  thinksmart-update（root）：客户端 POST /api/update/install → 写请求文件 → path 单元起它 → 装好跑自检       │
 │      自检不过（比如 /api/health 报错）→ 退回上一个版本（0.1.709 就是这样退回去的）                        │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 3. 说一句话走过的路

```
 你：「小麦小麦，明天下雨吗」
   │
   ▼ 音频线程（20 ms 一块）
 唤醒词模型 kws ──命中──► 复核：/kiosk/asr（网关识别这 3.5 秒，200 ms 左右）
   │                     就近唤醒：/kiosk/wake/claim（几台都听到时只有最近的那台回答）
   │                     认人打招呼：/kiosk/wake/greet（声纹）
   ▼
 连 WebSocket 16689 → listen start → 声音（Opus）一路发过去
   │                                          网关：VAD 说完 → 识别出字 → 大模型（DeepSeek）
   │                                                         ↘ 要查天气、设闹钟：调工具（插件或设备的 MCP）
   ▼                                          ← tts start / 语音（Opus）/ tts stop
 播放 → 混音（静音、勿扰把关）→ PipeWire → 喇叭
   │
   └─ 页面：SSE 推 state（listening → thinking → speaking → idle）、字幕、卡片
 说完：要不要接着听（follow_up），deadline 到了就回 idle；一段时间不说网关断开 WebSocket
```

## 4. 哪里会断、断了会怎样

| 断在哪 | 会怎样 | 谁兜底 |
|---|---|---|
| SSH 隧道 / 网关挂了 | 叫醒后连不上 → 「离线命令」（几个本地口令）；首页天气、清单停在上一次；后台显示离线 | 隧道是 systemd 服务，自己重连；客户端每 10 秒再试 |
| 断电重启 | 没有电池时钟：头一分钟系统时间是旧的 | 闹钟、睡眠时段等对上时间才判断；计时用单调时钟；文件写前落盘 |
| PipeWire 死了 | 喇叭不回调 | 客户端 5 秒没回调就退出，systemd 重启它 |
| 网页卡住 / GPU 出错 | 屏幕不动或黑 | kiosk_watch 重开页面；gpu-watchdog 重启浏览器 |
| 常驻循环抛异常 | 那个功能（计时、睡眠时段、天气……）停 | bg.forever 记日志、1 / 5 / 30 / 60 秒后重来；健康上报里能看到次数，侧栏心跳变黄点 |
| 新版本有问题 | 自检不过 | 升级程序自动退回上一版 |
| 米家会话过期 | 米家设备读不到 | 页面提示重新扫码 |
| rack-screen 的登录过期 | 机柜页 401 | 网关代理重新登录一次再要 |
| 两个请求同时改网关数据 | 以前会撞临时文件、丢修改 | atomic_file（每次不同的临时文件、落盘）+ 读改写整段加锁 |

## 5. 状态都存在哪

- **设备**：`~/.config/thinksmart-client/config.json`（所有设置：音量、睡眠时段、播报、静音勿扰、工作台……），
  `~/.local/share/thinksmart-client/`（闹钟、留言、卡片、相册缓存、米家会话、形象库），
  `/etc/thinksmart-kiosk.conf`（屏幕方向、网址），WirePlumber 的状态（音量本身）。
- **网关**：`data/.config.yaml`（后台改的配置）、`data/*.json`（设备设置、清单、相册、媒体、声纹、健康……）、
  `data/screens/`（每台最新一张截图）、`tmp/server.log`。
- **页面自己**：localStorage 只放无所谓丢的（上次看的页签、开机先铺的主题色）。

## 6. 排查先看什么

```bash
curl -s 127.0.0.1:8090/api/health          # 设备：版本、音频、连没连网关
curl -s 127.0.0.1:8090/api/page-state      # 屏幕上是哪一页、最近 20 次
journalctl -u thinksmart-client -n 100      # 设备日志
```
网关后台「设备」页：每台的健康、最近的错误、「截一张」看屏幕。
