# ADR-0017：不引入原生 HomeKit 后端，HomeKit 配件经 Home Assistant 的 HomeKit Device 集成接入

日期：2026-11-07　状态：已接受（对应 #18，原 GitHub #178；关联 #11、#58、ADR-0002、ADR-0003、ADR-0005、ADR-0006、ADR-0008）

## 背景

macOS 菜单栏应用天然贴近 Apple Home。用户与 issue 都提出过同一个设想：把 `HomeKit.framework` 当作第三个 `HomeBackend`，与 `HAWebSocketProvider`、`MiCloudProvider`（ADR-0002）并列，让「没装 Home Assistant、只用 Apple 家庭」的用户也能在菜单栏控制设备，并顺带获得 HomeKit 本地协议的低延迟与场景模型。

#18 要求先回答三个问题再决定是否立项：

1. macOS 原生 AppKit 进程能否链接并使用 `HomeKit.framework`；
2. `com.apple.developer.homekit` 这道签名门槛在当前自签名体系（ADR-0005）下是否可过；
3. 已接 Home Assistant 的用户，能否走 HA 的 HomeKit Device 集成，而不新增后端。

## 事实核对（本机实测，2026-11-07）

环境：macOS 27.0.1（26A434）、Xcode 27.2（27B52）、macOS SDK `MacOSX27.2.sdk`。每条结论都给出可重跑的命令与原始输出。

### 1. SDK 与运行时边界：原生 macOS 没有可链接的 HomeKit

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| SDK 路径 | `xcrun --sdk macosx --show-sdk-path` | `…/Developer/SDKs/MacOSX27.2.sdk` |
| 原生 `System/Library/Frameworks/HomeKit.framework` | `ls -d "$SDK/System/Library/Frameworks/HomeKit.framework"` | `No such file or directory` |
| `System/iOSSupport/.../HomeKit.framework` | `ls -d "$SDK/System/iOSSupport/System/Library/Frameworks/HomeKit.framework"` | 存在 |
| 链接桩支持的平台 | `grep '^targets:' …/HomeKit.tbd` | `[ x86_64-maccatalyst, arm64e-maccatalyst, arm64e.x1-maccatalyst ]` |
| Swift 模块可用的三元组 | `ls …/HomeKit.framework/Modules/HomeKit.swiftmodule` | 只有 `arm64e-apple-ios-macabi` 与 `x86_64-apple-ios-macabi` |
| 头文件可用性（`HMHomeManager` 类声明） | `sed -n '37,39p' …/Headers/HMHomeManager.h` | `API_AVAILABLE(ios(8.0), watchos(2.0), tvos(10.0), macCatalyst(14.0))`，下一行 `API_UNAVAILABLE(macos)` |

直接编译验证，同一份源码 `import HomeKit`：

```
$ xcrun swiftc -typecheck -target arm64-apple-macos14.0 -sdk "$SDK" hk.swift
hk.swift:1:8: error: no such module 'HomeKit'          # 退出码 1

$ xcrun swiftc -typecheck -target arm64-apple-ios17.0-macabi -sdk "$SDK" \
    -F "$SDK/System/iOSSupport/System/Library/Frameworks" hk.swift
（无输出）                                              # 退出码 0
```

对照组：同一 SDK 里的 `Matter.framework` 是原生框架（`Matter.tbd` 的 targets 含 `x86_64-macos`、`arm64e-macos`），`import Matter` 在 `arm64-apple-macos14.0` 下 typecheck 通过。**HomeKit 缺失是 Apple 的取舍，不是 SDK 装坏了。**

运行时同样是两套：

- `/System/Library/Frameworks/HomeKit.framework` 只剩 `PlugIns/HomeKitCustomerDiagnosticExtension.appex`，没有可加载的框架二进制；
- 真正的框架在 `/System/iOSSupport/System/Library/Frameworks/HomeKit.framework`，`Versions/A` 里也只有 `Resources`、`_CodeSignature`（二进制在 dyld 共享缓存里），install-name 为 `/System/iOSSupport/…`。

### 2. 桥接方向与 issue 的假设相反

#18 的原始假设是「原生 AppKit 应用额外构建 Mac Catalyst `.bundle`，再通过 `NSBundle` 桥接加载」。实测这条路被 dyld 直接拒绝：

```
$ # plugin.swift 用 -target arm64-apple-ios17.0-macabi 编成 libhkplugin.dylib
$ ./host            # 原生 arm64-apple-macos 进程里 dlopen 它
dlopen failed: mach-o file (libhkplugin.dylib), but incompatible platform
               (have 'macCatalyst', need 'macOS')
```

`LC_BUILD_VERSION` 的 platform 不同，dyld 不做跨平台映像加载。所以**原生 AppKit 进程无法在本进程内调用 `HMHomeManager`**，无论包装成 dylib、bundle 还是框架。

可行的形态只有反过来的两种：

1. **主 App 转成 Mac Catalyst**，AppKit 菜单栏做到一个原生 `platform: macOS` 的 bundle 里，由 Catalyst 进程加载；
2. **原生 App + Catalyst 助手进程**，通过 XPC 通信，助手单独签名并持有 HomeKit 权限。

社区里唯一在 Mac App Store 发货的 HomeKit 菜单栏应用 `nickustinov/itsyhome-macos`（MIT）走的是第 1 种，源码可查：`project.yml` 主 target 是 `platform: iOS`、`supportedDestinations: [iOS, maccatalyst]`、`SUPPORTS_MACCATALYST: YES`，entitlements 里 `com.apple.developer.homekit: true`；第二个 target `macOSBridge` 是 `type: bundle`、`platform: macOS`，由 `AppDelegate.loadMacOSPlugin()` 从 `Bundle.main.builtInPlugInsURL` / `resourceURL` 加载。**Catalyst 是主体，AppKit 才是插件。**

顺带否掉一个常见误解：`swift build --triple arm64-apple-ios17.0-macabi` 在当前工具链下**可以**产出 macabi 产物（实测编译通过，产物 `platform 6` 可直接运行），所以「SwiftPM 无法生成 Catalyst 切片」这句话不成立。真正的成本在别处（见下节）。

### 3. 签名门槛：受限权限，自签名体系下必被内核杀掉

`com.apple.developer.homekit` 是受限 entitlement。Apple 该键的可用性列表只有 iOS / iPadOS / tvOS / visionOS / watchOS，**不含 macOS**；开启方式是在 Xcode 里勾 HomeKit capability，由描述文件授权。

实测（`clang` 编一个 `int main(void){return 0;}`，只加这一个 entitlement，逐种签名身份跑）：

| 签名身份 | `codesign` | 运行结果 |
| --- | --- | --- |
| 不加 entitlement（对照） | ad-hoc | 退出码 0 |
| ad-hoc + entitlement | 成功 | 退出码 137（SIGKILL） |
| 自签名 `MenuBarMijia Release Signing`（ADR-0005 的发布身份，本机存在）+ entitlement | 成功 | 退出码 137 |
| `Apple Development` 身份 + entitlement | 成功 | 退出码 137 |

`amfid` 日志给出原因，两条都不是「配置问题」，是策略：

```
Adhoc signed app with restricted entitlements detected
… not valid: Code=-424 "The file is adhoc signed but contains restricted entitlements"

… not valid: Code=-413 "No matching profile found"
```

本机唯一的描述文件是 `iOS Team Provisioning Profile: com.yangzhiyuan.wda.runner.xctrunner`（platform 为 `iOS`，不含 HomeKit）。也就是说：**没有 Apple Developer Program 付费账号签发的、App ID 已开启 HomeKit capability 的描述文件，带该 entitlement 的二进制根本起不来。** 这与 #11（申请付费账号 + 签名 + 公证）是同一道门，#58 的 WidgetKit / `ControlWidget` 也是被它挡住。

即便签名过了，还有第二道 TCC：`HMHomeManager` 首次使用需要 `Info.plist` 的 `NSHomeKitUsageDescription`，缺失时进程直接崩溃（实测退出码 134），`tccd` 记录 `Refusing authorization request for service kTCCServiceWillow … without NSHomeKitUsageDescription key`；授予权限后才是「用户可能拒绝」的常规分支。

### 4. 落到本仓库的架构成本

`HomeProvider`（`Sources/MijiaCore/HomeProvider.swift`）要求实现 `events()` / `execute(_:)` / `refreshNow()` / `readBack` / 摄像头流与传感器历史等成员。`HomeBackend` 目前是 `homeAssistant` / `xiaomiCloud` / `demo` 三档。若加 `homeKit`：

- **不能放在 `MijiaCore`**：ADR-0003 要求核心库保持可在 Linux 编译，`import HomeKit` 既不在 Linux、也不在 `arm64-apple-macos` 可用，只能放在 App 层，用协议隔开；
- **不能是原生 App 内的代码**：dyld 那一关说明原生进程根本没有 HomeKit 可用；
- **要么把主 App 改成 Mac Catalyst**，等于推翻现有菜单栏形态与 `Package.swift`/`scripts/build_app.sh` 的单工程手工打包（现在是一个 `swift build --arch arm64 --arch x86_64` 通用二进制 + 手写 `Info.plist` + `scripts/MenuBarMijia.entitlements`），还要再造一个 AppKit bundle、两套签名目标、两套快照与 CI；
- **要么加 Catalyst XPC 助手**：多一个可执行体、多一份描述文件与 TCC 授权、多一条 IPC 协议，`DeviceCommand` 与状态回推都要跨进程序列化。

两种形态都绕不开 #11。**在 #11 完成之前，带 `com.apple.developer.homekit` 的产物无法启动，这一步的工程投入没有可运行的产出。**

## 备选方案

1. **原生 `HomeKitProvider`（Catalyst 主 App 或 Catalyst 助手）**。能力最完整：`HMHome` / `HMRoom` / `HMActionSet`（HomeKit 的场景就是 `HMHome.actionSets`，没有 `HMScene` 类型）直接映射房间与场景，`HMCharacteristic` 推送替代轮询，门铃与摄像头可走 HomeKit 通道。**否定原因**：需要付费开发者账号 + HomeKit 描述文件（#11），而 #11 是人工事项，不在代码可解范围；在拿到匹配的 Provisioning Profile 之前，携带 `com.apple.developer.homekit` 的产物会被 amfid 拒绝（SIGKILL）——不带该 entitlement 的 Catalyst 切片能启动，但读不到任何 HomeKit 数据——等于交付一个跑不起来的后端；同时要把现有单工程构建设计推翻（ADR-0003、ADR-0006 的分工与打包/快照/更新链路全部连带）。

2. **手写 HAP 控制器（不依赖 HomeKit.framework）**。HomeKit IP 配件的 HAP 是公开规范，理论上可以自己实现配对（SRP + ChaCha20-Poly1305）、加密会话、特征订阅与事件。**否定原因**：这是从零重写一套协议栈——配对、加密、mDNS 发现、特征元数据、摄像头 SRTP、多管理员配对全都要自己维护；规范里没有 HomeKit 的「家 / 房间 / 场景」模型，UX 还得另造；出错代价是控制真实门锁与车库门。与方案 3 的收益相比完全不划算。

3. **HomeKit 配件经 Home Assistant 的 HomeKit Device 集成（`homekit_controller`）接入，由 `HAWebSocketProvider` 呈现。** HA 官方文档明确：该集成用于把带「Works with HomeKit」标的配件接到 Home Assistant（与反方向的 HomeKit Bridge 不是一回事），支持 `alarm_control_panel`、`climate`、`cover`、`light`、`lock`、`switch`、`number`、`binary_sensor`、`sensor`、`fan`、`humidifier`，以及按钮 / 遥控器 / 门铃这类无状态配件的自动化触发器；IP 配件支持推送更新，不稳定时回落轮询。接入后 MenuBarMijia 什么都不用改：设备就是 HA 实体，走既有合成、乐观锁、撤销与跨源去重（ADR-0008）；两端（macOS Swift 与 Windows Tauri）同时受益，因为 Windows 端也是读 HA 快照。**代价**：需要用户有一台 HA；HomeKit 配件同一时刻只能被一个控制器配对，迁移步骤按入网方式区分——Wi-Fi / 以太网配件若已配对 Apple 家庭，在「家庭」App 里移除即可，不重置设备，移除后设备仍在网络中，HA 可以发现它；蓝牙入网的配件若曾用过 Thread 或仍与 iOS 配对，按厂商说明重置设备；经 Apple 家庭中枢（Thread 边界路由器）接入的 Thread 配件则是移除而不重置，让设备保留 Thread 凭据。无状态配件的响铃在 HA 里只作为设备触发器出现，不产生可订阅的 `event` 实体。

4. **只做 Matter。** `Matter.framework` 是原生框架，不受本 ADR 限制。但 HomeKit 配件是 HAP 而非 Matter，两者不互通；而且本项目没有 Apple Home 的 fabric 控制器权限。Matter 支持属于独立议题（#10 一类的生态跟进），不能替代 HomeKit 配件接入。

## 决定

选择方案 3：**不引入原生 `HomeKitProvider`**。

1. `HomeBackend` 维持 `homeAssistant` / `xiaomiCloud` / `demo` 三档，不新增 `homeKit`。
2. 不在 `Package.swift` 增加 macabi target，不引入 Catalyst 主 App 或 Catalyst 助手，不手写 HAP 控制器。
3. HomeKit 配件的支持路径写作：配件 → Home Assistant 的 HomeKit Device 集成 → `HAWebSocketProvider`（ADR-0002）→ 跨源去重（ADR-0008）。用户已把配件配到 Apple 家庭时，按入网方式迁移：Wi-Fi / 以太网配件在「家庭」App 移除即可，不重置；蓝牙入网的配件若曾用过 Thread 或仍与 iOS 配对，按厂商说明重置；经 Apple 家庭中枢接入的 Thread 配件移除而不重置，保留 Thread 凭据。
4. 本 ADR 的实测结论以本节为准；`#18` 与后续任何「加 HomeKit 后端」的提议都应先回到这里的证据，而不是重新试一遍 `import HomeKit`。

## 后果

- **用户侧**：只用 Apple 家庭、没有 HA 的用户，MenuBarMijia 看不到其 HomeKit 配件；这一点是明确的能力边界，不是 bug。已有 HA 的用户今天就能用，无需新后端、无需新权限。
- **维护侧**：省掉一套 Catalyst/AppKit 双形态构建、一份受限描述文件、一条跨进程指令协议，以及随之而来的签名、公证、快照与 CI 改造；`MijiaCore` 保持平台无关（ADR-0003）。
- **代价**：多一跳 HA，HomeKit 推送变成 HA WebSocket 推送（仍是本地局域网，延迟可接受）；未配对到 HA 的配件不可见；HomeKit 独占能力（HomeKit 安全视频、Apple 家庭的自动化与成员权限）拿不到。
- **与 #11 的关系**：#11 落地只解锁签名与公证，**不自动解锁本 ADR 的决策**——它只移除了第 3 节的门槛，第 2 节与第 4 节的架构成本仍在，需要另行评估。

## 重启条件

满足任意一条再回来看：

1. Apple 让 `HomeKit.framework` 在 `arm64-apple-macos` 可链接（SDK 里出现原生 `HomeKit.tbd` 或 `arm64-apple-macos` 的 swiftmodule），并且不再要求受限 entitlement 之外的额外签名；或
2. #11 完成（付费账号 + App ID 的 HomeKit capability + 描述文件），**并且**有人愿意承担把主 App 转 Catalyst（或加 Catalyst 助手）的连带改造；或
3. 出现具体用户需求，方案 3 明确不能满足——例如需要 HomeKit 安全视频、Apple 家庭场景 / 自动化双向同步，或门铃事件必须实时弹窗而 HA 的设备触发器路径不够。注意门铃响铃在 HA 里已有设备触发器这条路，立项前先验证它是否真的不够。

## 相关

- #18（本 ADR 的来源，原 GitHub #178）、#11（付费账号与签名/公证）、#58（同一道签名门槛挡住 WidgetKit 与 `ControlWidget`）
- ADR-0002（双后端 `HomeProvider`）、ADR-0003（`MijiaCore` 保持 Linux 可编译）、ADR-0005（自签名更新链与固定证书）、ADR-0006（双端独立实现）、ADR-0008（跨源去重合并）
- `Sources/MijiaCore/HomeProvider.swift`（`HomeBackend`、`HomeProvider`）、`Package.swift`、`scripts/build_app.sh`、`scripts/MenuBarMijia.entitlements`
- Home Assistant：[HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/)（`homekit_controller`，与反方向的 [HomeKit Bridge](https://www.home-assistant.io/integrations/homekit/) 不同）
- Apple：[HomeKit Entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.homekit)、[Enabling HomeKit in your app](https://developer.apple.com/documentation/HomeKit/enabling-homekit-in-your-app)
- 参考实现：[`nickustinov/itsyhome-macos`](https://github.com/nickustinov/itsyhome-macos)（MIT，Mac Catalyst 主 App + 原生 `macOSBridge` bundle）
