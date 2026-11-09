# 小麦 · ThinkSmart View 语音终端

把联想 ThinkSmart View（CD-18781Y）刷成本地语音助手「小麦」，网关「小麦网关」部署在局域网服务器上。设备负责唤醒、录音、播放和屏幕；语音识别、调大模型、语音合成、聊天记录和记忆都在网关上（见[系统架构](docs/architecture.md)）。本仓库由几个上游项目合并而成，在它们之上加了米家直连、内容卡片、屏幕旋转、大模型费用统计、更多供应商，以及自建的更新通道。

| 目录 | 是什么 | 跑在哪里 | 上游 |
| --- | --- | --- | --- |
| `client/` | 小麦：设备上的语音客户端（唤醒、对话界面、家居、设置） | 设备 | [xiaozhi-thinksmart-client](https://github.com/justhalfbit/xiaozhi-thinksmart-client) |
| `server/` | 小麦网关：语音识别、大模型、语音合成、管理后台 | 服务器 | [xiaozhi-hub-server](https://github.com/justhalfbit/xiaozhi-hub-server) |
| `system/` | 小麦系统：刷机镜像、系统层（展示屏外壳、音频、熄屏…）、在线更新程序 | 设备 | [thinksmart-ubuntu-image](https://github.com/justhalfbit/thinksmart-ubuntu-image) |
| `reference/menubar-mijia/` | 只读参考：米家扫码登录、签名、MIoT 规格的原实现（MIT） | — | [MenuBarMijia](https://gitlab.com/jryang1997/MenuBarMijia) |
| `deploy/` | 设备连网关的 SSH 隧道、服务器上的发布服务、设备配置脚本 | 两边 | 本仓库 |
| `scripts/` | 同步上游、发布客户端、部署网关、语音识别评测 | 这台 Mac | 本仓库 |
| `docs/` | [系统架构和流程图](docs/architecture.md) · [部署](docs/deploy.md) · [更新与跟进上游](docs/updates.md) · [刷机记录](docs/flashing.md) · [功能规划](docs/roadmap.md) | — | 本仓库 |
| `local/` | 只在这台 Mac 上：刷机镜像、原厂整盘备份、edl 工具（git 忽略，绝不上传） | 这台 Mac | — |

上游目录内部的文件名、代码里的包名和服务名、设备和服务器上的安装路径都保持原样，跟进上游时冲突最少（命名规则见[设计画布](https://claude.ai/artifact/EY4tpyoxYbT8h3saGzAWaw)）。每个目录同步到上游的哪个提交记在 [`UPSTREAM`](UPSTREAM)。刷机步骤见 [system/README.md](system/README.md)。

## 本仓库加的功能

| 功能 | 在哪里 | 怎么用 |
| --- | --- | --- |
| 米家直连 | `client/thinksmart_client/mijia/` | 屏幕顶栏 🏠「家居」→ 选地区 → 用米家 App 扫码。左上角切换家庭，房间横向切换，显示温湿度和亮灯数；点一下开关，点卡片调亮度、色温、温度、模式、风速、窗帘。直接说「打开书房的灯」「客厅温度多少」「哪些灯开着」也行；插座断电、加热设备开机这类会先问一句。不存密码和 passToken，令牌文件权限 0600，过期了重新扫码。 |
| 内容卡片 | `self.screen.show_card`（`client/thinksmart_client/mcp.py`、`client/web/v2/home.js`） | 问菜谱、步骤、清单、对比时，模型把内容作为一条图文消息放进屏幕上的对话（标题、列表、表格、图片），嘴上只说一句；客户端保存最近 30 条 |
| 屏幕旋转 | `client/thinksmart_client/rotation.py` | 设置 →「屏幕方向」：横屏、竖屏和它们的倒置，或「跟随摆放方向」 |
| 唤醒二次确认 | `client/thinksmart_client/wake_verify.py` | 唤醒词命中后经网关识别核对一次（约 0.3 秒），减少误唤醒；网关问不到时照常唤醒 |
| 更多供应商 | `server/main/xiaozhi-server/core/admin/catalog.py` | 后台「大模型」21 家；「语音识别」分本地（X-ASR 快速版/精度版、Qwen3-ASR、SenseVoice，后台可下载模型）和云端（豆包、阿里百炼、腾讯、讯飞、百度、OpenAI、Groq） |
| 大模型费用 | `server/main/xiaozhi-server/core/billing/` | 后台「费用」页：今日、本月、累计、近 30 天、按模型/设备/用途；分时单价、节假日、额度（拒绝或改用便宜模型）。设备设置页也能看。 |
| 自定义唤醒词 | `client/thinksmart_client/wake_words.py` | 设置 →「唤醒词」：按住说出新词（网关识别成文字、换算成拼音），马上生效；也可以说「唤醒词加一个……」。默认小麦小麦、哈喽小麦、小麦同学 |
| 天气 | `server/main/xiaozhi-server/core/utils/open_meteo.py` | Open-Meteo，不用 Key：当前天气、PM2.5、7 天预报和降水概率，国外城市也能查。设备所在地在后台「意图识别 → 查天气 → 设置」里改 |
| 千问 TTS（方言音色） | `server/main/xiaozhi-server/core/providers/tts/qwen3_tts.py` | 后台「语音合成」切到千问 TTS、填百炼 Key，可选上海话、北京话、四川话、粤语等音色 |
| 同音字理解 | `server/main/xiaozhi-server/hub-base-prompt.txt` | 识别错的同音字（「先辈怎么做」）由大模型按读音和上下文理解，直接按最可能的意思回答 |
| 天气播报、住宅地址 | `client/thinksmart_client/broadcast.py` | 设置 →「天气播报」：定时播报（早上那次可顺便播提醒和新闻）；住宅地址按住说出来，没说地点的天气按它查 |
| 拍照问答 | `server/…/core/agent/photo_ask.py` | 「帮我看下这是什么」，手机扫码拍照；后台「大模型 → 看图模型」填 Key |
| 首页外观 | `client/thinksmart_client/looks.py`、`web/v2/looks.css` | 设置 →「外观」：主题、形象（可用照片生成卡通形象）、首页显示内容 |
| 儿童模式、待机页、做菜模式 | `kids.py`、`cook.py` | 设置里各有一张卡片；做菜时直接说「下一步」 |
| 场景、留言、清单、电台、助眠声 | `daily.py`、`media.py`、`server/…/core/utils/device_lists.py` | 都可以直接说；清单能在手机上看 |
| 定时任务、主动告警、翻译模式 | `client/thinksmart_client/app.py` | 「每天早上七点告诉我要不要带伞」；米家漏水告警；「帮我们中英互译」 |
| 家人声音、断网兜底 | `server/…/core/utils/local_voiceprint.py`、`client/thinksmart_client/offline.py` | 设置 →「家人声音」录名字和三句话；网关连不上时也能调音量、报时、开关灯 |
| 网关后台界面 | `server/…/core/admin/static/index.html` | 侧栏底部账号菜单 →「界面」：主题、宽度、侧栏、菜单顺序；表格可排序、拖列、调列宽、拖行 |
| 后台登录、状态、工作原理 | `server/…/core/admin/`（`brand.py`、`presence.py`） | 登录页可保持登录 30 天；账号菜单里改密码、退出登录、改名称和图标（麦穗、表情或上传图片，浏览器标签图标跟着换）、填自己的仓库链接；状态页按设备心跳显示待机/对话中/离线，有 24 小时对话量、今天的费用、拓扑；工作原理画成六步路径 |
| DSH（DeepSeek Harness） | `server/…/core/agent/dsh_session.py`、`core/providers/llm/dsh_cli/` | 后台「大模型 → 本地 CLI 与智能体」选 DSH：用 DeepSeek 的 Key，在网关上读文件、做事；说「嗨 DSH」叫它。网关上要装 `@deepseek-ai/dsh` 和 Node 24 |
| 看图模型 12 家 | `server/…/core/agent/photo_ask.py` | DeepSeek V4.1 Flash（沿用大模型的 Key）、智谱、千问、豆包、Kimi、阶跃、硅基流动、OpenAI、Gemini、Claude、Ollama、自定义 |
| 本地和局域网服务 | `server/…/core/admin/` | Ollama、LM Studio 等可「读取模型列表」；语音识别和合成可接局域网里的 OpenAI 兼容服务（Speaches、Kokoro-FastAPI） |
| 首页常用设备 | `client/web/v2/home.js` | 家居页长按磁贴放到首页，首页轻点开关；叫醒时家居页自动收起 |
| 没网时换 Wi‑Fi、USB 线直连 | `client/web/v2/app.js`、`system/usbnet/` | 没连上网络时首页顶部提示「换 Wi‑Fi」；USB 线接电脑，电脑自动拿到 172.16.42.x，`ssh ubuntu@172.16.42.1` |
| 语音识别调优 | 服务器的 `data/.config.yaml` | X-ASR 精度版、整句识别（不按停顿分段）；对比方法和结果见 [scripts/asr-eval/](scripts/asr-eval/README.md) |

## 第三方

- 米家扫码登录、请求签名、MIoT 规格处理参考 [MenuBarMijia](https://gitlab.com/jryang1997/MenuBarMijia)（MIT，jryang1997）改写；客户端测试用了它的 `Tests/Fixtures/miot_golden.json`。
- 二维码：qrcode.js（Kazuhiko Arase，MIT），在 `client/web/v2/vendor/`。
- USB 网络的做法参考 [jethac/lenovo-thinksmart-view](https://github.com/jethac/lenovo-thinksmart-view)（MIT）。
- 网关后台的动效时长和缓动曲线参考 [Arc Library](https://github.com/kuratlielia/arc-library) 的 motion tokens（MIT）。
- Grok Bot 改走 Cursor 后台发送的字段定义参考上游 issue #4 里网友的分析和 [grok-bot-cli#166](https://github.com/ScriptedAlchemy/grok-bot-cli/pull/166)。
