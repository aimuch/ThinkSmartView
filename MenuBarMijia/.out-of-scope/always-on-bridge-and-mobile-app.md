# 配套手机端与 24/7 常驻桥服务（Always-On Bridge & Companion Mobile App）

MenuBarMijia 是桌面端的控制面板，不是家庭自动化服务器。它不提供配套手机 App，也不提供让电脑合盖或关机后继续执行的常驻桥。需要电脑不在线时仍然生效的规则，配置在米家云端或常驻的 Home Assistant 上。

## Why this is out of scope

1. **产品定位是桌面工作流的即开即控面板。** 主要的图形入口是 macOS 菜单栏与 Windows 系统托盘，另有 Raycast 扩展、`scripts/mijia` CLI、本地 MCP 服务、`menumijia://` URL Scheme 与 macOS App Intents 等本机伴生入口（见 [ADR-0013](../docs/adr/0013-raycast-companion-contract.md)），它们都依赖这台电脑上的应用在运行。README 的开场写的就是「Control your entire home right from your computer, without picking up your phone.」手机上的控制已经有成熟入口：官方米家 App 覆盖设备原厂能力，Apple「家庭」覆盖 HomeKit 设备，Home Assistant Companion App 覆盖自有 HA 实例的完整遥控与通知。再做一个移动端等于把这三个客户端重写一遍，还要额外背上推送、上架审核与多平台发版。常驻桥的代价同样不属于这个形态：独立后台进程、开机自启、崩溃守护、远程鉴权，任何一项都比面板本身重。

2. **睡眠与定时指令的安全边界。** 桌面电脑会因合盖、闲置休眠或关机而停止运行，应用从不阻止系统睡眠：`Sources/` 里没有任何 `IOPMAssertion` 或 `ProcessInfo.beginActivity` 休眠断言。`SystemActivityMonitor`（`Sources/MenuBarMijiaApp/SystemActivityMonitor.swift`）分别观察系统睡眠（`NSWorkspace.willSleepNotification` / `didWakeNotification`）、显示器睡眠（`screensDidSleepNotification` / `screensDidWakeNotification`）、锁屏（`com.apple.screenIsLocked` / `screenIsUnlocked`）与用户会话切换，并把结果经 `setScreenLocked`、`setSystemActive`、`setLowPowerMode` 交给 `HomeStore`。锁屏不等于休眠：定时任务在锁屏期间照常执行，锁屏拦截只作用于手动控制路径；系统睡眠、退出应用、关机或进程冻结等导致触发迟到超过 60 秒的情况，会让定时转入错过队列。

   定时指令因此按「到点执行，错过就问」处理。`ScheduledCommand`（`Sources/MijiaCore/AssistantSchedule.swift`）在设定时就把口令解析成 `IntentPlan` 并落盘，`storageKey = "assistant_scheduled_commands_v1"`：

   ```swift
   public struct ScheduledCommand: Codable, Identifiable, Equatable, Sendable {
       public static let storageKey = "assistant_scheduled_commands_v1"

       public let id: UUID
       /// "10 分钟后关闭书房吸顶灯"
       public let reply: String
       public let fireDate: Date
       public let plan: IntentPlan
   }
   ```

   `HomeStore` 用两个队列区分「还在倒计时」与「已经错过」（`Sources/MijiaCore/HomeStore.swift`）：

   ```swift
   /// Commands waiting on a spoken delay ("10 分钟后关灯"), soonest first. Persisted across launches.
   public internal(set) var scheduledCommands: [ScheduledCommand] = []
   /// Timers that came due while the app was closed or the Mac slept. Never auto-executed without user confirmation.
   public internal(set) var missedScheduledCommands: [ScheduledCommand] = []
   ```

   执行路径在 `Sources/MijiaCore/HomeStore+Assistant.swift`。`startScheduledTask(for:delay:)` 用 `Task.sleep` 等待，代码注释说明了关键前提：系统睡眠期间它继续计时，合盖的 Mac 唤醒时任务立刻返回。所以只要迟到超过 `lateTimerGrace`（60 秒，`HomeStore.swift`），任务不执行指令，而是调用 `moveToMissed(_:)` 转入 `missedScheduledCommands`，弹出助手栏并发通知。启动时 `restoreScheduledCommands(now:)` 做同样的分流：未到期项按剩余时间重新倒计时，已过期项直接进错过队列。只有 `runMissedScheduledCommand(id:)` 会在用户点「立即执行」后下发，`dismissMissedScheduledCommand(id:)` 丢弃条目；两个队列一起持久化在 `assistant_scheduled_commands_v1`，重启后不会丢。

   Windows 端同一条规则：`LATE_TIMER_GRACE_MS = 60_000` 与 `partitionDueTimers`（`windows/src/core/models.ts`）把每条定时分成 `run`、`missed`、`remaining` 三组，错过项显示「已错过定时：{command}（电脑睡眠时到点，没有自动执行）」。

   这就是常驻桥与本产品冲突的地方。真正的 24/7 只有两条路：阻止系统休眠，让用户的电脑违背节能与安全设置一直醒着；或者常驻后台进程，在唤醒后补发积压的硬件动作。后者会在几小时后静默打开一台电暖器、关掉一台冰箱所在的插座，而用户完全不知道发生了什么。MenuBarMijia 选择第三条路：到点没执行的动作交回用户确认，宁可漏一次，也不背着用户动硬件。

3. **24/7 自动化与远程控制的推荐路径。** 持续运行的规则应该待在它自己的执行环境里，而不是桌面客户端：

   - 米家 App 的定时与自动化在云端执行，与电脑开关机无关，覆盖开关灯、插座、空调和场景。代价是只能使用米家提供的触发条件。
   - Home Assistant 的自动化在常驻主机上执行（NAS、树莓派或其他 7×24 的小机器），触发条件、模板和通知都可自定义，并可通过 Companion App 推送到手机。

   MenuBarMijia 在这套分工里只做控制器。`HAWebSocketProvider` 与 `MiCloudProvider` 都实现 `HomeProvider`（`Sources/MijiaCore/HomeProvider.swift`、`Sources/MijiaCore/MiCloud/MiCloudProvider.swift`），负责连接、同步与下发；场景只负责触发，`scene.*` / `script.*` 的编排逻辑留在 Home Assistant（见 `GLOSSARY.md` 的 Scene 词条）。两个源同时在线时按 [ADR-0008](../docs/adr/0008-dual-source-device-deduplication.md) 合并同一台设备，不会出现一张卡片两个源互相覆盖。离家时用米家 App 或 Home Assistant Companion App 直连自己的账号或实例即可，不需要让 Mac 开着当桥。

## Prior requests

- #15 (GH#191): "电脑需常开才能控制；希望有配套手机端或常驻桥"
