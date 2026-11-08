# 内置米家转 Matter 桥接器（Direct MIoT-to-Matter Bridge）

MenuBarMijia 不内置 Matter 协议栈，也不把已接入的设备暴露成 Matter 网桥。限制来自网桥的在线要求与桌面应用的生命周期冲突，以及小米云这条数据源的轮询模型，和协议本身难不难写无关。要把米家设备放进 Apple「家庭」或别的 Matter 生态，用现成的桥接器，面板继续当控制入口。

本文件记录 #16（原 GitHub #190）的判定。

## Why this is out of scope

### 1. Matter Fabric 要求网桥节点 24/7 在线

Matter 网桥是 fabric 里的一个独立节点。它用自己的 operational certificate 加入 fabric，在局域网持续广播 mDNS（`_matter._tcp`）让控制器发现它，为每个子设备开一个 endpoint，并维持 CASE 会话与属性订阅。Apple 家庭把这些 endpoint 当作桥下的常驻配件，桥一掉线，整组子设备一起变成「无响应（No Response）」。

MenuBarMijia 的数据源就这几种（`Sources/MijiaCore/HomeProvider.swift:4`）：

```swift
public enum HomeBackend: String, CaseIterable, Identifiable, Sendable {
    case homeAssistant = "homeAssistant"
    case xiaomiCloud = "xiaomiCloud"
    /// 内置演示家庭，不联网。
    case demo = "demo"
}
```

应用生命周期跟着登录会话走。合盖休眠、切换用户、退出应用、离开家庭网络，任意一条都会让桥离线。开机自启解决不了系统会睡眠这件事；要真当常驻网桥用，就得要求用户关掉睡眠、永不锁屏，并为一台笔记本常年开着一个局域网服务。这是菜单栏控制器的代价里不该有的一项。

自己实现这层还要补齐：集成 chip/connectedhomeip 协议栈（Swift 与 Tauri 各一份，见 [ADR-0006](../docs/adr/0006-dual-platform-implementation.md)）；申请 CSA 认证与设备证书（DAC/PAI 与厂商 attestation）；做 Thread/Wi-Fi commissioning、OTA 和多管理员 fabric 持久化。规模上这是一个独立产品，不是桌面应用里的一个功能项。

### 2. 云端轮询换成 Matter 订阅，延迟和风控都会被放大

`MiCloudProvider`（[ADR-0002](../docs/adr/0002-xiaomi-cloud-direct-backend.md)）没有推送通道，靠分场景降频轮询拿状态（`Sources/MijiaCore/PollingPolicy.swift:26-31`）：

| 场景 | 间隔 |
| --- | --- |
| 面板打开 | 6 秒 |
| 面板关闭（菜单栏文字、提醒） | 60 秒 |
| 面板关闭且开低电量模式 | 180 秒 |
| 休眠、显示器休眠、锁屏 | 停止轮询，唤醒或解锁后立即补一次 |

```swift
public enum PollingPolicy {
    public static let panelOpenInterval: Duration = .seconds(6)
    public static let panelClosedInterval: Duration = .seconds(60)
    public static let lowPowerFactor = 3

    public static func interval(for context: PollingContext, ...) -> Duration? {
        guard context.systemActive else { return nil }  // 锁屏或休眠时完全不轮询
        if context.panelVisible { return panelOpen }
        return context.lowPower ? panelClosed * lowPowerFactor : panelClosed
    }
}
```

Matter 控制器按订阅模型工作。Apple 家庭中枢连上桥以后会订阅各 endpoint 的属性与事件，期望变化主动上报。桥要满足这个模型，只能全天候高频轮询小米云，也就是 `api.io.mi.com` 的私有 RC4 接口，官方没有为第三方桌面应用提供配额。用户以为拿到的是本地控制，实际得到的是更密集的云端读写。全屋几十台设备一起拉高轮询频率，是这套接口最容易被风控的用法；账号被限流或临时封禁时，面板自己的控制也会跟着失效。[ADR-0014](../docs/adr/0014-xiaomi-oauth2-mqtt-evaluation.md) 放弃私有 MQTT 通道时记录的也是这条边界。

写入侧同样对不上。面板现在用写后即时追询加乐观锁掩盖往返（`MiCloudProvider` 的 `followUpDelay` 为 800 毫秒），按下按钮到云端确认之间存在窗口。Matter 要求控制器读到的属性值就是设备的真值，这段窗口会被读成设备不听指令。

### 3. 已有成熟方案覆盖这个需求

按投入从低到高，三条现成路径：

1. **原生 Matter 设备，或明确声明可作 Matter Bridge 的第三方网关。** Aqara Hub M2、SwitchBot Hub 2、IKEA DIRIGERA、Philips Hue Bridge 这类桥在厂商文档里写明可把受支持的子设备暴露给 Matter 控制器，能桥接的型号与功能以厂商清单为准，且只覆盖标准化基础能力。手上是小米中枢网关一类的米家网关时，先查官方说明是否提供 Matter 桥接、可桥接哪些型号，不要按同品牌推断。米家 App 的 Matter 入口目前用于让米家控制原生 Matter 设备，不是把既有米家设备桥接给第三方平台。
2. **常驻设备跑 Home Assistant，再接回面板。** NAS 或树莓派上跑 HA，由它承担 7×24 的桥接与自动化：面向 Apple 家庭用官方的 HomeKit Bridge 集成，按 HAP 把实体暴露成配件（[ADR-0001](../docs/adr/0001-ha-as-single-source-of-truth.md)）。注意 HA 官方的 Matter 集成只是控制器，官方文档写明它不能把 HA 里的设备变成 Matter 设备，把它当 Matter 桥是常见误读。MenuBarMijia 的 `HAWebSocketProvider`（`Sources/MijiaCore/HomeProvider.swift:117`）订阅同一个 HA 实例，面板、快捷动作、自然语言指令与撤销都照旧，在线时长由那台常驻设备保证，不受笔记本休眠影响。Windows 端走自己的 Home Assistant 客户端，数据源互斥，结论相同。
3. **只求遥控，不要求进 Apple 家庭。** 面板本身就能控制这些设备，Raycast 扩展、`scripts/mijia` CLI 与 `menumijia://` 已经把面板能力开放给本机脚本，这条路径不需要任何桥。

## 这不改变什么

- 面板不排斥 Matter 生态。设备经上面的路径进入 Apple 家庭后，仍会由 HA 或米家云在 MenuBarMijia 里出现，控制方式不变。
- 若将来真要做桥接，它应该是一个独立常驻服务，交给 NAS、树莓派或专用网关运行，不塞进这个跟着桌面启停的应用。
- 桥接只提供标准化基础能力，灯效、厂商自动化、耗电历史这类细节仍以原生集成为准，这一点对所有 Matter 桥都成立。

## Prior requests

- #16 (GH#190): "希望米家设备能自动转 Matter，不经过 Home Assistant"
