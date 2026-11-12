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
| 大模型费用 | `server/main/xiaozhi-server/core/billing/` | 后台「费用」页分四页：总览（时间范围、按小时/按天、和上一段比、趋势、token 构成）、排行（模型/设备/用途/条目）、热力图（星期几 × 几点）、单价和额度（分时单价、节假日、额度，可从 models.dev、LiteLLM、OpenRouter 同步单价）。每次请求还记首字耗时、总耗时和失败。设备设置 →「智能体」也能看。 |
| 自定义唤醒词 | `client/thinksmart_client/wake_words.py` | 设置 →「唤醒词」：按住说出新词（网关识别成文字、换算成拼音），马上生效；也可以说「唤醒词加一个……」。默认小麦小麦、哈喽小麦、小麦同学 |
| 天气 | `server/main/xiaozhi-server/core/utils/open_meteo.py` | Open-Meteo，不用 Key：当前天气、PM2.5、7 天预报和降水概率，国外城市也能查。设备所在地在后台「意图识别 → 查天气 → 设置」里改 |
| 千问 TTS（方言音色） | `server/main/xiaozhi-server/core/providers/tts/qwen3_tts.py` | 后台「语音合成」切到千问 TTS、填百炼 Key，可选上海话、北京话、四川话、粤语等音色 |
| 同音字理解 | `server/main/xiaozhi-server/hub-base-prompt.txt` | 识别错的同音字（「先辈怎么做」）由大模型按读音和上下文理解，直接按最可能的意思回答 |
| 天气播报、住宅地址 | `client/thinksmart_client/broadcast.py` | 设置 →「天气播报」：定时播报（早上那次可顺便播提醒和新闻）；住宅地址按住说出来，没说地点的天气按它查 |
| 拍照问答 | `server/…/core/agent/photo_ask.py`、`photo_command.py`，`client/…/camera.py` | 「帮我看下这是什么」：机身摄像头实时画面、倒数自动拍（没有摄像头时手机扫码）；后台「大模型 → 看图模型」填 Key |
| 前置摄像头 | `system/camera/`（上游 PR #7） | S5KC505A 驱动、设备树、libcamera 0.8，变成普通摄像头 `/dev/video10`；单独安装，见 system/camera/README.md |
| 首页外观 | `client/thinksmart_client/looks.py`、`web/v2/looks.css` | 设置 →「外观」：主题、形象（可用机身摄像头或手机拍照生成卡通形象）、首页显示内容；首页布局可选「仪表盘」（`web/v2/dash.js`，设计画布第 35 块），主题多了「薰衣草」 |
| 儿童模式、待机页、做菜模式 | `kids.py`、`cook.py` | 设置 →「孩子和学习」「外观」里各有一张卡片；做菜时直接说「下一步」 |
| 场景、留言、清单、电台、助眠声 | `daily.py`、`media.py`、`server/…/core/utils/device_lists.py` | 都可以直接说；清单能在手机上看 |
| 定时任务、主动告警、翻译模式 | `client/thinksmart_client/app.py` | 「每天早上七点告诉我要不要带伞」；米家漏水告警；「帮我们中英互译」 |
| 家人声音、断网兜底 | `server/…/core/utils/local_voiceprint.py`、`client/thinksmart_client/offline.py` | 设置 →「家人声音」录名字和三句话；网关连不上时也能调音量、报时、开关灯 |
| 网关后台界面 | `server/…/core/admin/static/index.html` | 侧栏底部一张卡片：设备状态图标（颜色就是状态，连不上换成警告圈）+ 退出登录、心跳时间、账号菜单、三段主题切换；账号菜单 →「界面」：主题、宽度、侧栏、菜单顺序；表格可排序、拖列、调列宽、拖行 |
| 设备设置页 | `client/web/v2/`（`app.js` 的 `SET_GROUPS`） | 左边侧边栏 4 组 11 类：设备（屏幕和声音、外观、网络、系统）、日常（闹钟和作息、家居、生活）、家人（家人、孩子和学习）、智能（语音、智能体）；右边只显示这一类的卡片。侧边栏能收起成只剩图标，横屏能拖动调宽。加一类只改配置、加一页卡片。孩子和学习有「学一学」和最近 7 天，家人有留言，语音能选这台设备回答的声音（网关 `/kiosk/voice`），智能体列出每个模型的称呼 |
| 设备性能页 | `client/thinksmart_client/perf.py` | 设置 →「系统」第一张卡片是小结，点「详细」或说「设备温度多少」：每核占用/频率/温度、GPU、内存、存储、网速、最占资源的进程（进程名翻译成「屏幕浏览器」「摄像头画面」等）；只在打开时每 2 秒读一次 /proc 和 /sys |
| 后台登录、状态、工作原理 | `server/…/core/admin/`（`brand.py`、`presence.py`） | 登录页可保持登录 30 天；账号菜单里改密码、退出登录、改名称和图标（麦穗、表情或上传图片，浏览器标签图标跟着换）、填自己的仓库链接；状态页按设备心跳显示待机/对话中/离线，有 24 小时对话量、今天的费用、拓扑；工作原理画成六步路径 |
| DSH（DeepSeek Harness） | `server/…/core/agent/dsh_session.py`、`core/providers/llm/dsh_cli/` | 后台「大模型 → 本地 CLI 与智能体」选 DSH：用 DeepSeek 的 Key，在网关上读文件、做事；说「嗨 DSH」叫它。网关上要装 `@deepseek-ai/dsh` 和 Node 24 |
| 智能体页 | `server/…/core/agent/api_agent_session.py`、`core/providers/llm/agent_api/` | 后台侧栏「智能体」：本机（DSH、Claude Code）、云端（Grok Bot）、自己加的（OpenAI 兼容的 Hermes Agent 等、Dify 工作流、Coze）。叫名字派活，慢了转后台，做完汇报 |
| 按设备分别配置 | `server/…/core/utils/device_settings.py` | 后台侧栏「设备」：每台设备起名字，单独设默认模型、音色、人设（有小老师、陪老人等模板），没设的跟着全局。插件逐个选「跟着全局 / 这台开 / 这台关」，只记改过的，全局后来勾的新插件照样跟着；「意图识别」页每个插件下面列出单独开、关的设备，点「按设备」直接改（设计画布第 40 块）。「删除设备」会逐项列出要删的数据（聊天记录和记忆、住宅地址、儿童模式、家人声音、清单可选不删，费用统计保留），设备在线时要输入管理员密码 |
| 看图模型 12 家 | `server/…/core/agent/photo_ask.py` | DeepSeek V4.1 Flash（沿用大模型的 Key）、智谱、千问、豆包、Kimi、阶跃、硅基流动、OpenAI、Gemini、Claude、Ollama、自定义 |
| 本地和局域网服务 | `server/…/core/admin/` | Ollama、LM Studio 等可「读取模型列表」；语音识别和合成可接局域网里的 OpenAI 兼容服务（Speaches、Kokoro-FastAPI） |
| 首页常用设备 | `client/web/v2/home.js` | 家居页长按磁贴放到首页，首页轻点开关；叫醒时家居页自动收起 |
| 没网时换 Wi‑Fi、USB 线直连 | `client/web/v2/app.js`、`system/usbnet/` | 没连上网络时首页顶部提示「换 Wi‑Fi」；USB 线接电脑，电脑自动拿到 172.16.42.x，`ssh ubuntu@172.16.42.1` |
| 工作台、智能中枢蓝牙灯 | `server/…/core/integrations/`、`plugins_func/functions/integrations.py`，`client/web/v2/work.js` | 接家里另外几个项目（设计画布第 38、39 块），都是后台「意图识别 › 插件」里的插件，能按设备开关（设备自己的设置 › 智能体 › 接入的服务 也能开关这台的四个）：**智能中枢**（[SmartHub](https://github.com/aimuch/SmartHub) 的蓝牙灯和可调电源，填它的访问令牌）、**机柜状态**（rack-screen 用的 Prometheus，选填 rack-screen 令牌）、**仓库同步**（repo-manager）、**桌面**（Mac 上的 [DeskHub](https://github.com/aimuch/DeskHub) App 0.4.4 起，设置 › 小麦网关 里填网关地址和同一串连接口令）。设备顶栏的工作台按钮：切显示器输入源、AI 用量、电脑上的提醒事项、主机状态、同步结果；家居页最上面是蓝牙灯；首页和仪表盘可加摘要；主动告警可加机柜。语音：「补光灯调到 4300K」「服务器都正常吗」「仓库同步怎么样」「显示器切到 Windows」「Claude 额度还剩多少」 |
| 语音识别调优 | 服务器的 `data/.config.yaml` | X-ASR 精度版、整句识别（不按停顿分段）；对比方法和结果见 [scripts/asr-eval/](scripts/asr-eval/README.md) |

## 第三方

- 米家扫码登录、请求签名、MIoT 规格处理参考 [MenuBarMijia](https://gitlab.com/jryang1997/MenuBarMijia)（MIT，jryang1997）改写；客户端测试用了它的 `Tests/Fixtures/miot_golden.json`。
- 二维码：qrcode.js（Kazuhiko Arase，MIT），在 `client/web/v2/vendor/`。
- USB 网络的做法参考 [jethac/lenovo-thinksmart-view](https://github.com/jethac/lenovo-thinksmart-view)（MIT）。
- 费用页的时间范围、和上一段比、趋势开关、排行、热力图、单价同步顺序参考 [CPA Manager Plus](https://github.com/seakee/CPA-Manager-Plus)（MIT，seakee）的用量分析和模型价格设计，代码是自己写的。
- 网关后台的动效时长和缓动曲线参考 [Arc Library](https://github.com/kuratlielia/arc-library) 的 motion tokens（MIT）。
- Grok Bot 改走 Cursor 后台发送的字段定义参考上游 issue #4 里网友的分析和 [grok-bot-cli#166](https://github.com/ScriptedAlchemy/grok-bot-cli/pull/166)。
