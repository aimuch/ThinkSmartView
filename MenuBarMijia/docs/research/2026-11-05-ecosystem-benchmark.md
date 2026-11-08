# 生态对标调研：多设备支持、易用性与设计感

调研日期：2026-11-05　范围：米家 / Home Assistant / HomeKit / Matter 周边的开源项目与设计范式
方法：浅克隆并**读源码**（`al-one/hass-xiaomi-miot`、`XiaoMi/ha_xiaomi_home`、`nickustinov/itsyhome-macos`、`Do1e/mijia-api`、`merdok/homebridge-miot`、`AlexxIT/go2rtc`、`home-assistant/frontend`），对 MIoT 公开规格库（`miot-spec.org`，16,185 条 released 实例）跑脚本复算，其余靠检索。
标注：**【源码】** 读了实现；**【复算】** 跑了脚本；**【检索】** 只看了搜索摘要；**【推断】** 我的判断。

> 修订说明：本文取代同日早先版本。早先版本漏看了仓库里已有的 go2rtc 摄像头桥（`XiaomiCameraBridge.swift`），把"视频"写成了缺口，那是错的，已在 §4.2 更正。

---

## 0. 结论先行

1. **窗帘是漏注册，不是做不了。** 注册表里只有 `window-opener`，没有 `curtain`（943 个型号）。把 `buildCover` 的判定逻辑对 60 个随机窗帘规格复算：60/60 能解析开/关/暂停，54/60 有可写目标位置【复算】。
2. **晾衣架需要多补方向别名，并处理"位置反向"。** 抽样 60 个全部用 `up/down/pause`，我们的别名表只认 `open/close`，识别率 0/60【复算】。官方集成和 al-one 都把 `up→open`、`down→close`；al-one 还对晾衣架**整体反转位置语义**（§2.2）【源码】。
3. **鱼缸是多服务复合设备。** 三份真实规格都由 `fish-tank` + `light` + `feeder` + `filter` + 水温告警等服务组成，投喂是带参 action `pet-food-out`【复算】。目前整台被丢进"未显示设备"。
4. **猫眼：控制项已有，视频已有通路，真正缺的是"事件"。** 猫眼 39 个型号里 38 个有 `doorbell-ring` 事件【复算】。事件有两条可行路径（§4.2）。
5. **结构性差距：我们只有三层里的第一层。** 官方和 al-one 都是「类型化主实体 → 通用长尾兜底 → 按型号覆盖」；我们是"一设备一类型，选中主服务后其余丢弃"。这是鱼缸、喂食器、热水器、洗衣机一直补不完的根因（§3）。
6. **已有数据驱动先例**：`Resources/fallbackSpecs.json` 是 macOS 与 Windows 共读的数据表；`MiotSynthesizer.swift`（970 行）与 `synthesis.ts`（999 行）则是两份平行实现。把注册表与规则做成共享数据，可以直接缓解 ADR-0006 记录的"每个 bug 修两遍"。

---

## 1. 我们现在漏掉了多少

按小米系厂商前缀粗略归类（前缀表是我手写的启发式，**只代表数量级**）【复算】：小米系约 3,574 个型号，落在注册表之外的约 1,037 个（约 29%）。头部：窗帘 119、热水器 116、油烟机 91、路由器 84、遥控器 50、晾衣架 47、洗衣机 45、浴霸 44、网关 42、电视 39、净水器 39、洗碗机 38。全生态口径：窗帘 967、晾衣架 264、洗衣机 326、热水器 178、洗碗机 173、油烟机 146、鱼缸 20、喂食器 31、可视门铃 40。

与 [miot-unsupported-categories.md](miot-unsupported-categories.md)（2026-10-31）一致，那份路线图第 3 步（窗帘 + 晾衣架）仍未落地。

---

## 2. 窗帘/晾衣架：逐点对照（读完三份实现）

### 2.1 官方 `ha_xiaomi_home/cover.py`【源码】

- 设备映射：`curtain` 要求 `motor-control` 可写，可选 `status / current-position / target-position`；`window-opener`、`motor-controller`、`airer` 都别名到 `curtain`。**窗帘、窗户、卷帘、晾衣架共用一个 Cover 类**，只靠 `device_class` 区分图标。
- 方向别名是**名称集合**而不是枚举值：`{open, up}`、`{close, down}`、`{pause, stop}`。记录的是枚举**数值**，下发时直接写回。
- `status` 枚举用模糊词表归类，先去掉非字母字符：`opening/open/up/uping/rise/rising` → 开启中；`closing/close/down/dowm/falling/fallin/dropping/downing/lower` → 关闭中；`closed/closeover/stopatlowest/stoplowerlimit/lowerlimitstop/floor/lowerlimit` → 已关。词表里能看到真实规格的拼写错误（`dowm`、`fallin`），说明这是被现实数据打磨出来的。
- 没有 `status` 但有 `current-position` 时，**用目标位置与当前位置的大小关系推断开合中**（`_prop_pos_opening/closing`），位置一变化就清除。
- 没有 `current-position` 时，把 `target-position` 当作当前位置。
- `cover_dead_zone_width`：位置落在两端死区内就吸附成 0 或 100，避免显示 1% 或 99%。

### 2.2 al-one `cover.py`【源码】

在官方逻辑之上多了一层"现实世界的补丁"，值得逐条记下：

| 机制 | 做法 |
| --- | --- |
| 晾衣架识别 | 看型号/URN 里是否含 `airer`，命中则 `position_reverse = True` |
| 位置反转 | 读出时 `100 - val`，写入时 `range_max - position`；`open` 在反转时对应 0 |
| 已关语义反转 | 晾衣架到达下限算"已关"的反面：到 0 时 `closed = not is_airer` |
| 状态词 | 额外认 `Rising/Falling/Dropping/Stop Lower Limit/Stop At Highest/Ceiling`，并随 `position_reverse` 翻转开/关 |
| 死区 | `deviated_position` 默认 2：≤2 吸附为 0，≥98 吸附为 100 |
| 电机方向反了的型号 | `motor_reverse` 开关，交换 open/close 词表；`device_customizes.py` 里有 6 个型号开启它 |
| `current-position` 是枚举而非范围 | 通过 `cover_position_mapping` 做值映射 |
| 某系列电机控制不可用 | `'090615.curtain.*': exclude_miot_properties: motor_control` |
| 没有 `current-position` | `target2current_position`，同官方 |

### 2.3 我们的 `buildCover`【源码】

| 项 | 现状 | 对照 |
| --- | --- | --- |
| 触发服务 | 仅 `window-opener` | 缺 `curtain`、`motor-controller`、`airer` |
| 方向词 | 精确匹配 `open/close/pause/stop/打开/关闭/暂停`，注释说明是为避免 "Not Open" 被子串匹配成开启 | 缺 `up/down`；**精确匹配这个决定是对的**，官方也是集合精确匹配，不要改成子串 |
| 状态 | 只读 `device-fault`，没有开合中状态 | 官方/al-one 都有 opening/closing/closed；我们的卡片无法显示"正在打开" |
| 死区吸附 | 无 | 两家都有 |
| 晾衣架反转 | 无 | al-one 有 |
| 位置缺失 | `current ?? target` | 一致 |
| 电池 | 读 `battery` 服务 | 两家没有，这是我们的加分项 |
| 型号怪癖 | 无覆盖通道 | al-one 有 6 类开关；我们需要一个数据化的覆盖层 |

**结论**：补 `curtain` 注册 + `up/down` 别名是 15 分钟的事；但要让窗帘/晾衣架"好用"，还需要开合中状态、死区吸附、晾衣架反转，这三项在两家实现里都被证明是必需的。

---

## 3. 结构：三层模型（官方 + al-one 源码）

### 3.1 官方 `miot_device.py` 的 `spec_transform()`【源码】

分四步，**没被前一步认领的才进下一步**：

1. 设备级：按 `SPEC_DEVICE_TRANS_MAP` 的 `required/optional` 声明匹配（如 `curtain`、`water-heater`、`humidifier`）。
2. 服务级：同表，服务维度。
3. 属性级兜底，按 **格式 + 读写** 自动归类：
   - 可写 `str` → text；可写 `bool` → switch；可写且有枚举 → select；可写且有范围 → number；
   - 只读 `bool` → binary_sensor；其它只读 → sensor；
   - 既不是枚举也不是范围的可写属性（"不规则"）→ **跳过，不生成**。
4. 事件 → event 实体（`doorbell-ring` → `EventDeviceClass.DOORBELL`，`motion-detected` → MOTION）；动作无入参 → button，有入参 → notify。

规格修补层：`spec_filter.yaml`（按型号 URN 排除服务/属性，例如 `curtain:...lumi-hmcn01` 排除 `4/7/8` 号服务）、`spec_modify.yaml`、`spec_add.json`。

### 3.2 al-one 的覆盖层【源码】

`device_customizes.py` 3,914 行，键是型号或通配符：

- `'*.fishbowl.*'`：`temperature` 传感器，`water_pump` 开关，`pump_flux` 数值，`pet_food_out` 作为可选动作。
- `'hfjh.fishbowl.m100'`、`'xiaomi.fishbowl.m200'` 等：逐型号补充。
- `'*.cateye.*'`：`auto_cloud` + `use_motion_stream`。
- 可以按属性指定为 `sensor/switch/select/number/button_*` 之一，也可以 `exclude_miot_properties`。

### 3.3 homebridge-miot 的解析阶梯【源码】

`DeviceFactory.getDeviceClass` 依次尝试，**每级失败才降一级**：

1. 型号专用类（`modules/<type>/devices/<model>.js`）；
2. 按规格里的设备类型/描述识别；
3. 有规格但无专用类 → `GenericDevice`（只监控 `on/status/fault/mode/motor-control`）；
4. 什么都没有 → `UnknownDevice`。

这个阶梯是对的：**永远有一个落点**。我们现在是第 1 级失败就直接消失。

### 3.4 我们缺什么【源码】

`MiotSynthesizer.primary(for:)` 选出一个主服务后返回 `(service, builder)`，其余服务不再参与；选不出就返回 `nil`，设备进入 `UnsupportedDevice(.unknownModel)`。没有第 3 步的属性级兜底，也没有可配置的型号覆盖层。`fallbackSpecs.json` 只在**规格拉不到**时给一个只读规格，作用不同。

---

## 4. 猫眼与摄像头

### 4.1 规格事实【复算】

对 39 个猫眼/门铃型号逐个拉最新规格：

- `doorbell-ring` 事件：**38/39** 有；`someone-at-the-door`：12/39。
- 服务 `p2p-stream` 的动作：8 个型号有 `start-p2p-stream`（`xiaomi.cateye.4pro`、`midr.cateye.*`、`loock.cateye.v02/v06/v07`、`isa.cateye.hldb6`）；12 个有该服务但**动作为空**（几乎全是 `madv.cateye.*`）；19 个根本没有该服务。
- `on` 可写的只有 4 个，其余 35 个开关是只读，所以"开关"不是这类设备的主要控制项。
- 4 个型号的设备类型是 `doorbell`（`linp.doorbell.g03/g04`、`lkd001.doorbell.amlkml`、`madv.doorbell.dltxq`），不是 `video-doorbell`，注册表不认。

### 4.2 视频：已有通路（更正）【源码】

- 仓库已内置 go2rtc 桥（`XiaomiCameraBridge.swift`，1146 行）。`MiCloudProvider` 在开窗时：若规格有 `start-p2p-stream` 就先调用 → 取 LAN IP → 调 `v2/device/miss_get_vendor`（`MiCloudClient.swift:418`）→ 带密钥启动 go2rtc → 返回 fMP4/MJPEG 地址。**这个流程并不要求规格有 start 动作**，没有就跳过那一步继续往下走。
- go2rtc 用的 `sid` 是 `xiaomiio`，接口基址 `https://<region>.api.io.mi.com/app`，与我们的 `MiSession` 一致；它自己的设备筛选就包含 `.cateye.`【源码】。许可证 **MIT**【源码】。
- 所以猫眼视频能否出画面，取决于 `miss_get_vendor` 是否返回、go2rtc 是否支持该型号（它自己的说明承认旧的 `xiaomi/legacy` 格式可能有问题）。上面 12 个"服务在、动作空"的 `madv` 型号很可能落在 legacy 里。**这一点没有真机验证，不能下结论。**

### 4.3 事件：两条路径【源码 + 推断】

| 路径 | 机制 | 适用 | 状态 |
| --- | --- | --- | --- |
| **A. HA `event` 实体** | 官方集成把 `doorbell-ring` 生成为 `event` 实体，`device_class: doorbell`，每次触发状态变成新时间戳。Itsyhome 就是监听这类 `state_changed` 后弹出摄像头面板（`HomeAssistantPlatform+Delegate.swift:45-48`） | 已接 HA 的用户 | 我们已订阅 `state_changed`（`HomeProvider.swift:281`），但 `event` 域目前落入"实体类型暂不支持"。**这条几乎不需要新协议** |
| **B. 云端历史轮询** | al-one 调 `user/get_user_device_data`，`type` 取事件、带时间窗，每 120 秒拉一次 | 仅米家云直连用户 | 我们的客户端**已经**在用同一个接口（`MiCloudClient.swift:701`，用于统计）。`type=event` 的键格式和返回结构**需要真机探测** |

官方的实时推送走 MQTT，需要小米官方 OAuth 应用凭据，我们的 `xiaomiio` 会话拿不到，所以不是可选项【推断】。

---

## 5. 设计与易用性（读了实现）

### 5.1 HA 前端：功能条 = "能力谓词 + 渲染器"【源码】

`card-features/` 下有约 60 个功能条文件，如 `hui-cover-position-card-feature.ts`、`hui-cover-open-close-card-feature.ts`、`hui-climate-hvac-modes-card-feature.ts`、`hui-light-brightness-card-feature.ts`。每个文件导出两样东西：

- `supportsCoverPositionCardFeature(hass, context)`：**一个纯函数谓词**，检查实体是否具备该能力（这里是 `domain === "cover" && coverSupportsPosition(stateObj)`）；
- 渲染组件：谓词不成立就渲染 `nothing`。

磁贴只是 `身份区 + features[]`，`hui-card-features` 按配置顺序排布，支持内嵌/置底和多列。**能力在实体上，功能条在卡片上，二者靠谓词解耦。**

两个值得抄的细节：位置滑块在"关闭 / 0%"时用"开启色"，注释写明是"避免灰色滑块看起来像被禁用"；功能条高度固定 42px，保证不同卡片对齐。

对应到我们：`CoverDetailDrawer`（69 行）里"滑块 + 暂停按钮 + 电量"是手写的一个特例。把它改成"谓词 + 渲染器"的列表后，窗帘、鱼缸、晾衣架就能共用同一套抽屉，而不是每种设备一个 `*DetailDrawer`。

### 5.2 Itsyhome：自动分组是无状态合成【源码】

`AutoGroups.swift` 仅 144 行，设计很干净：

- `definitions` 是 6 项静态表（`lights/switches/blinds/acs/fans/locks`），每项只有 `key`、匹配的服务类型集合、图标；
- 分组**每次构建菜单时从设备列表合成，从不持久化**；
- 排序与可见性通过**确定性 token** 引用（`autogroup:room:<roomId>:<key>`），所以跨会话、跨语言都稳定；
- 成员 **< 2 个不生成**（"通过分组控制一个设备只是噪音"）；
- 房间级与全屋级同一套逻辑，只是输入范围不同。

对我们：`blinds` 这一项在窗帘支持后才有意义，所以窗帘支持是自动分组的前置依赖。

### 5.3 Itsyhome 的窗帘行【源码】

`BlindMenuItem.swift`：点整行 = 切换（`position > 50 ? 0 : 100`）；滑块；可选倾角行；电池徽标内联。本地改动后 **60 秒内忽略设备回传的位置**，防止窗帘还在走时滑块被中间值拉回。它是 HomeKit 特征模型，所以没有开合中状态，靠忽略窗口兜底。

我们已有 `lockOptimistic`（`CODING_STANDARDS.md` 第 2 条用 3 秒）。窗帘从全开到全关常常要几十秒，**固定时长的锁和"边走边上报中间值"会打架**；官方的做法是用 `status` 或目标/当前位置差推断"移动中"，把它当作状态而不是定时器。这是实现窗帘时需要先定的设计点【推断】。

### 5.4 其余设计参照【检索】

HA Areas 仪表盘与区域卡片的"紧凑 / 详细"两档、Mushroom 与 Bubble Card 的视觉处理、iOS 18 控制中心的可调尺寸网格、HomeKit 窗帘特征模型（Current/Target/PositionState）。这些我只看了摘要，没读源码，也没找到 Apple 官方的 Home 磁贴规范，所以不作为依据。Matter 1.4 新增类型偏能源（热水器、热泵），摘要里没有鱼缸/喂食器，这类长尾设备在 Apple Home 里基本没有对应形态。

---

## 6. 建议

### P0：低成本，先做（不依赖重构）

1. 注册 `curtain`、`motor-controller` → `buildCover`；`airer` 同样注册。
2. 方向别名加 `up/raise → open`、`down/lower → close`，保持**精确匹配**。
3. 加上**开合中状态**：优先读 `status`（沿用官方词表），没有就用目标/当前位置差推断。卡片显示"正在打开/关闭"，并替代固定时长锁。
4. 加死区吸附（≤2 → 0，≥98 → 100）。
5. 晾衣架：位置反转 + 文案显示"升/降"，别让用户看到"打开晾衣架"。
6. 复算用的 60+60 个真实规格落进 `Tests/Fixtures/miot_golden.json`，Swift 与 TS 同 PR（CODING_STANDARDS 第 6 条）。

### P1：结构重构（值得"不怕重构"的部分）

目标：从"一设备一类型"变成**解析阶梯 + 主卡片 + facets**。

- **解析阶梯**（抄 homebridge-miot）：型号覆盖 → 设备类型表 → 属性级通用兜底 → 通用卡片。永远有落点，"未显示设备"只留给真的无规格的。
- **Facet**（抄官方兜底规则 + HA 功能条）：`toggle / picker / slider / action / readout`，按"格式 + 读写"从规格自动生成；每种 facet 带**能力谓词**，抽屉按列表渲染。
- **过滤层**（抄 `spec_filter.yaml`）：必须有。鱼缸规格里 `exception-card`、`bind-timestamp`、`light-mode-list`（JSON 字符串）、`code`、`feed-time` 这类内部属性占一半以上，直接暴露就是噪音。官方对"不规则"的可写属性（无枚举无范围）直接跳过，我们也该如此。
- **型号覆盖层数据化**（抄 al-one 的通配符键 + 沿用 `fallbackSpecs.json` 的双端共享做法）：`motor_reverse`、`position_reverse`、`exclude_properties` 这类怪癖放进一份共享 JSON，Swift 与 TS 各写薄解释器，由 golden fixtures 守一致。需要新 ADR，并与 ADR-0006 对齐。
- 鱼缸在该模型下几乎无需专用代码：主卡片 = 开关 + 水温；facets = 水泵开关/档位、灯光模式/亮度、投喂（带参 action，份数 1–4）、滤芯寿命、水温告警。

### P2：猫眼

1. **HA 通道的门铃**：把 `event` 域、`device_class: doorbell` 纳入合成，把"状态时间戳变化"识别为一次响铃，触发现有 `CameraLivePanelController`。代价小，对已接 HA 的用户立即可用。
2. **云直连的门铃**：探测 `user/get_user_device_data` 的事件类型键，在真机上验证后再决定；做不了就在 README 写明边界。
3. 把 `doorbell` 设备类型的 4 个型号纳入。

### P3：体验层（互相独立）

- **按类型自动分组**，照 `AutoGroups` 的无状态 + token 设计，成员 < 2 不生成；依赖 P0 的窗帘。
- 多路摄像头网格与"附近设备"叠加按钮（Itsyhome Pro 功能）。
- 把置顶房间/设备做成独立菜单栏图标。
- Webhook / SSE 向外推送：优先级低。

### 明确不做

远程开锁（锁属性在规格里不可写）、PTZ、双向语音、miloco 的本地视觉模型（要 NVIDIA 显卡、无 macOS）、向 Apple Home 桥接。

---

## 7. 需要你拍板的两件事

1. **"智能水缸"是不是米家智能鱼缸？** 我按鱼缸调研。如果指净水器/饮水机，结论不同：它们多数无开关，主要是滤芯与水质，更适合只读 facet。
2. **P1 要不要立项。** 它跨 `Models.swift`、合成器、卡片与抽屉、Windows 端，按仓库约定走 PR + 新 ADR。P0 可单独先做，不依赖 P1。

---

## 附：局限

- 没读 Mushroom、Bubble Card 的实现；HomeKit 与 iOS 控制中心部分来自检索摘要。
- 没有任何真机验证：猫眼事件接口的键格式、`madv` 型号的视频是否可用、窗帘位置语义都需要实机确认。
- 小米系占比用的是手写厂商前缀表，只作数量级参考。
- 窗帘/晾衣架复算是用 Python 复现 `buildCover` 的判定逻辑，不是运行真实 Swift 代码。
- 克隆的仓库都是浅克隆（`--depth 1`），读的是 2026-11-05 当天的默认分支，未核对历史。

## 来源

读过源码：
- [XiaoMi/ha_xiaomi_home](https://github.com/XiaoMi/ha_xiaomi_home)：`cover.py`、`miot/miot_device.py`、`miot/specs/specv2entity.py`、`spec_filter.yaml`
- [al-one/hass-xiaomi-miot](https://github.com/al-one/hass-xiaomi-miot)：`cover.py`、`core/device_customizes.py`、`core/xiaomi_cloud.py`
- [nickustinov/itsyhome-macos](https://github.com/nickustinov/itsyhome-macos)：`macOSBridge/Models/AutoGroups.swift`、`MenuItems/BlindMenuItem.swift`、`Itsyhome/HomeAssistant/HomeAssistantPlatform+Delegate.swift`
- [merdok/homebridge-miot](https://github.com/merdok/homebridge-miot)：`lib/factories/DeviceFactory.js`、`lib/constants/DevTypes.js`、`lib/modules/*`
- [AlexxIT/go2rtc](https://github.com/AlexxIT/go2rtc)（MIT）：`internal/xiaomi/xiaomi.go`、`pkg/xiaomi/cloud.go`、`pkg/xiaomi/miss/client.go`
- [home-assistant/frontend](https://github.com/home-assistant/frontend)：`src/panels/lovelace/card-features/`
- [Do1e/mijia-api](https://github.com/Do1e/mijia-api)：`mijiaAPI/apis.py`
- [MIoT 规格库](https://miot-spec.org/miot-spec-v2/instances?status=released)：鱼缸 `xiaomi-m300 / xiaomi-m200 / hfjh-m100`，猫眼 39 个型号逐一复算

仅检索：[go2rtc 小米源说明](https://go2rtc.org/internal/xiaomi/)、[HA Areas 仪表盘](https://www.home-assistant.io/blog/2025/04/02/release-20254/)、[Mushroom](https://cdn.jsdelivr.net/gh/piitaya/lovelace-mushroom@main/README.md)、[Bubble Card](https://www.GitHub.com/Clooos/Bubble-Card)、[iOS 18 控制中心](https://www.macrumors.com/guide/ios-18-control-center)、[Matter 1.4](https://appleinsider.com/articles/24/11/07/new-matter-14-spec-supports-more-device-types-improvements-for-apple-home)、[Matter Hub 社区 fork](https://github.com/RiDDiX/home-assistant-matter-hub)、[xiaomi-miloco 技术报告](https://arxiv.org/pdf/2512.17436)
