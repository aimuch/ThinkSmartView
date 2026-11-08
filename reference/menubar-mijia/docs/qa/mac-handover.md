# Mac 实机验收与继续修复交接

本任务继续维护 `jryang1997/MenuBarMijia` 的 PR #65、分支 `fix/audit-issues`。目标是完成 #64 索引中的审计问题；自动化检查通过和真实 macOS 行为通过分别记录。不得用“Swift 编译通过”代替菜单栏、系统权限、钥匙串、快捷指令或真实设备验收。

## 本轮交接状态

按用户要求收敛范围：Windows 端能在本机执行的检查已完成；macOS 编译、打包、系统交互及真实设备验收交给 Mac 端继续。代码已分四笔功能提交及一笔验证修正推送到草稿 PR #65；没有合并 main、打 tag 或发布。

固定代码基准：`ad6c72046ca4a7ca88d48ce57cc08da0b7351a55`。其后的交接文档提交不改功能代码。

| 检查 | 实际结果 |
| --- | --- |
| `npm test` | 93/93 通过；核心、界面、布局设置及国际化回归。 |
| `npm run build` | TypeScript 检查与 Vite 生产打包通过。 |
| 浏览器页面冒烟 | 运行真实 Vite 页面，通过 DOM 事件验证英文/华氏切换、重新加载后保留设置与引导不再出现；修正并核对“全部”和设备辅助标签的残留中文。浏览器指针点击与截图接口异常，本轮未验证原生点击手感/视觉布局，不能当作 Tauri 原生验收。 |
| `bash scripts/check.sh --lint-only` | 密钥扫描与机械性编码规范通过。 |
| `bash -n scripts/build_app.sh`、`bash -n scripts/release.sh` | 语法通过；不代表 macOS 实际打包通过。 |
| `bash packaging/homebrew/self_test.sh` | 28 项离线自检通过；本机没有 Ruby，Ruby 语法项未测。 |
| `cargo test --locked --manifest-path windows/src-tauri/Cargo.toml` | 本机失败：`tauri v2.12.4` 自定义 build script 以 `0xc0000005 STATUS_ACCESS_VIOLATION` 退出；未进入测试，不能记为通过。Windows CI 已接入此命令。 |
| `swift test` / App Release 构建 / 本轮 UI 快照 | 本机没有 Swift/Xcode。首轮 CI <https://github.com/jryang1997/MenuBarMijia/actions/runs/40450924249> 的 Linux 与 macOS 均编译失败；Windows 前端通过、Windows 安装包因依赖失败跳过。错误已按下文修正，但 `ad6c720` 的后续 Swift 结果尚未确认，接手先查看 PR 最新 CI。 |
| Homebrew 工作流 | `6a58d7d` 的离线自检 job 已成功：<https://github.com/jryang1997/MenuBarMijia/actions/runs/40450924311>。未向真实 tap 推送。

上一批 `8e5c4dd` 的四项 CI 与 14 张快照通过，不能据此认定本轮新增代码通过。14 条 issue 已附此前证据关闭；其他项目保留未验证或外部前置条件。当前批次尚未完成最终独立双轴复审，Mac 端修复编译/实机问题后需继续复审。

优先检查三处集成：`scripts/build_app.sh` 的 App Intents 元数据路径及本地化资源包；`MenuBarPanelController` 的点击/按住说话、失焦收起、设置窗口入口；语言与温度单位切换后的实时刷新和设备精度。不得删掉元数据门禁或盲录快照以获得绿灯。

### 已定位的 CI 错误与修正

- Linux：`PanelSearchTests.swift` 的三个调用参数顺序错误，另一个 `hasCommand` 误传给测试 helper，而非 `PanelSearchKey`。`ad6c720` 仅纠正实参顺序和归属，没有删除测试或更改断言。
- macOS：`MenuBarPanelController.installObservers` 在 `MainActor.assumeIsolated` 中捕获不可发送的 `Notification`，Swift 6 报 `sending 'notification' risks causing data races`。`ad6c720` 改为先提取 `NSWindow`，仅在主执行器闭包使用该窗口；还需实际编译验证。
- 这两个失败不能称为“平台误报”；修正后的完整核心测试、App 测试、打包及快照仍需跑完。

给 Mac 端代理的任务：读取本文与 PR #65，从上述固定代码 SHA 建修复分支，先跑 `bash scripts/check.sh` 与 `bash scripts/build_app.sh`，修复实际失败，再按下表逐项做系统/真实设备验收与独立双轴复审。沿用现有设备模型、凭据与安全门禁，不扩大功能，不合并或发布；交回固定 SHA、测试结果、失败日志及剩余未测项。

## 领取代码与固定证据

在独立目录领取 PR 的确定提交，不覆盖正在使用的仓库或应用配置：

```bash
git clone --branch fix/audit-issues https://github.com/jryang1997/MenuBarMijia.git MenuBarMijia-qa
cd MenuBarMijia-qa
git fetch origin pull/65/head
git checkout --detach ad6c72046ca4a7ca88d48ce57cc08da0b7351a55
QA_DIR="$HOME/Library/Logs/MenuBarMijia-qa/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$QA_DIR"
git rev-parse HEAD | tee "$QA_DIR/commit.txt"
xcodebuild -version | tee "$QA_DIR/xcode.txt"
swift --version | tee "$QA_DIR/swift.txt"
sw_vers | tee "$QA_DIR/macos.txt"
uname -m | tee "$QA_DIR/architecture.txt"
```

从该 SHA 开始记录；复测另一提交时另开一份记录。若继续修改，先 `git switch -c fix/mac-acceptance`。不改 main，不发版，不把失败用更新快照、延长等待或跳过测试掩盖。

## 构建与自动化验证

需要完整 Xcode 和 macOS 26 SDK；App 仍以 macOS 14 为最低部署目标。`xcode-select -p` 应指向完整 Xcode。安装依赖只能按现有项目流程，不能为了绕过失败随意升级 Swift、依赖或签名配置。

```bash
set -o pipefail
bash scripts/check.sh 2>&1 | tee "$QA_DIR/check.log"
bash scripts/build_app.sh 2>&1 | tee "$QA_DIR/build-app.log"
plutil -p MenuBarMijia.app/Contents/Info.plist | tee "$QA_DIR/info-plist.txt"
codesign --verify --deep --strict MenuBarMijia.app
find MenuBarMijia.app -name extract.actionsdata -print | tee "$QA_DIR/app-intents-metadata.txt"
```

`build_app.sh` 必须成功生成 `Contents/Resources/Metadata.appintents/extract.actionsdata`，缺失会中止打包。测试失败时保留完整日志和差异图，不能直接录制新基线。当前 14 张基线只覆盖主面板九套皮肤与经典皮肤五类卡片，不覆盖所有布局/语言组合。

`build_app.sh` 会重启原先正在运行的同名 App，开始前先保存当前操作。首次验收建议从演示模式启动，避免无意操控真实设备：

```bash
open MenuBarMijia.app --args -home_backend demo
```

命令行参数只用于这次启动，不执行 `defaults delete`，不清除真实账号、钥匙串或用户配置。需要全新安装与旧版升级对照时使用独立 macOS 测试账号。

## 实机顺序与通过条件

| 阶段 / issues | 操作 | 通过条件与证据 |
| --- | --- | --- |
| 启动、演示 #47 | 不登录账号进入演示；切皮肤、切房间、开关灯、执行本地指令和无法解析的句子；进入设置点击连接测试及更新检查 | 面板可用并明显标为演示；虚拟状态变化；无法解析时给本地说明；后台无米家/HA/LG/AI/更新请求。离线断网仍能完成。 |
| 独立设置 #42 | `⌘,` 打开设置；切换到浏览器复制普通测试文本，再返回设置；检查菜单栏面板 | 设置窗口持续存在；没有把长表单塞回菜单栏面板；所有分区可达。 |
| 菜单栏、声音 #54/#56 | 悬停图标、单击、右键、拖动菜单栏图标、切显示器与分辨率；打开/关闭声音反馈 | 摘要随状态更新；定位正确；声音默认关闭、选择开启后成功/失败可区分。私有 KVC/选择器的替代方案按实际源码和诊断记录，未移除的不可声称修完。 |
| 自定义热键 #43 | 录制新组合并重启；关闭全局热键；用另一 App 占用相同组合 | 新组合持久化；旧组合被释放；关闭后不再占用；冲突有可见提示。 |
| 无障碍 #41 | VoiceOver 完成打开面板→找到灯→开关→调亮度；打开系统“降低透明度” | 可读出名称/状态/数值，可调节；编辑布局有菜单入口；实测对比度达到验收要求。截图不能替代读屏操作。 |
| 外观、语言 #51/#52 | 九套皮肤切换浅/深色；切中英与繁体；美国区域设置下检查温度 | 跟随系统外观；温度显示单位与区域一致、设备命令仍为摄氏；参数化文案和长英文无截断。新增资源语言按资源目录约定验证。 |
| 搜索 #60 | 搜索 `sfd` 定位书房灯；↑↓、Return、→；输入完整控制句；连续两次 Esc | 匹配拼音与房间；Return 操作所选在线可控设备；完整指令走原安全路径；第一次清空，第二次收起。 |
| 撤销 #34/#53 | 单设备、多设备、全关、场景执行后悬停横幅；横幅消失后打开 AI 栏撤销 | 悬停不消失；十分钟窗口内有界面入口；只恢复成功执行的字段；撤销后习惯记忆计数正确。 |
| 通知与语音 #50/#55 | 新 macOS 账号首次启动；首次设定时/启用提醒；拒绝通知后查看设置；支持离线中文的机器首次语音 | 启动不索要通知；使用时有用途说明；拒绝后状态和系统修复入口可见；支持时默认本机识别，不支持时明确说明。 |
| 真实连接 #18/#26/#37 | 连接自己的 HA/米家账号；让 HA 地址变化；睡眠/锁屏/解锁；需要时使用海外账号 | HA 不自动把令牌发给新主机；候选先确认；锁屏暂停轮询、解锁恢复；地区与实际账号一致。Windows 海外扫码另在 Windows 验证。 |
| 误操作与定时 #21/#22/#23 | 低置信度和高风险 AI 计划；当前房间全关；隐藏设备、普通插座；设定时后合盖跨过触发时间 | 低置信度/高风险先确认；全关按住且范围正确；不碰隐藏或未加入的插座；错过超过 60 秒的定时只进入待确认，醒来不自动执行。 |
| 长开、电量、用电 #35/#62 | 5W 路由器插座、缺功率插座、大功率测试设备；重启；查看七日用电并清除 | 低功率/无功率不误提醒；低电量 24 小时冷却跨重启；能耗只累积有效表读数增量、无负数/重复累计；清除后重启不恢复旧历史。 |
| URL、快捷指令 #57 | 快捷指令 App 搜索四个动作；Spotlight 调用；`menumijia://toggle?device=...`、scene、ask；确认框取消 | 四个动作实际可发现、执行并报告结果；手写 URL 默认确认、取消不下发；冷启动等首份快照；沿用设备控制失败回滚/撤销与云端授权。 |
| Mac 自动化 #59 | 默认空规则；创建锁屏关灯；锁屏/合盖、解锁、插拔外接屏；一分钟内重复触发 | 规则默认关闭；按配置执行；触发去重；解锁后可撤销；不计入习惯。真实睡眠可能中断云请求，记录实际结果，不能只靠直接调用 handler 测试。 |

涉及真实设备时选可安全切换的测试灯；先核对名称与范围。不要用路由器、冰箱、鱼缸、加热设备作自动化冒烟目标。

## 升级、签名与凭据专门验收

#16/#17/#32 需要独立 macOS 测试账号与真实签名产物，不能在日常账号里写入假 token 或删除凭据。

1. 新装登录后，仅检查偏好域是否存在旧明文键；不要输出整个偏好域或钥匙串内容。升级旧版后检查 `ha_fallback_token`、`mi_session_fallback_v1`、`keychain_fallback_*`、`lg_webos_client_key` 是否在成功迁移后删除。迁移失败应保留原值以便重试，并显示真实限制。
2. 连续两次升级验证固定证书的钥匙串访问。记录是否有一次必要授权；拒绝后不能回退写入明文。
3. 正常更新验证离线 Ed25519 签名和已钉住的代码签名身份；同 Bundle ID 的 ad-hoc 包、缺 pin、篡改 DMG 并同步修改 SHA-256、错误签名都应拒绝。固定自签名与 Apple Developer ID 的 Gatekeeper/quarantine 行为分别记录，按 ADR-0005 说明限制。
4. #30：用测试电视或受控本地 TLS 服务替换证书，必须拒绝连接且提示；单纯点击“发起配对”不能绕过钉住；只有明确确认“重新信任电视”才清除旧身份，且必须在电视确认新配对。HA 非本机 HTTP 地址在设置与引导均有明文传输提示。
5. Windows 同时验证 EXE/MSI 的版本、SHA-256、Authenticode 状态、安装/托盘/WebView2、凭据迁移。CSP 需要在实际 WebView DevTools 中观察内联脚本拒绝，`native_http` 白名单外地址需要通过真实 invoke 被拒绝。Rust 单测不替代这两项。

## 外部前置条件

- #58：桌面 Widget / 控制中心控件需要实际开发者签名、App Group 标识及授权。当前不能把纯 SwiftPM 主程序当成已可安装的 Widget 扩展。
- #61：`jryang1997/homebrew-tap` 尚不存在；新建公开 tap、发布首个 cask和设置跨仓库写权限需要用户明确授权。需要新 Mac 上一条命令安装并启动、下一版发版后 tap 自动更新的证据后才能关闭。
- GitHub 签名 secrets 不能由代码或普通 CI 日志推断。私钥仅用现有凭据机制；不得放入仓库、诊断、截图、issue 或聊天。

## 交回格式与继续修复要求

在 `$QA_DIR` 留存 `commit.txt`、系统/工具链信息、完整检查/打包日志、失败截图与快照差异图。每条验收记录“通过 / 失败 / 未测”、复现步骤、预期与实际、是否涉及真实设备。

公开反馈只附应用里的“复制诊断信息”及脱敏后的必要片段；不上传原始偏好域、HAR、完整网络请求、钥匙串内容、二维码或账号截图。保留未脱敏原件于本机私有日志目录。

接手代理先复现用户报告的具体失败或直接使用其报告为证据；修根因，补能捕获真实行为的回归，再跑相关路径和完整规定检查。改动只进入修复分支；不得自行合并、发布、换模型路由或修改认证权限。仅在本条验收真的满足后关闭 issue，并附固定 SHA、运行记录和仍未测的边界。
