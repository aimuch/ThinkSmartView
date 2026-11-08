# MenuBarMijia

macOS 菜单栏智能家居控制中心，将群晖 Home Assistant 或米家云端直连接入的设备合成为符合 Apple Control Center 交互范式的桌面面板。

## Language

### 核心领域实体

**SmartDevice（逻辑设备）**：
面板中展示与操控的最小设备单元（如一台空调、一盏吸顶灯、一台双开门冰箱），由同一物理设备下的多个 Home Assistant 原始实体或 MIoT 服务属性聚合而成。
_Avoid_: Entity（原始实体）、Widget（小组件）、Accessory（配件）

**RawEntity（原始实体）**：
底层数据源暴露的单点状态或动作寻址接口（如 Home Assistant 的 `light.*`、`sensor.*_temperature` 或米家云端的 `miot:<did>:<siid>`），仅存在于数据同步与命令下发边界，不直接暴露给用户界面。
_Avoid_: Device（设备）、Card（卡片）

**DeviceSynthesis（设备合成）**：
将 Home Assistant 注册表/状态表或米家云端设备列表/MIoT 属性值，按物理归属与功能优先级折叠为 `SmartDevice` 列表与 `EnvironmentReading` 的纯函数转换过程。
_Avoid_: Data mapping（数据映射）、Parsing（解析）

**HomeBackend（连接后端）**：
驱动 `HomeStore` 的底层数据源配置，支持局域网 `Home Assistant`（`HAWebSocketProvider`）与免网关的 `米家云端直连`（`MiCloudProvider`）；当 macOS 客户端同时配置并登录两端时自动并行连接并合并设备，仅配置一端时单源运行，演示模式由 `DemoHomeProvider` 提供。Windows 采用互斥数据源选择，HA 通过原生 `ha_snapshot`、`ha_execute` 与 `HomeAssistantClient` 接入，不进行双源合并。
_Avoid_: Server type（服务器类型）、Mode（模式）

**RaycastSnapshot（伴生快照）**：
启用「Raycast 集成」后由 `MenuBarMijia` 写到 `~/Library/Application Support/MenuBarMijia/raycast-snapshot.json`（`0600`）的只读设备、场景、快捷动作、环境读数与 `lastCommandFeedback`，供 `raycast/` 扩展、`scripts/mijia` CLI 与 `scripts/mijia-mcp` 本地 MCP 服务搜索与展示，伴生端因此不需要接触米家或 Home Assistant 凭据。字段随 `RaycastSnapshot.currentSchemaVersion` 演进，改名即破坏契约。
_Avoid_: 缓存文件、数据库

**BridgeToken（伴生令牌）**：
本机生成、随伴生快照一起写出的随机令牌。`menumijia://` 链接带上它就跳过外部链接确认，比较为常量时间；关闭「Raycast 集成」即作废，再次开启重新签发。它只证明请求来自本机已授权的伴生端，不改变高风险操作仍需二次确认与锁屏拦截的规则；MCP 服务在进程内部用它拼装链接，但向模型返回的资源与工具结果一律剥离该字段。
_Avoid_: API Key、密码

**LocalNetworkEndpoint（局域网端点）**：
流量不离开本机或家庭网络的服务地址：回环地址、私有网段、Tailscale 网段、`.local`/`.lan`/`.home.arpa`/`.internal` 后缀，以及不带点的单段主机名（两个字母或 `xn--` 开头的国家顶级域除外，它们可能自己解析到公网）。凭据（HA token、AI key）只发往 https 地址或局域网端点；Home Assistant 的其余明文 http 地址须由用户逐个同意明文传输后才能使用。
_Avoid_: Intranet（内网）、Trusted host（可信主机）

**DeviceDeduplication（跨源设备去重合并）**：
当 Home Assistant 与米家云端同时生效时，按硬件标识（`xiaomi_home` 的 `<server>_<did>`、裸 `did`、MAC 地址）及同房间唯一同款/同名规则识别同一台物理设备，并按「在线状态优先 → 可控能力评分优先 → 同分 Home Assistant 优先」选出单一胜出数据源驱动该 `SmartDevice` 卡片的纯函数合并过程。
_Avoid_: Cloud sync（云同步）、Entity bridge（实体桥接）

**MiSession（米家云端会话）**：
通过小米账号二维码扫码登录获取并持久化在钥匙串中的会话凭证（含 `userId`、`ssecurity`、`serviceToken` 与区域），用于签名与加解密 `/app/*` 请求。这里的 RC4 是小米云 `api.io.mi.com` 私有接口的**传输层**加密（`MIOT-ENCRYPT-ALGORITHM: ENCRYPT-RC4`），不是端到端加密：指令与状态经小米服务器中转，服务器可读明文。
_Avoid_: Token（令牌）、Cookie（网页凭据）、End-to-end encryption（端到端加密）

**MiAccount（米家账号）**：
一个已登录的小米账号在某个云端区域下的凭据集合，身份由区域与 `userId` 派生，同一账号在同一区域重复扫码只刷新凭据。一个安装可同时保存多个 `MiAccount`，各自使用独立区域与云端主机；设备按所属账号合并展示，面板在账号多于一个时将全屋视图下未归入常用或自定义分组的设备按账号分组。
_Avoid_: Session（会话）、Profile（账号配置）、Home（家庭）

**MiotSpec（MIoT 设备规格）**：
由 `miot-spec.org` 定义的设备型号能力描述模型（含服务 `siid`、属性 `piid`、动作 `aiid`、取值范围与枚举列表），作为米家云端设备合成与指令映射的元数据契约并按型号缓存于本地。
_Avoid_: Schema（模式）、Profile（配置表）

**MiotFacet（通用控件）**：
按「格式 + 读写」从 MIoT 规格自动合成的辅助控件（开关、下拉、滑块、读数、按钮），带 `siid.piid`（属性）或 `siid.aiid`（动作）稳定标识、写入风险等级与高价值标记。没有专用卡片的设备靠它保留可控项；噪声服务与属性、高价值名单、低风险写入白名单都存于双端共读的 `deviceProfiles.json`。
_Avoid_: Widget（小组件）、Generic control（泛指控件）

**MoreSettings（更多设置）**：
详情抽屉里的辅助控件区，由 `FacetControl` 渲染：默认折叠，高价值控件在前，超出的次要项再折叠一层。主卡片已经展示的属性与主服务动作不重复出现；低风险白名单之外的写入显示为禁用，需在设置里显式打开。
Home Assistant 的 `switch`、`select`、`number`、`button`、`sensor`、`binary_sensor` 实体也按域生成辅助控件，写入走实体所在域的 service，不经过 MIoT。
_Avoid_: Advanced panel（高级面板）、Extra controls（额外控件）

**RoomArea（房间区域）**：
物理空间的归属划分（如客厅、书房、卧室、次卧、厨房），以 Home Assistant 的区域注册表或米家家庭房间列表为准并经过名称归一化。
_Avoid_: Zone（防区）、Group（分组）

**CoverDevice（开合类设备）**：
由电机开合或升降、带位置状态的设备，按风险分为四个子类型：窗帘类（窗帘、百叶、遮阳帘）、开窗器、车库门与大门、晾衣架。窗帘类开合都直接执行；开窗器打开须确认；车库门与大门开、关都须确认；判断不出子类型时按开窗器对待。电机报告 Opening/Closing 时卡片视为运动，点卡片先停下；否则关着就打开、开着就关上。一台电机带多个窗帘服务（双轨的布帘与纱帘）时按服务拆成多张卡片，每张卡片只控制自己那条轨。
_Avoid_: Window（作总称时）、Curtain（作总称时）

**HumidifierDevice（加湿器卡片）**：
Home Assistant `humidifier` 实体合成的卡片，暴露电源、模式与目标湿度；当前湿度优先取实体自带的 `current_humidity`，否则取同设备的湿度传感器。
_Avoid_: Humidifier fan（加湿风扇）

**ValveDevice（阀门卡片）**：
Home Assistant `valve` 实体合成的开 / 关卡片；打开水阀或燃气阀须确认，关闭直接执行，默认不参与「全关」。
_Avoid_: Switch card（开关卡）

**ApplianceDevice（家电卡片）**：
由同一份 MIoT 规格驱动的通用家电卡片，按「规格里有才显示」暴露电源、模式、目标温度、状态、故障、剩余时间与无参的开始 / 暂停 / 停止动作；目标温度的范围与步长取自规格，当前温度作为主读数。热水器、洗衣机、烘干机、洗碗机共用该形状：状态取自 `status`，机器工作时由更具体的 `run-status` 阶段词取代，暂停 / 完成 / 保管 / 故障等终态压过滞留的阶段词，未归类的状态词按规格原文显示，已知状态词按名字归类、容忍拼写差异，`left-time` 按规格单位（分钟 / 秒 / 小时）折算成分钟。Home Assistant 的 `water_heater` 实体复用同一张卡，模式与温度范围取自其实体属性。浴霸与油烟机的主功能不在同名服务里，由 `deviceProfiles.json` 的 `appliances` 表声明主服务（浴霸的 PTC 暖风、油烟机的排风）与副服务（`fan-control`、`light`、`environment`），合成时把功率、模式、目标温度、风量、灯光与室温读数按语义并入同一张卡；副服务的灯只作为卡内开关，不再冒充独立卡片。打开 / 开始须确认，关闭 / 暂停 / 停止直接执行；默认不参与「全关」。
_Avoid_: Washer card（洗衣机专用卡）、Kitchen appliance（厨房家电专用卡）

**GenericDeviceCard（通用兜底卡片）**：
米家云端规格里没有专用合成器、但通用控件层（`MiotFacet`）仍能列出可写控件或只读读数的设备所显示的卡片。卡片只保留名称与房间、最多两个关键读数，以及一个低风险可写的 `on` 主开关；其余控件进入详情抽屉「更多设置」。设备家族可以覆盖读数（鱼缸取水温、喂食器取余粮），列表型读数命中告警词时（水泵低水位 / 堵塞 / 故障、粮仓已空、滤芯需更换）会在卡片副标题与抽屉顶部显示红色提示。没有任何可写 facet 的设备标为「只读」，不提供写控件。门锁、加热类与路由器配置默认只读，开启 `allowAdvancedFacetWrites` 需二次确认；通用卡片默认不参与「全关」与助手批量指令。
_Avoid_: Unsupported card（不支持卡片）、Fallback tile（兜底磁贴）

**FamilyOverride（设备家族覆盖）**：
`deviceProfiles.json` 的 `overrides` 用来给一族设备补充卡片规则：按型号通配符（`*.fishbowl.*`）或 MIoT 类型（`fish-tank`、`pet-feeder`）匹配，指定主卡片读数、高亮项、投喂动作与「今日已投喂」读数。高亮项是优先顺序表，每项只标记第一个命中的控件，因此带两个滤芯服务的机型不会把八个名额用在同一类控件上。噪声服务的例外（`keepInNoisyServices`，如把水温告警从 `temp-custom` 里捞出来）与告警词表同样保存在这里，由 macOS 与 Windows 共读。
_Avoid_: Model table（型号表）、Hardcoded card（写死卡片）

**FeedAction（按份投喂）**：
鱼缸与宠物喂食器的 `pet-food-out` 动作带一个投喂份数输入。抽屉把份数做成可选值（取规格的 `value-range`，如 1–4 份），点击后先弹二次确认，确认文案写明份数与「今日已投喂」读数；该动作不受「允许次要写入」开关限制，因为二次确认本身就是闸门。投喂不可撤销，不产生撤销横幅。同一规格里重复出现的同名动作只有第一个进入抽屉，其余保持原有卡片行为。
_Avoid_: Snack button（零食按钮）、Quick feed（快捷投喂）

**HomeOption（米家家庭）**：
小米账号下的独立家庭空间（由 `homeroom/gethome` 的 `homelist` 返回，含家庭 ID、名称与去重设备数），支持在面板顶部切换查看单个家庭或合并查看全部家庭。
_Avoid_: Workspace（工作区）、Account（账号）

**SplitDevice（多键分键设备）**：
多键 Mesh 墙壁开关等复合设备由 `v2/home/device_list_page` 拆分出的独立按键记录（`did` 形如 `<parentDid>.s2`，携带 `splitParentDid` 与 `splitSiid`）；合成时隐藏父面板并以各分键作为独立 `SmartDevice` 展示，MIoT 读写仍寻址父 `did` 与对应 `siid`。
_Avoid_: Sub-entity（子实体）、Virtual plug（虚拟插座）

**EnvironmentReading（环境读数）**：
从在线温湿度计、带内置温湿度的空气净化器/风扇以及带当前室温读数的空调/电暖器提取，按「独立传感器 → 空气净化器 → 空调室温」优先级排序并展示在面板顶部与菜单栏的实时温湿度指标。
_Avoid_: Weather（天气）、Telemetry（遥测）

**WeeklyHealthReport（家庭健康周报）**：
只用本机已有数据算出的每周摘要：本周与上周用电对比及耗电最多的插座、各传感器一周温湿度区间与超出舒适区间的提醒、快用完的电池（≤20%）和滤芯（≤10%）、生成时离线或本周有 2 天以上出现离线的设备（离线天数由 `OfflineHistory` 按天记录，保留 14 天）。可导出为 Markdown，也可压成一行通知（#182）。
_Avoid_: Analytics（统计）、Telemetry（遥测）

### 控制与偏好

**Scene（场景）**：
在 Home Assistant（或由米家同步至 HA）中定义的一键多设备联动动作（`scene.*` / `script.*`），在菜单栏面板中仅触发执行，不在本地维护编排逻辑。
_Avoid_: Automation（后台自动化规则）、Macro（宏）

**QuickAction（内置快捷动作）**：
基于设备类别自动聚合的批量领域指令（如「全屋关灯」「柔和夜灯」「睡眠关温控」），无需在 Home Assistant 预置场景即可一键执行。执行后按设备数给出撤销横幅：1–2 台 10 秒、3 台及以上 15 秒（`HomeStore.undoBannerSeconds(forDeviceCount:)`，指针悬停时暂停）；横幅消失后，助手的最后一次动作仍可用「撤销上一步」在 10 分钟内回滚。
_Avoid_: Local scene（本地场景）、Custom script（自定义脚本）

**AllOffTarget（全关目标）**：
「全关」实际会关闭的设备集合（`HomeStore.allOffTargets`）：当前视图内、开启中、且能从面板关闭的设备。默认不参与全关的类别（插座、扫地机、摄像机，`joinsAllOffByDefault` 返回 false）可由用户按设备覆盖（`setJoinsAllOff`），覆盖结果持久化；通用兜底卡片没有全关命令（`allOffChange` 返回 nil），因此既不默认参与，也无法通过覆盖加入。
_Avoid_: All devices（全部设备）

**DeviceListCut（房间或类型筛选）**：
当前家庭设备列表上的一次性筛选。同一时刻只能是全部设备、一个房间，或一种设备类型（灯、空调、插座等，各房间合在一屏）。不写入磁盘。选中类型时 `selectedRoomId` 仍为空，常用、分组、房间角标和磁贴尺寸继续按全部房间那一页计算。点「全部」、点某个房间，或切换家庭，会取消类型筛选。
_Avoid_: IntentDeviceCategory（意图设备大类）、Persistent filter（持久筛选）

**IntentPlan（意图执行计划）**：
自然语言指令经本地规则解析（`LocalIntentParser`）或远端大模型回退（`ModelIntentResolver`）后生成的结构化设备操作列表（`[DeviceIntent]`）与简短反馈文案，经能力校验后由 `HomeStore` 并发下发并支持多设备一键撤销。
_Avoid_: Prompt output（模型输出）、Action script（动作脚本）

**BareCategoryCommand（裸类别指令）**：
只说了设备类别和动作、没有指明房间、设备名或「全部」等范围的指令（如「关灯」「打开空调」）。关闭时只针对开着的同类设备：0 台如实回复，1 台直接执行，多台时取面板正在显示的房间，否则给出候选；打开时同类设备只有 1 台就按原样直接打开，多台时只针对关着的，取面板正在显示的房间，否则按当前时段习惯给出最多 3 个候选并附「全部打开」。打开候选里的高风险设备仍按手动控制规则二次确认。快捷指令、URL 这类无界面入口遇到歧义时只回复、不执行。
_Avoid_: Ambiguous command（模糊指令）、Whole-home command（全屋指令）

**ScheduledCommand（定时指令）**：
带有绝对触发时刻 `fireDate` 与已解析 `IntentPlan` 的延时控制任务，持久化在本地 `UserDefaults`（`assistant_scheduled_commands_v1`）。应用重启时未到期项按剩余时间继续倒计时；退出期间已过期项转入 `missedScheduledCommands` 待确认队列并保留在磁盘，必须由用户点击「立即执行」或「忽略」后才执行或移除，绝不在后台静默触发过期硬件动作。
_Avoid_: Background job（后台任务）、Cron（计划任务）

**ActiveDevice（活跃开启设备）**：
当前在线且处于工作状态的受控设备（开着的灯、正在制冷/制热的空调或电暖器、运行中的风扇、通电开启的插座、清扫中的扫地机、播放中的音箱、监控中的摄像机）。顶部「全关」只作用于其中参与全关的 AllOffTarget；插座、扫地机、摄像机等常年通电或看护设备默认不计入全关目标（冰箱、待机电视、纯传感器和处于打开位置的 CoverDevice 都不算活跃设备）。
_Avoid_: Online device（在线设备）

**CompactTile（双列紧凑磁贴）**：
每行并排展示两张设备的紧凑网格视图；当任意设备展开详情抽屉时，该卡片自动切换为跨满整行宽度以容纳滑块与模式按钮。
_Avoid_: Mini card（迷你卡片）、Widget（小组件）

**SafetyAlert（主动守护提醒）**：
macOS 原生通知。原有四类仍按原规则提醒：插座持续开启 45 分钟且**实时功率超过 300 W**（没有功率读数就不触发）、空调或电暖器连续运行 4 小时、传感器或门锁电量低于 15%、星标设备意外离线。低电量同一设备 24 小时内不重复，冷却时间持久化；带关闭动作的提醒可在通知上直接执行。传感器边沿提醒另计：漏水、烟雾、燃气默认开启，门窗与人体移动默认关闭，每一类都能单独开关。报警从无到有才通知一次，危险类第一次采样已是报警也会通知，正文带检测时刻；恢复平静不通知。漏水、烟雾、燃气报警期间菜单栏图标变红。门窗和移动可以限制为只在这台 Mac 锁屏、睡眠或切换用户期间提醒。Windows 没有这条传感器提醒。
_Avoid_: Alarm（警报）、Push notification（远程推送）

**ProactiveSuggestion（猜你想控推荐）**：
结合用户全渠道操作习惯记忆（`UserHabitMemory`，涵盖时段与工作日/周末频次、时间衰减与动作序列预测，见 ADR-0009）、当前环境温湿度与在线设备，立即给出最多四项当前可执行的候选动作（`AssistantSuggestion`）；配置大模型时在后台刷新。动作不足四项时显示实际数量。
_Avoid_: Static shortcut（静态快捷方式）、Random tip（随机提示）

**GrokBall（AI 情绪表情球）**：
控制面板外的球面双目动态组件，实时注视光标，并随睡眠、待机、聆听、思考、执行成功与异常状态切换表情；macOS 的默认、好奇和睡眠表情使用黑色球体与白色眼睛，支持自由拖动与长按 0.5 秒进入语音输入。轻点后最多展示四项设备 `ProactiveSuggestion`，macOS 根据球在主面板的左、右、上、下位置向外安排胶囊，并在屏幕边缘调整行列或换向；Windows 保留固定侧挂与纵向推荐。展开不改变主面板卡片布局，设备推荐支持撤销，场景可通过语音或文本指令执行。
_Avoid_: Static icon（静态图标）、Chatbot avatar（聊天头像）

**LightGroupFold（米家灯组折叠）**：
把米家云端 `group.*` 灯组与其成员单灯合成一张卡片的展示规则：`v2/groupv2/query_status` 返回的成员归属（`member_ship`，插件 SDK 写作 `membership`）随设备列表缓存到 `SynthesizedHome.lightGroupOwners`；面板只在灯组卡片本身可见时，从 `HomeStore.visibleDevices` 隐藏其成员灯，`filteredDevices` 仍保留完整集合，因此全关、场景、搜索与助手继续按每盏成员灯计算。偏好「灯组内的单灯不重复显示」(`hideGroupedLights`) 默认开启。
_Avoid_: DeviceGroup（本地自定义分组）、Room grouping（房间分组）

**RoomOrderDraft（房间排序草稿）**：
一次房间排序会话里在内存中维护的完整顺序与别名（`RoomOrderDraft`）：进入会话时用 `RoomPrefs.seededOrder` 拍下完整历史顺序（含其他家庭/账号暂时不可见的房间 id），拖动、键盘与 VoiceOver 只改草稿，不写 `UserDefaults`；「完成」或任一会触发提交的隐式退出（面板隐藏、搜索、换家庭、进入设备布局、退出应用）经 `RoomPrefs.persist` 合并写回 `room_custom_order` / `room_aliases`，只有显式「取消」/`Esc` 丢弃草稿。「恢复默认顺序」只把当前可见房间恢复成 provider 顺序，保留其他家庭 id 的相对位置，且不改动别名。同一会话还带一份设备类型筛选胶囊的草稿（`kindOrderDraft`，存 `kind_custom_order`），列表顶部「房间 | 类型」切换（`OrderListTarget`），「完成」/「取消」同时结算两份。
_Avoid_: Room order（保存后的顺序值）、Layout edit（设备布局编辑）

**PanelEditMode（面板编辑模式）**：
面板唯一的三态编辑状态（`none` / `deviceLayout` / `roomOrder`），保存在共享的 `LayoutUIState` 上，因此面板视图与全局键盘监视器读到同一个值。进入任一模式时按自身规则收尾另一个会话：进入设备布局会先提交房间排序草稿。排序模式的 `Return` 提交退出、`Esc` 取消回滚，都不会落到「开关聚焦设备」或「关闭面板」。
_Avoid_: isEditingLayout / isEditingRooms（已被本枚举取代的两个布尔值）

**ViewPreference（本地视图偏好）**：
仅作用于当前客户端的展示配置（包括星标常用置顶、自定义分组 `DeviceGroup`、长按拖拽排序、通过编辑态卡片右下角圆弧手柄拖拽或轻点调节的分页面卡片尺寸 `TileSize`、单设备固定数据源覆盖、隐藏特定设备、隐藏离线设备、面板高度 `PanelHeight`、Windows 面板位置 `PanelAnchor`（跟随任务栏或工作区四角，保存在 `%APPDATA%\MenuBarMijia\panel-placement.json` 以便拖动窗口后即时落盘）、菜单栏常驻指标选择），存储在本地且不回写远端数据源。
_Avoid_: Device config（设备配置）、Profile（档案）
