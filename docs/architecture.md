# 系统架构

小麦由三部分组成：**设备**（ThinkSmart View，跑客户端和系统层）、**网关**（局域网服务器上的小麦网关），以及网关和设备各自调用的**云服务**。设备只负责「听、放、显示、本机的事」，**听懂和回答都在网关**：语音识别、调大模型（比如 DeepSeek）、语音合成、聊天记录和记忆、查天气这类插件，都在网关上跑。设备自己不调大模型，也不保存任何大模型的 Key。

下面的图用 Mermaid 写，GitHub 上直接显示成图。

## 1. 总览

```mermaid
flowchart TB
  user(("家人"))

  subgraph dev["设备 10.10.10.192"]
    shell["展示屏外壳<br/>Weston + 全屏 WebKit"]
    ui["对话界面 web/v2<br/>127.0.0.1:8090"]
    subgraph client["小麦客户端"]
      audio["声音<br/>麦克风、扬声器<br/>回声消除"]
      kws["唤醒词<br/>sherpa-onnx"]
      core["对话状态机<br/>app.py"]
      tools["设备工具 mcp.py<br/>音量、闹钟、卡片…"]
      mijia["米家直连<br/>mijia/"]
    end
    sys["系统层<br/>麦克风、熄屏、在线更新"]
    tunnel["SSH 隧道<br/>只转发三个端口"]
  end

  subgraph hub["小麦网关 10.10.10.224"]
    ws["语音 WebSocket<br/>:16689"]
    http["HTTP :16688<br/>管理后台、/kiosk/*"]
    rel["发布服务<br/>:16690"]
    subgraph pipe["对话流水线"]
      vad["VAD<br/>Silero"] --> asr["语音识别<br/>X-ASR 本地"] --> llm["大模型路由<br/>function_call"] --> tts["语音合成"]
    end
    plugins["网关插件<br/>天气、搜索、新闻…"]
    db[("chat.db 记录和记忆<br/>usage.db 费用")]
  end

  subgraph cloud["云服务"]
    deepseek["DeepSeek 等<br/>大模型 API"]
    ttsc["Edge TTS /<br/>千问 TTS"]
    meteo["Open-Meteo<br/>搜索、新闻"]
    mi["小米云"]
  end

  user -- 说话 --> audio
  audio -- 播放 --> user
  user -- 触摸 --> shell --> ui
  ui <-- "事件流 + 按钮接口" --> core
  audio --> kws --> core
  core --> tools --> mijia
  core -- "语音 ws + /kiosk/*" --> tunnel
  sys -- 更新源 --> tunnel
  tunnel ==> ws & http & rel
  ws --> pipe
  llm <--> db
  llm --> plugins --> meteo
  llm -- HTTPS --> deepseek
  tts -- HTTPS --> ttsc
  mijia -- HTTPS 直连 --> mi
```

| 放在哪 | 做什么 | 不做什么 |
| --- | --- | --- |
| 设备 · 客户端 | 唤醒词、回声消除、录音上传、播放回复、对话界面、闹钟提醒（本机响）、米家（直连小米云）、蓝牙音箱、本机设备工具 | 不识别整句话，不调大模型，不存大模型 Key |
| 设备 · 系统层 | 全屏外壳、屏幕键盘、熄屏和接近传感器、麦克风服务、音频配置、在线更新和自动退回 | — |
| 网关 | 断句（VAD）、语音识别、大模型（按称呼切换模型）、插件、语音合成、聊天记录、记忆总结、费用统计、管理后台、唤醒二次确认、给设备发版本 | 不碰米家账号（米家令牌只在设备上） |
| 云 | 大模型、语音合成（Edge TTS 是微软的免费接口，千问 TTS 要百炼 Key）、天气、搜索 | — |

**为什么用 SSH 隧道**：网关的 `/kiosk/*` 接口不认证，只接受网关本机的请求。走隧道后，设备在网关看来就是 127.0.0.1，这些接口也不会暴露给局域网。隧道密钥只能转发这三个端口，拿不到 shell（见[部署](deploy.md)）。

### 端口

| 端口 | 在哪 | 用途 |
| --- | --- | --- |
| 8090 | 设备 127.0.0.1 | 对话界面、事件流、按钮接口（[客户端接口](../client/README.md#接口只监听本机)） |
| 16689 | 网关 | 语音 WebSocket `/xiaozhi/v1/`（小智协议） |
| 16688 | 网关 | 管理后台 `/admin`、设备接口 `/kiosk/*`、OTA、`/mcp/vision/explain` |
| 16690 | 网关 127.0.0.1 | 客户端和系统层的发布包 |

## 2. 一次对话

以「小麦小麦，青浦明天下雨吗」为例。DeepSeek 由**网关**调用，设备只收到识别出的文字、回复文字和合成好的声音。

```mermaid
sequenceDiagram
  autonumber
  actor U as 家人
  participant C as 设备客户端
  participant H as 小麦网关
  participant L as DeepSeek
  participant T as 语音合成
  participant W as Open-Meteo

  U->>C: 「小麦小麦」
  Note over C: 本地唤醒词命中
  C->>H: POST /kiosk/asr 最近 3 秒（唤醒二次确认）
  H-->>C: 识别文字，按拼音核对是唤醒词
  C->>U: 提示音，界面变「在听」
  C->>H: WebSocket 连接（没连着时）+ hello（声明支持 MCP）
  H->>C: MCP tools/list
  C-->>H: 设备工具列表（音量、闹钟、米家、卡片…）
  U->>C: 「青浦明天下雨吗」
  C->>H: Opus 音频帧（实时上传）
  Note over H: VAD 判断说完 → X-ASR 识别
  H-->>C: stt「青浦明天下雨吗」（屏幕显示）
  Note over C: 麦克风暂停上传，只听唤醒词（用来打断）
  H->>L: 系统提示（时间、记忆）+ 最近对话 + 这句话 + 工具定义
  L-->>H: 调用 get_weather(location=青浦区)
  H->>W: 查地名、预报、空气质量
  W-->>H: 数据
  H->>L: 工具结果
  L-->>H: 流式回复文字
  loop 每一句
    H->>T: 合成这一句
    T-->>H: 音频
    H-->>C: tts sentence_start（文字）+ Opus 音频
    C->>U: 播放
  end
  H-->>C: tts stop
  Note over H: 这一轮写进 data/chat.db
  Note over C: 10 秒追问窗口：麦克风重新上传，<br/>接着说不用再喊唤醒词
  Note over C,H: 约 2 分钟没人说话，网关关闭连接；<br/>再次唤醒时重新连接，并从 chat.db 载入最近的对话
```

**打断**：回复期间麦克风不上传，旁人说话、咳嗽都不会打断。只有喊唤醒词或点屏幕上的按钮才能打断，此时客户端告诉网关「这句话播了多少毫秒」，网关只把听到的部分留在上下文里。

**文字提问**：设置页或 `POST /api/text` 发来的文字，跳过语音识别，后面的流程一样。

## 3. 客户端状态

```mermaid
stateDiagram-v2
  [*] --> idle
  idle --> connecting: 唤醒词（二次确认通过）/ 点按钮 / 按住说话
  connecting --> listening: 连上网关，响提示音
  listening --> thinking: 网关返回识别文字（stt）
  thinking --> speaking: 第一句回复开始播放
  speaking --> speaking: 下一句
  speaking --> listening: 播完，进入 10 秒追问窗口
  listening --> idle: 追问窗口内没再说话
  thinking --> listening: 唤醒词或按钮打断（这句作废）
  speaking --> listening: 唤醒词或按钮打断（告诉网关播了多少）
  listening --> idle: 结束按钮 / 「再见」
  speaking --> idle: 结束按钮
```

- `connecting`、`listening`、`thinking`、`speaking` 期间屏幕不熄。
- 「结束」会关闭网关连接、清掉网关这边的上下文（聊天记录还在）。
- 闹钟、倒计时、提醒到点时，不管在哪个状态都会停下当前对话开始响铃；喊唤醒词、点「停止」或「再睡」停下，没人管 10 分钟后自己停（提醒是 3 分钟）。

## 4. 工具调用：网关插件和设备工具

大模型能用两类工具。网关自己的插件在网关上执行，设备工具通过 MCP 交给设备执行。

```mermaid
flowchart TB
  llm["大模型返回 function_call"] --> which{"工具名"}
  which -- "get_weather、web_search、<br/>get_news、handle_exit_intent…" --> plugin["网关插件<br/>plugins_func/functions/"]
  which -- "self_* <br/>（设备通过 MCP 上报的工具）" --> mcp["网关把调用发给设备<br/>（30 秒超时）"]
  mcp --> dev["设备 mcp.py 执行"]
  dev --> local["本机：音量、亮度、睡眠时段、<br/>闹钟提醒、唤醒词、内容卡片"]
  dev --> home["米家：self.home.list_devices / get_state / control<br/>→ 设备直连小米云"]
  plugin --> r1{"返回什么"}
  dev --> r1
  r1 -- "RESPONSE：现成的一句话<br/>（如「已打开书房台灯」）" --> say["网关直接念，<br/>不再过一遍大模型（更快）"]
  r1 -- "REQLLM / 普通结果：数据<br/>（如天气预报、设备状态）" --> again["交回大模型组织语言再念"]
  r1 -- "need_confirm：要先确认<br/>（插座断电、加热设备开机）" --> ask["大模型先问用户，<br/>同意后带 confirm 再调一次"]
```

内容卡片（`self.screen.show_card`）：菜谱、步骤、清单、表格这类适合看的内容，大模型把标题、Markdown 和图片链接交给设备，屏幕上的对话里多一条图文消息，嘴上只说一句。图片只能是大模型知道确实存在的 https 链接（DeepSeek 本身不能生成图片）。

## 5. 聊天记录和记忆

```mermaid
flowchart TB
  turn["每轮对话结束 /<br/>回复被打断 / 连接关闭"] -- 同步新消息 --> db[("data/chat.db<br/>messages：按设备、按模型")]
  db -- "新连接：载入最近 30 条" --> ctx["这个连接的对话上下文"]
  ctx -- "每次发给大模型：最近 30 条<br/>且不超过约 8000 token" --> llm["大模型"]
  db -- "被窗口截掉的、攒够 6 条" --> sum["后台总结（同一个模型）"]
  sum --> mem[("device_memory<br/>每台设备一段，≤500 字")]
  mem -- "放进系统提示的 &lt;memory&gt;" --> llm
  admin["管理后台「记忆」"] -- 查看、手改、清空 --> mem
  screen["设备没连着时<br/>GET /kiosk/chat/history"] -- 每个模型最近 50 条 --> db
  clear["设置页 / 说「清除聊天记录」<br/>POST /kiosk/chat/clear"] -- 删记录和记忆 --> db
```

- 记忆是网关自己总结的（上游的 mem0 等记忆模块没用，配置里 `Memory: nomem`），记的是关于用户的事，比如「家里有三岁宝宝」「住在青浦」，这台设备所有普通 API 模型共用。
- Claude Code、Grok Bot 这类自己带会话的模型，上下文在它们自己那边，网关只存记录给屏幕显示。

## 6. 米家扫码登录和控制

米家账号只在设备上，网关不经手。

```mermaid
sequenceDiagram
  autonumber
  actor U as 家人
  participant S as 设备屏幕（家居页）
  participant C as 设备客户端 mijia/
  participant A as account.xiaomi.com
  participant P as 米家 App（手机）
  participant M as api.io.mi.com

  U->>S: 点「家居」，选地区
  S->>C: POST /api/mijia/login/start
  C->>A: longPolling/loginUrl
  A-->>C: loginUrl + 长轮询地址
  C-->>S: 本地画二维码
  U->>P: 扫码、确认
  C->>A: 长轮询（每次 15 秒）
  A-->>C: userId、ssecurity、location
  C->>A: 访问 location（不跟随跳转）
  A-->>C: Set-Cookie serviceToken
  Note over C: 存 session.json（0600，不存密码和 passToken）
  C->>M: 签名请求：家庭、设备列表、MIoT 属性
  M-->>C: 设备和状态
  C-->>S: 按房间显示卡片（家居页开着时每 6 秒刷新）
  U->>S: 点灯 / 或说「打开书房的灯」
  C->>M: miotspec/prop/set
  C->>M: 800 毫秒后补查一次
```

## 7. 发布和更新

```mermaid
flowchart TB
  subgraph mac["这台 Mac"]
    code["改代码、提交"]
    pub["scripts/publish-release.sh"]
    dep["scripts/deploy-server.sh"]
    code --> pub & dep
  end
  subgraph gw["网关"]
    rel["发布服务<br/>~/thinksmart-releases :16690"]
    bare["~/repos/xiaozhi-hub-server.git"]
    upd["update.sh<br/>补装依赖、重启<br/>起不来自动退回"]
    bare --> upd
  end
  subgraph device["设备"]
    chk["每天检查一次<br/>（只检查不装）"]
    btn["设置 → 软件更新<br/>用户点「更新」"]
    ins["thinksmart-update install<br/>先系统层、后客户端"]
    self{"自检 90 秒"}
    done["切换完成<br/>保留上一版"]
    back["自动退回上一版"]
    chk --> btn --> ins --> self
    self -- 通过 --> done
    self -- 不通过 / 断电 --> back
  end
  pub -- "客户端 + 系统层<br/>清单带 sha256" --> rel
  dep -- 快进推送一个快照提交 --> bare
  rel -- 经隧道 --> chk
```

版本号规则、跟进上游的方法见[更新](updates.md)。

## 8. 设备上的进程

```mermaid
flowchart TB
  subgraph systemd["systemd"]
    kiosk["thinksmart-kiosk<br/>Weston + 全屏 WebKit"]
    clientsvc["thinksmart-client<br/>小麦客户端（Python）"]
    mic["thinksmart-mic<br/>麦克风（codec + 数字麦）"]
    pw["PipeWire / WirePlumber"]
    blank["idle-blanker、screen-wake<br/>熄屏、接近传感器亮屏"]
    tun["xiaozhi-hub-tunnel<br/>SSH 隧道"]
    updt["thinksmart-update-*<br/>检查、安装、开机收尾"]
    gpu["thinksmart-gpu-watchdog<br/>GPU 出错时重启浏览器"]
    bluez["bluetooth（BlueZ）"]
  end
  mic --> pw --> clientsvc
  clientsvc -- 熄屏提示 --> blank
  kiosk -- "加载 127.0.0.1:8090" --> clientsvc
  clientsvc --> tun
  updt -- 读状态 --> clientsvc
  bluez -- 手机音源 --> pw
```

## 9. 设备自己发起的请求

天气播报、定时任务、拍照问答都是设备自己发一句文字给网关（和设置页的文字提问同一条路），文字以【标签】开头，屏幕上显示成小标签，不显示成用户说的话。大模型照常调工具、回答，回答后 10 秒内可以接着问。

```mermaid
sequenceDiagram
  autonumber
  participant P as 手机
  participant C as 设备客户端
  participant H as 小麦网关
  participant V as 看图模型
  participant L as DeepSeek

  Note over C: 到点（天气播报 / 定时任务）
  C->>H: 文字「【天气播报 07:30】先调用查天气工具……」
  H->>L: 对话（带工具）
  L-->>H: 调 get_weather（住宅地址的坐标）
  H-->>C: 回复和语音
  Note over C: 拍照问答：说「帮我看下这是什么」
  C->>H: POST /kiosk/photo/start
  H-->>C: 一次性链接（5 分钟）
  C->>C: 屏幕显示二维码
  P->>H: 打开 /photo/令牌，拍照上传（压缩到 1600 宽）
  H->>V: 照片 + 问题
  V-->>H: 看到的内容
  C->>H: 轮询 /kiosk/photo/令牌，取回答和照片
  C->>H: 文字「【拍照问 09:41】看图模型看到的是……」
  H->>L: 对话
  L-->>H: 一两句回答
  H-->>C: 回复和语音（照片和原话作为图文消息留在对话里）
```

## 10. 数据存在哪

| 在哪 | 文件 | 内容 |
| --- | --- | --- |
| 设备 `~/.config/thinksmart-client/config.json` | 用户设置 | 唤醒词、天气播报、住宅地址、外观、儿童模式、待机页、场景、定时任务、告警、翻译模式（只存改过的） |
| 设备 `~/.local/share/thinksmart-client/` | `alarms/`、`cards.json`、`notes.json`、`photos/`、`avatars/`、`album/`、`offline/`、`kids-usage.json` | 闹钟、图文消息、留言、拍照问答的照片、照片形象、家庭相册、断网时的回答录音、儿童模式今天用了多久 |
| 网关 `data/` | `chat.db`、`usage.db` | 聊天记录和记忆、费用 |
| 网关 `data/` | `device_homes.json`、`device_prefs.json`、`device_lists.json`、`voiceprints.json` | 每台设备的住宅地址、儿童/翻译模式、清单、家人声纹（192 维向量，不存录音） |
| 网关 `data/photos/` | 拍照问答、形象、相册上传的照片 | 7 天后删，清除聊天记录时一起删 |
| 网关 `models/voiceprint/` | `3dspeaker_campplus_zh-cn_16k.onnx` | 声纹模型（不在仓库里，手动下载到这里） |
| 浏览器 localStorage | `xhub-ui`、`xhub-tables` | 网关后台的界面偏好、每个表格的列宽和顺序 |

### 设备接口（/kiosk/*，只接受经隧道来的本机请求）

`/kiosk/asr`、`/kiosk/tts`、`/kiosk/llm`、`/kiosk/chat/*`、`/kiosk/reports`、`/kiosk/billing`（原有），
`/kiosk/geocode`、`/kiosk/home`、`/kiosk/today`、`/kiosk/prefs`、`/kiosk/lists`、`/kiosk/photo/*`、`/kiosk/voiceprint/*`（这一轮加的）。
手机用的两个页面 `/photo/<令牌>`、`/lists/<令牌>` 在局域网里谁都能打开，令牌（128 位随机）就是凭证。

## 硬件限制

- **摄像头用不了**：设备自带的摄像头传感器是三星 s5kc505a，是联想定制件，主线内核没有它的驱动，设备树里摄像头（camss、cci）也是关闭的。`/dev/video0`、`/dev/video1` 是视频编解码器，不是摄像头。社区移植者也认为在主线内核上点亮它希望很小。所以「拍照问这是什么」现在做不了，现在用手机拍照代替（见下面的「拍照问答」）。
- **网关没有显卡**：8 核 i7-8700T 虚拟机、7 GB 内存，本地语音识别只能用小模型（现在是 X-ASR，每句约 0.1 秒）。
