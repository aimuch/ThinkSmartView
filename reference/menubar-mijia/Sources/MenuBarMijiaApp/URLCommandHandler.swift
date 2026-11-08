import AppKit
import Foundation
import MijiaCore

/// Executes `menumijia://` links (#57).
///
/// Web pages can navigate to a custom scheme without a verifiable user gesture, so each control
/// action is shown in an app-modal confirmation sheet unless the user turned on “允许 URL 直控”
/// or the link carries the local `0600` companion `bridgeToken`.
/// The sheet names the device and the exact action (turn on/off, run scene, run command) so the
/// user is never asked to approve an opaque request.
@MainActor
final class URLCommandHandler {
    static let shared = URLCommandHandler()

    private enum Resolved {
        case toggle(SmartDevice)
        case scene(HomeScene)
        case builtInScene(BuiltInURLScene)
        case ask(String)
        case power(SmartDevice, Bool)
        case brightness(SmartDevice, Int)
        case colorTemp(SmartDevice, Int)
        case temperature(SmartDevice, Double)
        case fan(SmartDevice, Int?, String?)
        case focus(SmartDevice)

        /// Navigation-only commands run without the external-link confirmation sheet.
        var controlsDevice: Bool {
            if case .focus = self { return false }
            return true
        }
    }

    private var pendingURLs: [URL] = []
    private var isDraining = false

    /// Test seam replacing modal `NSAlert` confirmation in unit tests.
    var confirmationHandler: ((String) -> Bool)?
    /// Test seam replacing `HotkeyAndNotificationManager.shared.ensureMenuBarExtraWindowVisible()`.
    var windowVisibilityHandler: (() -> Void)?

    init() {}

    func enqueue(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        pendingURLs.append(contentsOf: urls)
        guard !isDraining else { return }
        isDraining = true
        Task { @MainActor [weak self] in
            await self?.drain()
        }
    }

    private func drain() async {
        while !pendingURLs.isEmpty {
            let url = pendingURLs.removeFirst()
            await handle(url)
        }
        isDraining = false
    }

    func handle(_ url: URL, in store: HomeStore = HomeStore.shared) async {
        guard let envelope = AppURLParser.parseEnvelope(url) else {
            let message = L10n.tr("无法识别的控制链接", "Unrecognized control link")
            store.showToast(message, isError: true)
            store.recordRaycastCommandFeedback(
                requestId: nil,
                command: "invalid",
                message: message,
                isError: true
            )
            return
        }

        // A link can be the launch reason; wait briefly for the provider's first snapshot so the
        // confirmation sheet can name the device instead of failing.
        do {
            try await store.waitForInitialSnapshot()
        } catch {
            finish(error.localizedDescription, isError: true, envelope: envelope, store: store)
            return
        }

        guard let resolved = resolve(envelope.command, store: store) else {
            let message = L10n.tr(
                "链接里的设备或场景不存在，或尚未加载",
                "The device or scene in the link is missing or not loaded yet"
            )
            store.showToast(message, isError: true)
            store.recordRaycastCommandFeedback(
                requestId: envelope.requestId,
                command: commandLabel(for: envelope.command),
                message: message,
                isError: true
            )
            return
        }

        if store.isControlBlockedByScreenLock {
            finish(store.screenLockBlockedMessage, isError: true, envelope: envelope, store: store)
            return
        }

        let isTrustedBridge = URLDirectControl.isValidBridgeToken(
            envelope.token,
            expectedToken: store.raycastBridgeToken
        )

        if resolved.controlsDevice,
           URLDirectControl.requiresConfirmation(
               in: store.userDefaults,
               providedToken: envelope.token,
               expectedToken: store.raycastBridgeToken
           ) {
            let prompt = description(for: resolved)
            let approved = confirmationHandler?(prompt) ?? confirm(prompt)
            guard approved else {
                store.recordRaycastCommandFeedback(
                    requestId: envelope.requestId,
                    command: commandLabel(for: envelope.command),
                    message: L10n.tr("已取消外部链接执行", "Cancelled external link execution"),
                    isError: true
                )
                return
            }
        }

        // Keep Raycast/Tinycast keyboard focus when invoked with a valid local bridgeToken;
        // only open the menu-bar window upfront for untrusted external links. `focus` opens it
        // itself after it has moved the panel to the device.
        if !isTrustedBridge, resolved.controlsDevice {
            openMenuBarWindowIfNeeded()
        }
        await execute(resolved, envelope: envelope, isTrustedBridge: isTrustedBridge, store: store)
    }

    private func openMenuBarWindowIfNeeded() {
        if let windowVisibilityHandler {
            windowVisibilityHandler()
        } else {
            HotkeyAndNotificationManager.shared.ensureMenuBarExtraWindowVisible()
        }
    }

    private func resolve(_ command: AppURLCommand, store: HomeStore) -> Resolved? {
        switch command {
        case .toggle(let deviceId):
            // Accept an id (documented) or an exact name for hand-written links. Only devices a
            // single click can control qualify: `togglePrimary` opens the detail drawer for
            // speakers/fridges, and a link must never expand UI instead of controlling something.
            guard let device = store.devices.first(where: {
                ($0.id == deviceId || $0.name == deviceId) && $0.isPrimaryToggleable
            }) else {
                return nil
            }
            return .toggle(device)
        case .scene(let name):
            if name.lowercased().hasPrefix("quick:"),
               let builtIn = BuiltInURLScene.resolve(name) {
                return .builtInScene(builtIn)
            }
            if let scene = store.scenes.first(where: { $0.name == name || $0.id == name }) {
                return .scene(scene)
            }
            if let builtIn = BuiltInURLScene.resolve(name) {
                return .builtInScene(builtIn)
            }
            return nil
        case .ask(let text):
            return .ask(text)
        case .power(let deviceId, let isOn):
            guard let device = Self.device(deviceId, in: store) else { return nil }
            return .power(device, isOn)
        case .brightness(let deviceId, let percent):
            guard let device = Self.device(deviceId, in: store) else { return nil }
            return .brightness(device, percent)
        case .colorTemp(let deviceId, let kelvin):
            guard let device = Self.device(deviceId, in: store) else { return nil }
            return .colorTemp(device, kelvin)
        case .temperature(let deviceId, let celsius):
            guard let device = Self.device(deviceId, in: store) else { return nil }
            return .temperature(device, celsius)
        case .fan(let deviceId, let percent, let preset):
            guard let device = Self.device(deviceId, in: store) else { return nil }
            return .fan(device, percent, preset)
        case .focus(let deviceId):
            guard let device = Self.device(deviceId, in: store) else { return nil }
            return .focus(device)
        }
    }

    /// Parameterized commands address a device by its snapshot id only: a slider must never land on
    /// a different device whose name happens to match.
    private static func device(_ deviceId: String, in store: HomeStore) -> SmartDevice? {
        store.devices.first(where: { $0.id == deviceId })
    }

    private func description(for resolved: Resolved) -> String {
        switch resolved {
        case .toggle(let device):
            let verb: String
            if device.isMomentaryButton {
                verb = L10n.tr("触发", "Trigger")
            } else {
                verb = device.isActiveOn
                    ? L10n.tr("关闭", "Turn off")
                    : L10n.tr("打开", "Turn on")
            }
            let room = L10n.displayRoomName(device.roomName)
            return L10n.format(
                "%@「%@」（%@）",
                "%@ \"%@\" (%@)",
                verb,
                device.name,
                room
            )
        case .scene(let scene):
            return L10n.format("触发场景「%@」", "Run scene \"%@\"", scene.name)
        case .builtInScene(let builtIn):
            return L10n.format("执行快捷动作「%@」", "Run quick action \"%@\"", builtIn.displayTitle)
        case .ask(let text):
            return L10n.format("执行指令：「%@」", "Run command: \"%@\"", text)
        case .power(let device, let isOn):
            let room = L10n.displayRoomName(device.roomName)
            let verb = isOn ? L10n.tr("打开", "Turn on") : L10n.tr("关闭", "Turn off")
            return L10n.format("%@「%@」（%@）", "%@ \"%@\" (%@)", verb, device.name, room)
        case .brightness(let device, let percent):
            let room = L10n.displayRoomName(device.roomName)
            if percent == 0 {
                return L10n.format("关闭「%@」（%@）", "Turn off \"%@\" (%@)", device.name, room)
            }
            return L10n.tr(
                "把\(device.name)亮度调到 \(percent)%（\(room)）",
                "Set \(device.name) brightness to \(percent)% (\(room))"
            )
        case .colorTemp(let device, let kelvin):
            return L10n.tr(
                "把\(device.name)色温调到 \(kelvin)K",
                "Set \(device.name) colour temperature to \(kelvin)K"
            )
        case .temperature(let device, let celsius):
            let text = IntentReply.formatTemperature(celsius)
            return L10n.tr("把\(device.name)温度调到 \(text)°C", "Set \(device.name) to \(text) °C")
        case .fan(let device, let percent, let preset):
            if let percent {
                return L10n.tr(
                    "把\(device.name)风速调到 \(percent)%",
                    "Set \(device.name) fan speed to \(percent)%"
                )
            }
            return L10n.tr(
                "把\(device.name)风速切换到「\(preset ?? "")」",
                "Set \(device.name) fan mode to \"\(preset ?? "")\""
            )
        case .focus(let device):
            return L10n.format("打开「%@」详情", "Open \"%@\" details", device.name)
        }
    }

    /// Returns true when the user approved this one action.
    private func confirm(_ description: String) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = L10n.tr(
            "允许外部链接控制设备？",
            "Allow an external link to control devices?"
        )
        alert.informativeText = description + "\n\n" + L10n.tr(
            "网页和其它 App 都可以打开这个链接。确认后只执行这一次。",
            "Web pages and other apps can open this link. It runs only once after you confirm."
        )
        alert.alertStyle = .warning
        // Cancel is the default (Return) button so a web-triggered sheet is not approved by a stray
        // keystroke; the user has to choose the second button explicitly.
        alert.addButton(withTitle: L10n.tr("取消", "Cancel"))
        alert.addButton(withTitle: L10n.tr("执行一次", "Run Once"))
        return alert.runModal() == .alertSecondButtonReturn
    }

    private func execute(
        _ resolved: Resolved,
        envelope: AppURLCommandEnvelope,
        isTrustedBridge: Bool,
        store: HomeStore
    ) async {
        switch resolved {
        case .toggle(let snapshot):
            guard let device = store.devices.first(where: { $0.id == snapshot.id }) else {
                let message = L10n.tr("设备已不在面板中", "The device is no longer available")
                finish(message, isError: true, envelope: envelope, store: store)
                return
            }
            guard device.isOnline else {
                finish(
                    L10n.format("%@ 当前离线", "%@ is offline", device.name),
                    isError: true,
                    envelope: envelope,
                    store: store
                )
                return
            }
            // `togglePrimary` flips whatever the device is doing now, so report the result rather
            // than a prediction made before the confirmation sheet appeared.
            let isMomentary = device.isMomentaryButton
            let previousToastId = store.toastNotice?.id
            guard let task = store.togglePrimary(device) else {
                let cancellationOrRejection = (store.toastNotice?.id != previousToastId)
                    ? store.toastNotice?.message
                    : nil
                finish(
                    cancellationOrRejection ?? (
                        isMomentary
                            ? L10n.format("%@ 触发失败", "Failed to trigger %@", device.name)
                            : L10n.format("%@ 响应失败", "%@ failed to respond", device.name)
                    ),
                    isError: true,
                    envelope: envelope,
                    store: store
                )
                return
            }
            guard await task.value == true else {
                finish(
                    isMomentary
                        ? L10n.format("%@ 触发失败", "Failed to trigger %@", device.name)
                        : L10n.format("%@ 响应失败", "%@ failed to respond", device.name),
                    isError: true,
                    envelope: envelope,
                    store: store
                )
                return
            }
            let isOnNow = store.devices.first(where: { $0.id == device.id })?.isActiveOn ?? false
            if isMomentary {
                finish(
                    L10n.format("已触发 %@", "Triggered %@", device.name),
                    envelope: envelope,
                    store: store
                )
                return
            }
            finish(
                isOnNow
                    ? L10n.format("已打开 %@", "Turned on %@", device.name)
                    : L10n.format("已关闭 %@", "Turned off %@", device.name),
                envelope: envelope,
                store: store
            )
        case .scene(let snapshot):
            guard let scene = store.scenes.first(where: { $0.id == snapshot.id }) else {
                let message = L10n.tr("场景已不在列表中", "The scene is no longer available")
                finish(message, isError: true, envelope: envelope, store: store)
                return
            }
            let succeeded = await store.triggerScene(scene).value
            let message = succeeded
                ? L10n.format("已触发场景：%@", "Activated scene: %@", scene.name)
                : L10n.format("场景「%@」执行失败", "Scene \"%@\" failed", scene.name)
            store.recordRaycastCommandFeedback(
                requestId: envelope.requestId,
                command: commandLabel(for: envelope.command),
                message: message,
                isError: !succeeded
            )
            // `triggerScene` already reports success/failure through the store toast; only mirror it
            // to the notification banner so a Raycast/Stream Deck trigger is visible.
            HotkeyAndNotificationManager.shared.deliverExternalActionNotification(
                title: L10n.tr("MenuBarMijia 外部链接", "MenuBarMijia external link"),
                body: message
            )
        case .builtInScene(let builtIn):
            let outcome = await store.executeBuiltInURLScene(builtIn)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
        case .ask(let text):
            let direct = await store.executeRaycastDirectCommand(text)
            if direct.handled {
                finish(direct.message, isError: direct.isError, envelope: envelope, store: store)
                return
            }
            let plan = await store.runAssistantCommand(text)
            let state = store.assistantState
            if store.pendingCloudConsent != nil {
                // The consent card lives in the assistant bar; open it so the user can allow the
                // request the link asked for.
                if isTrustedBridge {
                    openMenuBarWindowIfNeeded()
                }
                store.presentAssistantBar()
                finish(
                    L10n.tr(
                        "这条指令需要云端 AI。请在面板里确认后重试。",
                        "This command needs cloud AI. Confirm it in the panel and try again."
                    ),
                    isError: true,
                    envelope: envelope,
                    store: store
                )
            } else if case .awaitingChoice(let reply) = state {
                if isTrustedBridge {
                    openMenuBarWindowIfNeeded()
                }
                store.presentAssistantBar()
                finish(reply, isError: false, envelope: envelope, store: store)
            } else if case .failed(let reason) = state {
                finish(reason, isError: true, envelope: envelope, store: store)
            } else if case .done(let reply) = state, !reply.isEmpty {
                finish(reply, isError: false, envelope: envelope, store: store)
            } else if let plan, !plan.reply.isEmpty {
                finish(plan.reply, isError: false, envelope: envelope, store: store)
            } else {
                switch state {
                case .failed(let reason):
                    finish(reason, isError: true, envelope: envelope, store: store)
                case .done(let reply), .awaitingChoice(let reply):
                    finish(reply, envelope: envelope, store: store)
                case .thinking:
                    finish(L10n.tr("正在执行…", "Working…"), envelope: envelope, store: store)
                case .idle:
                    finish(
                        L10n.tr("没有找到可执行的设备操作", "No matching device actions"),
                        isError: true,
                        envelope: envelope,
                        store: store
                    )
                }
            }
        case .power(let device, let isOn):
            let outcome = await store.executeRaycastPower(deviceId: device.id, isOn: isOn)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
        case .brightness(let device, let percent):
            let outcome = await store.executeRaycastBrightness(deviceId: device.id, percent: percent)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
        case .colorTemp(let device, let kelvin):
            let outcome = await store.executeRaycastColorTemp(deviceId: device.id, kelvin: kelvin)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
        case .temperature(let device, let celsius):
            let outcome = await store.executeRaycastTemperature(deviceId: device.id, celsius: celsius)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
        case .fan(let device, let percent, let preset):
            let outcome = await store.executeRaycastFan(deviceId: device.id, percent: percent, preset: preset)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
        case .focus(let device):
            let outcome = store.focusRaycastDevice(deviceId: device.id)
            finish(outcome.message, isError: outcome.isError, envelope: envelope, store: store)
            if !outcome.isError { openMenuBarWindowIfNeeded() }
        }
    }

    private func commandLabel(for command: AppURLCommand) -> String {
        switch command {
        case .toggle(let deviceId): return "toggle:\(deviceId)"
        case .scene(let name): return "scene:\(name)"
        case .ask(let text): return "ask:\(text)"
        case .power(let deviceId, let isOn): return "power:\(deviceId):\(isOn ? "on" : "off")"
        case .brightness(let deviceId, let percent): return "brightness:\(deviceId):\(percent)"
        case .colorTemp(let deviceId, let kelvin): return "color-temp:\(deviceId):\(kelvin)"
        case .temperature(let deviceId, let celsius):
            return "temperature:\(deviceId):\(IntentReply.formatTemperature(celsius))"
        case .fan(let deviceId, let percent, let preset):
            if let percent { return "fan:\(deviceId):\(percent)" }
            return "fan:\(deviceId):\(preset ?? "")"
        case .focus(let deviceId): return "focus:\(deviceId)"
        }
    }

    private func finish(
        _ message: String,
        isError: Bool = false,
        envelope: AppURLCommandEnvelope,
        store: HomeStore
    ) {
        store.showToast(message, isError: isError)
        store.recordRaycastCommandFeedback(
            requestId: envelope.requestId,
            command: commandLabel(for: envelope.command),
            message: message,
            isError: isError
        )
        HotkeyAndNotificationManager.shared.deliverExternalActionNotification(
            title: L10n.tr("MenuBarMijia 外部链接", "MenuBarMijia external link"),
            body: message
        )
    }
}
