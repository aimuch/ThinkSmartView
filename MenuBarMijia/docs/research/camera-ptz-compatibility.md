# 摄像头云台（PTZ）兼容性调研

调研日期：2026-11-07　范围：小米/米家/创米/华来等米家生态摄像头的**实体云台转向与停止**能力，以及在本应用（macOS 为主）里落地的通道选择。
结论先行：**公开 MIoT 规格没有方向云台；可用的方向控制全部在私有 P2P 通道。当前最小、可复现、风险最低的方案是给内置 go2rtc 打一个小补丁（上游 PR #2531），由它复用已建立的 P2P 会话发 `0x112` 电机命令，应用只调本地 HTTP 接口。** 本文所有“已验证/推断/未知”逐条标注；没有证据的型号一律写未知。

---

## 0. 用户机型映射

| 项 | 结论 | 置信度 | 证据 |
| --- | --- | --- | --- |
| 产品名 | 小米智能摄像机 3 Pro 云台版 | 高 | 小米商城商品页（product_id=19174）标题；miot-spec 产品库页面标题 |
| Mi model | `chuangmi.camera.061a01` | 高 | miot-spec 产品页 JSON-LD：`"name":"小米智能摄像机3 Pro 云台版","model":"chuangmi.camera.061a01"`（产品 ID 77542，厂商“小白/创米”，2023-09 验证）；python-miio issue #1959 同型号同名称，固件 `5.1.7_0420` |
| 物理云台 | 是（产品名与 `cruise` / `camera-fav-area` 服务一致） | 高 | miot-spec 实例 `urn:miot-spec-v2:device:camera:0000A01C:chuangmi-061a01:3` 含 `cruise`（巡航）、`camera-fav-area`（收藏位） |
| P2P transport（cs2 / tutk / mtp） | **未知** | — | 本地无留存；只能运行时由 `/v2/device/miss_get_vendor` 返回的 `vendor` 判定（1=tutk，4=cs2，3=agora，6=mtp） |
| `0x112` 电机命令是否可用 | **未知**（无公开实机报告） | — | 见 §2、§3 |

**不要混淆的邻近型号**（miot-spec 产品页 JSON-LD 逐一核对）：

| model | 产品名 | 备注 |
| --- | --- | --- |
| `chuangmi.camera.061a01` | 小米智能摄像机 3 Pro 云台版 | 用户这台 |
| `chuangmi.camera.069a01` | 小米智能摄像机 3 云台版 | 另一款；go2rtc 表记为 “Xiaomi Smart Camera 3 PTZ Version”，`cs2+tcp` |
| `chuangmi.camera.061a03` | Xiaomi Smart Camera C500 Pro | 同 061 家族，`cs2`（视频已验证） |
| `chuangmi.camera.039c04` / `039a04` | Xiaomi Smart Camera C400 | `cs2` |
| `chuangmi.camera.72ac1` | 小米智能摄像机 C300 双摄版 | `cs2+tcp` |
| `xiaomi.camera.c01a01` | Xiaomi Smart Camera C300 | `cs2` |

注意：`061a01` 的 `camera-control` 服务**没有** `ptz-camera` 属性（只有 `on` / `night-shot` / `recording-mode` / `human-tracking` 等）。任何“用 `ptz-camera=true` 判断能否转”的门控都会误判这台用户机。

---

## 1. 协议族矩阵

### 1.1 协议族

| 族 | 传输 | 方向命令 | 停止语义 | 备注 |
| --- | --- | --- | --- | --- |
| CS2（`cs2` / `cs2+udp` / `cs2+tcp`） | 局域网 UDP/TCP，摄像机 32108 起 | cfg `0x112` + JSON `{"operation":1左 2右 3上 4下}` | 社区证据显示**每次命令是一个有界小步**（无需显式 stop）；见 §6 的边界说明 | go2rtc 用既有 MISS 会话发送 |
| TUTK / dafang（`tutk` / `xiaomi/legacy`） | TUTK IOTC（UID） | 仅旧机型：raw `0xff2404`（左 1,0 / 右 2,0 / 上 0,2 / 下 0,1） | **必须显式 stop `(0,0)`**，否则一直转到限位 | 上游注释里左右写反，社区实测见 §2 |
| MTP（vendor 6） | 摄像机 40000 | 无公开实现 | — | go2rtc 不支持；`c302n` 是已知案例（云端报 cs2，实际 MTP） |
| Agora（vendor 3） | 云端 | 无 | — | 不支持 |

### 1.2 型号矩阵（只列有证据的；其余未知）

“视频已验证”来自 go2rtc issue #1982 的主表/评论（真实用户 probe）；“PTZ 证据”来自 #2162 评论与 PR #2531。

| 型号 | 产品名 | transport（视频实测） | 方向 PTZ 证据 | 停止证据 | 等级 |
| --- | --- | --- | --- | --- | --- |
| `chuangmi.camera.061a01` | 3 Pro 云台版（用户） | **未知** | 无 | 无 | 未知 |
| `chuangmi.camera.029a02` | 云台版2K | `cs2` | PR #2531 作者真机：一左/右上下一小步 ≈70px（640 宽画面），五步 400ms 间隔全部落地 | 松手/Stop 后不再走；连续 10s 封顶 | 已验证（方向+停止） |
| `xiaomi.camera.c01a01` | C300 | `cs2` | korasino fork “C300”；bkulpa 同型号真机 | 位置查询（op 6）、绝对定位（op 13）、自动回中已验证；方向操作未逐项公开 | 已验证（位置/绝对）；方向推断 |
| `chuangmi.camera.039a04` / `039c04` | C400（两代） | `cs2` | 评论者 omansak：C400 用 korasino fork “PTZ movements work fine”（未写具体子型号） | 同 fork | 家族级已验证；子型号未拆分 |
| `chuangmi.camera.72ac1` | C300 双摄版 | `cs2+tcp` | 无公开方向报告 | — | 未知 |
| `isa.camera.hlmax` / `isa.camera.500dh` | CW500 双摄版 | `cs2+udp` / `cs2+tcp` | **已更正**：`{"operation":1..4}` 在 CW400/CW500 真机可用，每步自停（`lubos-hnanicek/xiaomi-camera-viewer` 源码与 probe 脚本）；此前 bhaslaman 的失败是键名用了 `direction` | 单步自停，无需 stop | 已验证（步进） |
| `isa.camera.df3` | 大方 1080P 云台 | `tutk`（miss/legacy 均有记录） | raw `0xff2404`，社区真机（gn1024） | 必须显式 stop `(0,0,5)` | 已验证（仅该旧机型；本补丁已实现） |
| `isa.camera.isc5c1` | 小方 | `tutk` | 上游注释把 `isc5c1` 与 df3 同列（代码层面） | 未独立验证 | 推断 |
| `chuangmi.camera.061a03` | C500 Pro | `cs2` | 无 | — | 未知 |
| `chuangmi.camera.069a01` | 3 云台版 | `cs2+tcp` | 无 | — | 未知 |
| `chuangmi.camera.039c01` | 2 云台版 | `cs2+tcp` | 无 | — | 未知 |
| `chuangmi.camera.021a04` / `026c02` / `ipc019` / `ip029a` | 云台版 Pro / SE+ / 云台版 / 2K | `cs2` | 无 | — | 未知 |
| `chuangmi.camera.81ac1` / `079ae2` / `079ac1` / `083ac1` | C700 / C701 / 4 4K / 4 变焦 | `cs2` / `cs2+tcp` | 无 | — | 未知 |
| `xiaomi.camera.c302n` | CW302 | 云端报 `cs2`，实际 MTP | 无 | — | 不适用 |

**品牌不能作为门控**：创米（chuangmi）既有已验证的 C400，也有反证的 CW500；华来（isa）既有可用的旧 dafang，也有反证的 CW500。判断依据必须是 **model + 运行时 vendor + 实测 allowlist**。

---

## 2. 社区 PTZ 证据（精确来源）

### 2.1 上游状态

- go2rtc v1.9.15（`7c05066c90ab6ad1fccb506d5f0408aa31ebe60f`）**没有 PTZ API**；`pkg/xiaomi/miss/client.go` 只有注释掉的 dafang 方向命令。
- 上游 issue #2162（open，2026-03-17）：维护者确认“知道怎么用小米协议控制云台，但 go2rtc 架构不支持 PTZ”。
- 上游 **PR #2531**（open、mergeable，head `e23a21b9ee631873ea299881975aec6ad276e56f`，base `eb75edcb…`，作者 bcamarneiro）：
  - `miss.Client.Motor(operation)` 在**流已经持有的同一个 P2P 会话**上 `WriteCommand`，命令 `0x112` + `{"operation":N}`；不新建会话。
  - `POST /api/xiaomi/motor?src=<stream>&operation=1..4`。
  - 同时把方向映射成 ONVIF PTZ（`ContinuousMove` → 每 400ms～1.2s 发一步，`Stop` 取消，**10s 封顶**）。
  - 真机：`chuangmi.camera.029a02` + Frigate 0.17.2；一步 ≈70px（pan）/≈40px（tilt）；五步全部落地；反向五步回到原构图；移动时画面不中断。
  - 关键限制（作者原话）：“the camera I tested accepts a single P2P session, and dialing a second one drops the video.” → **应用侧另起一条 P2P 会话会掉画面**。

### 2.2 另一份独立实现（CW400/CW500）

`lubos-hnanicek/xiaomi-camera-viewer`（main）：`bridge/internal/miss/client.go:284-315` 与
`bridge/NOTICE.md:27-44` 记录：

- `{"operation":1..4}` 是 CW400（`isa.camera.hlc8/hlc8a`）与 CW500（`isa.camera.500dh/hlmax`）
  真机接受的载荷；**每一次调用是一个固定小步，随后自停，不需要 stop**；
- `{"direction":…,"speed":…}` 会被静默忽略——这解释了 `@bhaslaman` 的“只读状态”误判；
- 上游 `ReadCommand` 按小端读取命令号，必须改成大端；命令通道必须持续排水，否则约十条
  电机回复后整个视频会话断开。

本次实现采用同一结论：步进型不发 stop；连续型（C300/C400）才发 `-1001`；未分类机型
每次按住只发一次方向并补发 stop。

### 2.3 fork

- `korasino/go2rtc` 分支 `feat/xiaomi-miss-ptz`：commit `95d32a9c85`（add MISS PTZ control API）、`e746789ecb`（tighten MISS PTZ synchronization），相对 master +约 700 行；`POST /api/xiaomi/ptz?src=…`，动作 `move/stop/calibrate/refresh`，operation 1/2/3/4、停止 `-1001`、取位置 `6`、绝对 `13`。作者称用于 C300；评论者确认 C400 可用。
- `bkulpa/go2rtc` 分支 `feat/xiaomi-auto-center`：commit `c88e1c980d7ac565bd23cee2b992a7ca34f19990`、`a23db132dc2c5e8d7a1460ea88c7d3a3c550d1dc`；在 `xiaomi.camera.c01a01` 真机验证位置反馈、绝对定位与自动回中；CS2 命令 ID 需按大端解析；该机型 `0x113` 响应尾部有 NUL。PR：`korasino/go2rtc#1`。
- 对 **`chuangmi.camera.061a01` 的 PTZ 证据：没有**。korasino/bkulpa/PR #2531 的实测型号都不包含它。任何“3 Pro 云台版肯定能转”的说法目前都是推断。

---

## 3. 本次可复现验证（已实际执行）

在 `/tmp` 完成，未改产品代码、未替换日用二进制：

1. 克隆 go2rtc，检出 `v1.9.15`（`7c05066`）。
2. 从 `bcamarneiro/go2rtc@xiaomi-ptz` 取 PR #2531 的两个 commit，`git cherry-pick 1a056d7c29 e23a21b9ee`：**零冲突**，`git diff --stat` = 10 files, +594/−5。
3. 用临时下载的 Go 1.26.10（`go.mod` 要求 1.26）构建：`go build -trimpath` **成功**，产物 28,616,882 bytes。
4. `go test ./pkg/onvif ./pkg/xiaomi/...` 与 `go test ./internal/onvif` **全部通过**。
5. 端到端探针（无摄像机，仅本地 API）：
   - 官方 v1.9.15：`POST /api/xiaomi/motor` → **404 `404 page not found`**。
   - 打补丁后：未知流 → **404 `xiaomi: motor: no stream "nope"`**；`operation=9` → **400 `operation must be 1 (left), 2 (right), 3 (up) or 4 (down)`**。
   → 说明路由存在且参数校验正确；这也是应用侧**无副作用的能力探针**（`GET` 在补丁返回 405、官方返回 404）。

未做：真机转向（无授权、无设备在环）。`0x112` 对 `061a01` 是否生效仍未验证。

---

## 4. 能力门控：用 model / transport，不用品牌

运行时可拿到的事实：

1. 设备 model：`MiDeviceRecord.model`（米家设备列表），如 `chuangmi.camera.061a01`。
2. P2P transport：`/v2/device/miss_get_vendor` 的 `vendor`（1=tutk，4=cs2，3=agora，6=mtp）。本应用已在 `MiCloudClient.cameraP2PVendor` 实现（映射见 `MiCloudClient.swift:421-445`），并在 `MiCloudProvider.resolveCameraStream` 里传给桥接。
3. 流是否已连接：go2rtc `/api/streams` 里有该流（PTZ 命令要求“source connected”）。
4. 构建是否带电机接口：`GET /api/xiaomi/motor` → 405（补丁）/ 404（官方）。**不产生设备动作**。

推荐判定：

```
canPTZ = buildHasMotorEndpoint
      && streamConnected
      && vendor == "cs2"                  // 0x112 只在 CS2 家族有证据；tutk 走另一套 raw
      && model ∈ ptzVerifiedModels        // 证据表，不是品牌
```

`ptzVerifiedModels`（初期）：`029a02`、`039a04`、`039c04`、`c01a01`。用户机 `061a01` 不在表内 → 首次给一个显式选项「这台是云台摄像机，尝试转向」；用户确认后允许拖动并把错误（HTTP 失败/画面无变化）如实提示。**绝不**默认向未知型号发电机命令，更不宣传“全机型支持”。

`ptz-camera` 属性不可作门控：`061a01` 没有该属性却明确是云台。

扩展机制（新增一个型号或协议族时）：

- 数据文件（沿用 `Resources/deviceProfiles.json` 风格）记录 `model → {transport, ptzOps, stopOps, evidence}`；未收录 = 未知。
- 新协议族只实现两种抽象之一：`move(step)`（有界步进）或 `move(continuous)+stop`（必须带服务端租约/看门狗）。
- 任何“按品牌/系列批量放行”的规则都需要同族反例审查（CW500 就是反例）。

---

## 5. 既有 CS2 中继 gate 对 PTZ 的影响

事实（当前代码）：

- 中继 `spawnCS2UDPRelay` 只做 UDP 转发；**应用→摄像机方向不拦截**，所以 `0x112` 请求能正常到摄像机。
- 摄像机→应用方向有一个 `CS2CommandChannelGate`：每个会话只放行前 6 条 channel-0 消息，其余**代为 ACK 后丢弃**（`XiaomiCameraBridge.swift:1100-1130`）。原因是上游 go2rtc 只读一次 channel-0，10 槽缓冲约 23s 撑爆会丢整条 P2P 会话（#97）。
- 电机响应 `0x113` 走 channel 0，登录回复之后的响应因此会被 gate 丢掉。

影响：

| 用法 | 是否受 gate 影响 | 说明 |
| --- | --- | --- |
| 开环方向步进（PR #2531 的 `Motor()`） | 否 | 只发请求，不读响应 |
| ONVIF `ContinuousMove`/`Stop`（PR #2531） | 否 | 基于发送与取消循环 |
| 位置反馈 / 绝对定位 / 自动回中（bkulpa） | **是** | 需要持续读 `0x113`；gate 会丢 |
| 未来想在应用里显示云台角度 | 是 | 同上 |

若采用 PR #2531（开环），**gate 保持现状即可**，不需要改中继。若以后要位置反馈，必须同时：让 go2rtc 持续读命令通道（fork 已做）+ 放宽/移除 gate，否则响应到不了 go2rtc；放宽后要重新处理缓冲区溢出。

---

## 6. 最小可复现的 go2rtc 固定方案（推荐）

### 6.1 补丁

基座：`v1.9.15`（应用当前下载并 SHA-256 校验的版本）。
补丁：PR #2531 的两个 commit（已实测可零冲突 cherry-pick）：

```
git clone https://github.com/AlexxIT/go2rtc
git checkout 7c05066c90ab6ad1fccb506d5f0408aa31ebe60f   # v1.9.15
git remote add pr https://github.com/bcamarneiro/go2rtc
git fetch pr xiaomi-ptz
git cherry-pick 1a056d7c29 e23a21b9ee
go build -trimpath -ldflags "-s -w" -o go2rtc .
```

产物：`go2rtc_mac_arm64.zip` / `go2rtc_mac_amd64.zip`（Windows 另出 `go2rtc_win64.zip`）。许可证 MIT，保留版权声明。

### 6.2 发布与升级路径（当前应用是“下载官方 release”）

- macOS：`XiaomiCameraBridge.downloadGo2RTCBinary` 固定 `AlexxIT/go2rtc v1.9.15` + 两个 SHA-256；只有文件不存在时才下载。→ 需要改成 **自有构建的固定 URL + 新校验和 + 构建标记检查**（例如 `--version`/能力探针），并强制升级已存在但无电机接口的旧二进制。
- Windows：`windows/src-tauri/src/camera.rs` 同样固定官方 v1.9.15 + SHA-256，需要同步换 pin。
- 资产可放 GitLab release（origin 在 GitLab）或随安装包内置；不能依赖个人 fork 的临时分支。

### 6.3 应用侧接口（已实现）

- 能力探针：`GET /api/xiaomi/ptz`（补丁 405 / 官方 404），不发设备动作。
- `POST /api/xiaomi/ptz` JSON（Basic auth 用现有 `Go2RTCSession`；仅 loopback、POST、无 `Origin`）：
  - `begin` 由桥单调分配 `session` 并返回进程级 `epoch`；
  - `move`/`heartbeat` 携带 `epoch/session/seq`，`seq` 不递增的迟到包被拒；
  - `stop` 带 `epoch/session`；每条流有永久单调高水位 `stoppedThrough`，`session <= 高水位`
    的迟到 move 永久拒绝；未收到 move 的 `begin` 被 stop 也留下 tombstone；桥重启换 epoch，
    旧 token 全部失效。
- 应用每 250ms 续租；桥端 700ms 未续租即停；单次按住 8s 硬上限；`stop` 响应
  `stopConfirmed` 恒为 false，写失败返回 `stopSent=false` + `stopError=write_failed`。
- 所有响应带 `X-MenuBarMijia-PTZ: v1`；应用 probe 同时校验 `405` 与该 header，未来 schema
  变化时 fail-closed。`build.sh` 写 patch-sha manifest，`build_app.sh` 发现 stale out 会重建，
  并校验双架构、路由与 header。
- 步进型（029a02 / CW400 / CW500）桥端每 380ms 重发一步；连续型（C300/C400）只发一次方向；
  未分类（含用户 061a01）每次按住只发一次方向并必发 stop。
- 旧的官方 go2rtc：探针 404 → 不显示拖动；CS2 中继继续启用原有 6 条截断保护，视频行为不变。

### 6.4 与另外两条路的成本对比

| 方案 | 代码量/依赖 | 真机可验证性 | 主要风险 |
| --- | --- | --- | --- |
| **A. 固定补丁 go2rtc（推荐）** | Go 补丁已存在（10 文件/+594）；应用侧 HTTP + 手势 + 状态机 | 社区已在 3 个型号验证；本次已验证补丁可构建、测试通过、接口存在 | 需要自有二进制发布与升级链路；用户机 061a01 仍需实测 |
| B. 应用内重写 CS2/加密 + 独立会话 | Swift 新建 ChaCha20/HSalsa20/CS2 帧/登录/命令，约 600–900 行 | 只能靠真机，无法本地验证 | **PR #2531 作者实测：第二条 P2P 会话会断开视频**；安全敏感；型号差异 |
| C. 走 Home Assistant PTZ | 复用 HA 服务调用 | 取决于用户 HA 里的集成 | 米家摄像头没有 HA camera/PTZ 平台；用户当前后端是米家云端；对 061a01 无效 |
| D. 只做手势不做协议 | 小 | 不解决转动 | 不满足目标 |

---

## 7. 停止 / 失联 / 无 ACK 边界（必须实现的行为）

前提：采用 A（CS2 有界步进）。以下场景逐条定义；标 **推断** 的需真机确认。

| 场景 | 期望行为 | 依据/风险 |
| --- | --- | --- |
| 拖动中松手 | 立即停止发步进；取消待发；在途请求不再重试 | 应用侧状态机 |
| 拖出窗口 / 失焦 / Esc / 关窗 / 切设备 / 重连 | 同“松手”，并清空节流计时器 | 面板生命周期 `stopStreamTasks` / `closePanel` |
| App 崩溃或被强杀 | 不再有请求；残留在途最多 1 步 | CS2 步进被实测为有界（029a02）；**其他型号推断** |
| go2rtc 进程崩溃 | 同 App 崩溃；流断开 | 同上 |
| 摄像机断网/离线 | `POST` 失败；不重试风暴；提示“画面未就绪” | 接口要求 source connected |
| 无 ACK | `HTTP 200` 只代表“写入会话”，不代表“已转动”；不显示成功文案 | PR 不读 `0x113`；响应还会被 gate 丢弃 |
| 连续按住过久 | 应用侧 6s 上限后停止发步进，需重新按下 | 防跑飞；服务端 ONVIF 另有 10s 上限 |
| 停止失败（未来若用连续移动） | 重试一次 stop，仍失败则报错并停止后续移动 | 当前步进方案无显式 stop |
| 摄像机到限位 | 命令可能无效但无害；不要连续报错 | 物理限位 |
| 两台摄像机/两窗口 | 每流独立；同一流只允许一个拖动所有者 | 会话唯一 |
| 旧 tutk/dafang（若未来支持） | **必须**显式 stop + 服务端看门狗；进程崩溃会导致持续转动 | gn1024 实测“必须 stop，否则转到限位” |

结论：**只暴露“有界步进”路径**；不要在应用或补丁里引入“发一次连续移动、依赖客户端记得 stop”的 tutk 语义。

---

## 8. 未决与真机验证清单（按顺序）

1. 在用户 `chuangmi.camera.061a01` 上读取 `miss_get_vendor` 的 vendor（只读，需用户授权/登录取值），确认 cs2 还是 tutk。
2. 用补丁版 go2rtc 发一次 `operation=3`（上），观察画面是否移动、是否一次后自停；再发反向恢复。若一次后不停，立即停用并评估显式 stop。
3. 验证 5 步连发与松手停止；记录单步像素位移，用于拖动灵敏度。
4. 验证 App 强杀/关窗后的残留动作（应为 0 或 1 步）。
5. 在 C500 Pro / 069a01 / 039c01 等云台型号上重复 2–4，扩充 allowlist。
6. 验证 Windows 端（同一补丁二进制 + Tauri 调用）后再谈跨端支持。

---

## 9. 本次实现（隔离 worktree，未发布）

| 层 | 文件 | 内容 |
| --- | --- | --- |
| go2rtc 补丁 | `scripts/go2rtc-ptz/patches/0001-xiaomi-ptz.patch` | 基座 `v1.9.15`（`7c05066`）；同会话 `0x112`、TUTK dafang raw、命令通道排水、大端修正、尾 NUL、可中断 ACK 等待、租约端点 |
| 构建/复核 | `scripts/go2rtc-ptz/{build,verify}.sh`、`README.md` | 可重现构建（arm64/amd64 通用 + Windows）、`go test -race`、许可证与证据说明 |
| 应用打包 | `scripts/build_app.sh` | 默认官方桥；`PTZ_BRIDGE=1` 时构建/校验双架构补丁桥并 fail-closed 打包；`SKIP_RESTART=1` 同时禁止 killall/open |
| 领域模型 | `Sources/MijiaCore/CameraPTZ.swift` | 能力证据表（CS2 step/continuous/unknown、TUTK dafang）、拖动规划器（阈值/轴锁/心跳/8s cap/松手 stop） |
| 桥客户端 | `Sources/MijiaCore/CameraPTZClient.swift` | loopback HTTP + Basic auth；拒绝时只暴露通用错误，不回显响应 |
| 桥接线 | `Sources/MijiaCore/MiCloud/XiaomiCameraBridge.swift` | 端点探针、能力门控、按桥版本决定 CS2 gate 是否截断命令通道 |
| 流绑定 | `Sources/MijiaCore/Models.swift`、`MiCloudProvider.swift` | `.liveMP4` 携带 `CameraPTZBinding`（model/vendor/stream）；未知型号不携带 |
| 交互 | `Sources/MenuBarMijiaApp/CameraPTZDragSurface.swift`、`CameraLivePanelController.swift` | 按住拖动、双击缩放保留、窗口标题栏仍可拖动、松手/失焦/关窗/切设备/断流/MJPEG 回退/PiP 收尾、失败提示；一在途 + 一最新意图合并 |
| 测试 | `Tests/MijiaCoreTests/CameraPTZ{Tests,ClientTests}.swift`、`pkg/xiaomi/miss/{motor_test.go}`、`pkg/xiaomi/miss/cs2/conn_test.go`、`pkg/tutk/conn_ptz_test.go`、`internal/xiaomi/{ptz_test,audit_repro_test}.go`、`Tests/Fixtures/camera_ptz_models.json` | 方向/阈值/轴锁/心跳/cap/迟到 move/token+高水位/租约到期/stop 最后发出/stop 写失败/真实 loopback socket 丢 ACK 抢占/端点安全/老桥兼容/型号表一致性 |

本机验证（2026-11-07）：

- `bash scripts/go2rtc-ptz/verify.sh`：补丁零冲突，`go test -race ./internal/xiaomi ./pkg/xiaomi/miss ./pkg/onvif` 通过，`go build ./...` 通过；
- `bash scripts/check.sh`：安全扫描、机械 lint、Swift 测试（含快照）通过；Windows/Raycast 因未安装 node_modules 跳过；
- 未做真机转向：`chuangmi.camera.061a01` 的运动模式与 `-1001` 行为仍需用户设备确认。

## 10. 证据来源（均为一手页面/源码/真机报告，2026-11-07 访问）

- miot-spec 产品页（JSON-LD 名称/model）：`https://home.miot-spec.com/p/chuangmi.camera.061a01`（另核对了 `069a01`、`061a03`、`039c04`、`72ac1`、`c01a01`、`029a02`、`hlmax`、`500dh`、`df3`、`isc5c1`、`021a04`、`039c01`、`ipc019`）
- miot-spec 规格：`https://miot-spec.org/miot-spec-v2/instance?type=urn:miot-spec-v2:device:camera:0000A01C:chuangmi-061a01:3`
- python-miio issue #1959（型号/名称/固件）：`https://github.com/rytilahti/python-miio/issues/1959`
- go2rtc 已知机型表（视频 transport）：`https://github.com/AlexxIT/go2rtc/issues/1982`
- go2rtc PTZ issue（dafang/CW500 真机、fork 指引）：`https://github.com/AlexxIT/go2rtc/issues/2162`
- PTZ 上游 PR（唯一会话、0x112、10s 封顶、029a02 实测）：`https://github.com/AlexxIT/go2rtc/pull/2531`
- korasino fork：`https://github.com/korasino/go2rtc/tree/feat/xiaomi-miss-ptz`（`95d32a9c85`、`e746789ecb`）
- bkulpa fork（位置/绝对/回中）：`https://github.com/bkulpa/go2rtc/tree/feat/xiaomi-auto-center`（`c88e1c98`、`a23db132`）
- 官方 miloco 摄像头清单（厂商/名称）：`https://github.com/XiaoMi/xiaomi-miloco/blob/main/backend/miot/src/miot/configs/camera_extra_info.yaml`
- 独立实现（CW400/CW500 步进证据、大端/排水说明）：`https://github.com/lubos-hnanicek/xiaomi-camera-viewer`（`bridge/internal/miss/client.go:284-315`、`bridge/NOTICE.md:27-44`）
- 本仓库既有调研：`docs/research/issue-97-camera-support.md`、`docs/research/miot-unsupported-categories.md`

### 附：公开 MIoT 规格全量扫描（2026-11-07）

`https://miot-spec.org/miot-spec-v2/instances?status=all` 共 48,191 条实例，筛出 camera/cateye/video-doorbell 1,612 个唯一 type 并逐个拉取规格：

- 只有 2 个（`careco.camera.650` 两个版本）带 `ptz-control` 方向动作，入参是未定义编码的 `string`；
- `xiaovv` 的 `hs-ptz-extend` 有状态/位置/校准，没有方向动作；
- `camera-control.ptz-calibrate` 127 个、`camera-control.ptz-camera` 108 个，均非方向控制；
- 结论：**MIoT 云端无法转动小米云台**，必须走私有 P2P。
