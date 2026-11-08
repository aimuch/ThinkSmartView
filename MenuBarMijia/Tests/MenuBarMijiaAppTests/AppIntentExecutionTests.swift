import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

@MainActor
struct AppIntentExecutionTests {
    private static let lamp = SmartDevice(
        id: "test-lamp", name: "Test lamp", roomId: "study", roomName: "Study",
        model: "test.light", isOnline: true,
        kind: .light(LightSpec(entityId: "light.test", isOn: false))
    )
    private static let scene = HomeScene(id: "test-scene", name: "Test scene", entityId: "scene.test")

    @Test("External actions wait for the first snapshot, including an empty home")
    func firstSnapshot() async throws {
        let suite = "test.intent.snapshot.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let provider = ControlledIntentProvider()
        let store = HomeStore(provider: provider, defaultsSuiteName: suite)
        var returned = false
        let waiting = Task {
            try await store.waitForTestSnapshot()
            returned = true
        }
        await Task.yield()
        #expect(!returned)
        await provider.sendSnapshot(SynthesizedHome())
        try await waiting.value
        #expect(returned)
        #expect(store.devices.isEmpty)
        #expect(store.hasReceivedInitialSnapshot)
    }

    @Test("Missing initial data times out instead of looking like an empty home")
    func snapshotTimeout() async throws {
        let suite = "test.intent.timeout.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let store = HomeStore(provider: ControlledIntentProvider(), defaultsSuiteName: suite)
        await #expect(throws: HomeProviderError.self) {
            try await store.waitForInitialSnapshot(timeout: .milliseconds(10))
        }
    }

    @Test("Device intent waits for provider rejection and reports rollback")
    func rejectedToggle() async throws {
        try await L10n.$overrideLanguage.withValue(.en) {
            let suite = "test.intent.toggle.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let provider = ControlledIntentProvider()
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            let intent = ToggleDeviceIntent()
            intent.device = DeviceEntity(device: Self.lamp)
            var returned = false
            let invocation = Task {
                let message = try await intent.execute(in: store)
                returned = true
                return message
            }
            await Task.yield()
            #expect(!returned)
            await provider.sendSnapshot(SynthesizedHome(devices: [Self.lamp]))
            await provider.waitForCommand(1)
            #expect(await provider.commandCount == 1)
            #expect(store.waitingDeviceIds.contains("test-lamp"))
            #expect(store.devices.first?.isActiveOn == false)
            #expect(!returned)
            await provider.complete(failing: true)
            let message = try await invocation.value
            #expect(message == "Test lamp failed to respond")
            #expect(store.devices.first?.isActiveOn == false)
            #expect(store.waitingDeviceIds.isEmpty)
        }
    }

    @Test("Scene intent reports provider failure and only reports success after acknowledgment")
    func sceneResult() async throws {
        try await L10n.$overrideLanguage.withValue(.en) {
            let suite = "test.intent.scene.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let provider = ControlledIntentProvider()
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            await provider.sendSnapshot(SynthesizedHome(scenes: [Self.scene]))
            let intent = RunSceneIntent()
            intent.scene = SceneEntity(scene: Self.scene)
            let failed = Task { try await intent.execute(in: store) }
            await provider.waitForCommand(1)
            #expect(await provider.commandCount == 1)
            await provider.complete(failing: true)
            let failedMessage = try await failed.value
            #expect(failedMessage == "Scene \"Test scene\" failed")
            let succeeded = Task { try await intent.execute(in: store) }
            await provider.waitForCommand(2)
            #expect(await provider.commandCount == 2)
            await provider.complete(failing: false)
            let succeededMessage = try await succeeded.value
            #expect(succeededMessage == "Activated scene: Test scene")
        }
    }

    @Test("Momentary button device intent reports Triggered instead of Turned off (#100)")
    func momentaryButtonIntentResult() async throws {
        let wolDevice = SmartDevice(
            id: "wol_pc", name: "Study PC", roomId: "study", roomName: "Study",
            model: "Wake on LAN", isOnline: true,
            kind: .plug(PlugSpec(switchEntityId: "button.wake_on_lan_pc", isOn: false))
        )
        try await L10n.$overrideLanguage.withValue(.en) {
            let suite = "test.intent.wol.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let provider = ControlledIntentProvider()
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            // A momentary plug button is a hazard category, so the app-issued confirmation must be
            // approved for this Shortcuts-path test (#143).
            store.manualControlConfirmationHandler = { _ in true }
            await provider.sendSnapshot(SynthesizedHome(devices: [wolDevice]))
            let intent = ToggleDeviceIntent()
            intent.device = DeviceEntity(device: wolDevice)
            let succeeded = Task { try await intent.execute(in: store) }
            await provider.waitForCommand(1)
            #expect(await provider.commandCount == 1)
            await provider.complete(failing: false)
            let succeededMessage = try await succeeded.value
            #expect(succeededMessage == "Triggered Study PC")
        }
    }

    @Test("Shortcuts answer an ambiguous 关灯 without sending any device command")
    func ambiguousBareCategoryStaysHandsOff() async throws {
        try await L10n.$overrideLanguage.withValue(.zhHans) {
            let suite = "test.intent.bare.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let ceiling = SmartDevice(
                id: "study_ceiling", name: "书房吸顶灯", roomId: "study", roomName: "书房",
                model: "yeelink.light.ceiling20", isOnline: true,
                kind: .light(LightSpec(entityId: "light.study_ceiling", isOn: true))
            )
            let desk = SmartDevice(
                id: "study_desk", name: "书房台灯", roomId: "study", roomName: "书房",
                model: "yeelink.light.lamp4", isOnline: true,
                kind: .light(LightSpec(entityId: "light.study_desk", isOn: true))
            )
            let provider = ControlledIntentProvider()
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            let intent = AskAssistantIntent()
            intent.text = "关灯"
            let invocation = Task { try await intent.execute(in: store) }
            await Task.yield()
            await provider.sendSnapshot(SynthesizedHome(devices: [ceiling, desk]))

            let message = try await invocation.value
            #expect(message == "开着：书房吸顶灯、书房台灯。请说出房间或设备名")
            #expect(await provider.commandCount == 0)
            #expect(store.scheduledCommands.isEmpty)
            #expect(store.devices.allSatisfy { $0.isActiveOn })
        }
    }

    @Test("Shortcuts answer an ambiguous 开灯 without sending any device command (#274)")
    func ambiguousBareCategoryOpenStaysHandsOff() async throws {
        try await L10n.$overrideLanguage.withValue(.zhHans) {
            let suite = "test.intent.bare.open.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let ceiling = SmartDevice(
                id: "study_ceiling", name: "书房吸顶灯", roomId: "study", roomName: "书房",
                model: "yeelink.light.ceiling20", isOnline: true,
                kind: .light(LightSpec(entityId: "light.study_ceiling", isOn: false))
            )
            let desk = SmartDevice(
                id: "study_desk", name: "书房台灯", roomId: "study", roomName: "书房",
                model: "yeelink.light.lamp4", isOnline: true,
                kind: .light(LightSpec(entityId: "light.study_desk", isOn: false))
            )
            let provider = ControlledIntentProvider()
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            let intent = AskAssistantIntent()
            intent.text = "开灯"
            let invocation = Task { try await intent.execute(in: store) }
            await Task.yield()
            await provider.sendSnapshot(SynthesizedHome(devices: [ceiling, desk]))

            let message = try await invocation.value
            #expect(message == "关着：书房吸顶灯、书房台灯。请说出房间或设备名")
            #expect(await provider.commandCount == 0)
            #expect(store.devices.allSatisfy { !$0.isActiveOn })
        }
    }

    @Test("URLCommandHandler runs trusted bridgeToken silently without stealing focus, while preserving hazard confirmation")
    func urlCommandHandlerBridgeTokenAndHazardGate() async throws {
        try await L10n.$overrideLanguage.withValue(.zhHans) {
            let suite = "test.url.bridge.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let desk = SmartDevice(
                id: "study_desk", name: "书房台灯", roomId: "study", roomName: "书房",
                model: "yeelink.light.lamp4", isOnline: true,
                kind: .light(LightSpec(
                    entityId: "light.study_desk",
                    isOn: false,
                    brightnessPercent: 80,
                    colorTempKelvin: 4000,
                    minKelvin: 2700,
                    maxKelvin: 6500
                ))
            )
            let heater = SmartDevice(
                id: "bath_heater", name: "浴室取暖器插座", roomId: "bath", roomName: "卫生间",
                model: "chuangmi.plug.212a01", isOnline: true,
                kind: .plug(PlugSpec(switchEntityId: "switch.bath_heater", isOn: false))
            )
            let provider = ControlledIntentProvider(autoComplete: true)
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            await provider.sendSnapshot(SynthesizedHome(devices: [desk, heater]))
            try await store.waitForInitialSnapshot()

            let handler = URLCommandHandler()
            var externalAlertsCount = 0
            var windowOpenCount = 0
            var manualHazardConfirmCount = 0
            handler.confirmationHandler = { _ in
                externalAlertsCount += 1
                return false
            }
            handler.windowVisibilityHandler = {
                windowOpenCount += 1
            }
            store.manualControlConfirmationHandler = { _ in
                manualHazardConfirmCount += 1
                return false
            }

            store.setRaycastIntegration(enabled: true)
            let token = try #require(store.raycastBridgeToken)

            // 1. Untrusted link without token triggers external URL confirmation and is rejected
            let untrustedURL = try #require(URL(string: "menumijia://toggle?device=study_desk&id=untrusted-1"))
            await handler.handle(untrustedURL, in: store)
            #expect(externalAlertsCount == 1)
            #expect(windowOpenCount == 0)
            #expect(store.lastRaycastCommandFeedback?.requestId == "untrusted-1")
            #expect(store.lastRaycastCommandFeedback?.isError == true)

            // 2. Trusted link with valid bridgeToken skips external URL confirmation and does NOT open window
            let trustedToggleURL = try #require(
                URL(string: "menumijia://toggle?device=study_desk&token=\(token)&id=trusted-toggle-1")
            )
            await handler.handle(trustedToggleURL, in: store)
            #expect(externalAlertsCount == 1) // unchanged
            #expect(windowOpenCount == 0) // did not steal Raycast focus
            #expect(store.devices.first(where: { $0.id == "study_desk" })?.isActiveOn == true)
            #expect(store.lastRaycastCommandFeedback?.requestId == "trusted-toggle-1")
            #expect(store.lastRaycastCommandFeedback?.isError == false)

            // 3. Trusted built-in quick action (`quick:softNightLight`) executes and records feedback
            let quickURL = try #require(
                URL(string: "menumijia://scene?name=quick:softNightLight&token=\(token)&id=trusted-quick-1")
            )
            await handler.handle(quickURL, in: store)
            #expect(windowOpenCount == 0)
            #expect(store.lastRaycastCommandFeedback?.requestId == "trusted-quick-1")
            #expect(store.lastRaycastCommandFeedback?.isError == false)

            // 4. High-risk device (`bath_heater`) STILL requires ManualControlPolicy confirmation even with valid bridgeToken
            let hazardURL = try #require(
                URL(string: "menumijia://toggle?device=bath_heater&token=\(token)&id=trusted-hazard-1")
            )
            await handler.handle(hazardURL, in: store)
            #expect(externalAlertsCount == 1) // URL link alert skipped
            #expect(manualHazardConfirmCount == 1) // Manual hazard confirmation enforced!
            #expect(store.devices.first(where: { $0.id == "bath_heater" })?.isActiveOn == false)
            #expect(store.lastRaycastCommandFeedback?.requestId == "trusted-hazard-1")
            #expect(store.lastRaycastCommandFeedback?.isError == true)
            #expect(store.lastRaycastCommandFeedback?.message.contains("已取消") == true)
        }
    }

    @Test("URLCommandHandler deterministically executes all Raycast device presets offline without cloud AI")
    func urlCommandHandlerExecutesAllRaycastDevicePresetsOffline() async throws {
        try await L10n.$overrideLanguage.withValue(.zhHans) {
            let suite = "test.url.presets.\(UUID().uuidString)"
            defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
            let desk = SmartDevice(
                id: "study_desk",
                name: "书房台灯",
                roomId: "study",
                roomName: "书房",
                model: "yeelink.light.lamp4",
                isOnline: true,
                kind: .light(LightSpec(
                    entityId: "light.study_desk",
                    isOn: false,
                    brightnessPercent: 50,
                    colorTempKelvin: 4000,
                    brightnessWritable: true,
                    colorWritable: true
                ))
            )
            let monoLamp = SmartDevice(
                id: "bed_mono",
                name: "床头阅读灯",
                roomId: "bedroom",
                roomName: "主卧",
                model: "yeelink.light.mono1",
                isOnline: true,
                kind: .light(LightSpec(
                    entityId: "light.bed_mono",
                    isOn: false,
                    brightnessPercent: 80,
                    colorTempKelvin: nil,
                    brightnessWritable: true,
                    colorWritable: false
                ))
            )
            let ac = SmartDevice(
                id: "living_ac",
                name: "客厅空调",
                roomId: "living",
                roomName: "客厅",
                model: "xiaomi.aircondition.mc5",
                isOnline: true,
                kind: .climate(ClimateSpec(
                    entityId: "climate.living_ac",
                    isOn: false,
                    hvacMode: "off",
                    availableModes: ["off", "cool", "heat", "auto"],
                    targetTemperature: 26,
                    currentTemperature: 27.5
                ))
            )
            let curtain = SmartDevice(
                id: "living_curtain",
                name: "客厅电动窗帘",
                roomId: "living",
                roomName: "客厅",
                model: "lumi.curtain.hagl05",
                isOnline: true,
                kind: .cover(CoverSpec(
                    entityId: "cover.living_curtain",
                    subtype: .curtain,
                    positionPercent: 0,
                    positionWritable: true,
                    openDescription: "Open",
                    closeDescription: "Close",
                    pauseDescription: "Pause"
                ))
            )
            let fan = SmartDevice(
                id: "bed_purifier",
                name: "主卧空气净化器",
                roomId: "bedroom",
                roomName: "主卧",
                model: "zhimi.airpurifier.mb4",
                isOnline: true,
                kind: .fan(FanSpec(
                    entityId: "fan.bed_purifier",
                    isOn: false,
                    percentage: 20,
                    presetMode: "Auto",
                    availablePresetModes: ["Auto", "Silent", "Favorite"],
                    speedWritable: true
                ))
            )
            let vacuum = SmartDevice(
                id: "roborock",
                name: "石头扫拖机器人",
                roomId: "living",
                roomName: "客厅",
                model: "roborock.vacuum.a15",
                isOnline: true,
                kind: .vacuum(VacuumSpec(
                    entityId: "vacuum.roborock",
                    status: "docked",
                    battery: 100,
                    actionsWritable: true
                ))
            )

            let provider = ControlledIntentProvider(autoComplete: true)
            let store = HomeStore(provider: provider, defaultsSuiteName: suite)
            store.manualControlConfirmationHandler = { _ in true }
            await provider.sendSnapshot(
                SynthesizedHome(devices: [desk, monoLamp, ac, curtain, fan, vacuum])
            )
            try await store.waitForInitialSnapshot()

            let handler = URLCommandHandler()
            store.setRaycastIntegration(enabled: true)
            let token = try #require(store.raycastBridgeToken)

            let presetCommands: [(id: String, text: String)] = [
                ("preset-bright", "把书房台灯亮度调到50%"),
                ("preset-ct", "把书房台灯色温调到2700K"),
                ("preset-moon-color", "把书房台灯调到15%亮度2700K"),
                ("preset-moon-mono", "把床头阅读灯调到15%亮度2700K"),
                ("preset-ac-temp", "把客厅空调调到24度"),
                ("preset-ac-mode", "把客厅空调设为制冷模式"),
                ("preset-cover-open", "打开客厅电动窗帘"),
                ("preset-cover-half", "把客厅电动窗帘开到50%"),
                ("preset-cover-close", "关闭客厅电动窗帘"),
                ("preset-fan-speed", "把主卧空气净化器风速调到60%"),
                ("preset-fan-auto", "把主卧空气净化器设为自动模式"),
                ("preset-fan-sleep", "把主卧空气净化器设为睡眠模式"),
                ("preset-vac-start", "打开石头扫拖机器人"),
                ("preset-vac-pause", "暂停石头扫拖机器人"),
                ("preset-vac-dock", "让石头扫拖机器人回充")
            ]

            for item in presetCommands {
                var comps = URLComponents()
                comps.scheme = "menumijia"
                comps.host = "ask"
                comps.queryItems = [
                    URLQueryItem(name: "text", value: item.text),
                    URLQueryItem(name: "token", value: token),
                    URLQueryItem(name: "id", value: item.id)
                ]
                let url = try #require(comps.url)
                await handler.handle(url, in: store)
                #expect(
                    store.lastRaycastCommandFeedback?.requestId == item.id,
                    "Expected feedback for \(item.id) (\(item.text))"
                )
                #expect(
                    store.lastRaycastCommandFeedback?.isError == false,
                    "Expected non-error for \(item.id) (\(item.text)), got: \(store.lastRaycastCommandFeedback?.message ?? "nil")"
                )
            }
        }
    }
}

private actor ControlledIntentProvider: HomeProvider {
    nonisolated let stream: AsyncStream<HomeProviderEvent>
    private let continuation: AsyncStream<HomeProviderEvent>.Continuation
    private let autoComplete: Bool
    private var pending: CheckedContinuation<Void, any Error>?
    private var commandWaiters: [(count: Int, continuation: CheckedContinuation<Void, Never>)] = []
    private(set) var commandCount = 0

    init(autoComplete: Bool = false) {
        self.autoComplete = autoComplete
        (stream, continuation) = AsyncStream.makeStream()
    }

    nonisolated func events() -> AsyncStream<HomeProviderEvent> { stream }
    func refreshNow() async {}

    func sendSnapshot(_ home: SynthesizedHome) {
        continuation.yield(.snapshotUpdated(home))
    }

    func waitForCommand(_ count: Int) async {
        guard commandCount < count || pending == nil else { return }
        await withCheckedContinuation { commandWaiters.append((count, $0)) }
    }

    func execute(_ command: DeviceCommand) async throws {
        commandCount += 1
        if autoComplete {
            return
        }
        try await withCheckedThrowingContinuation { cont in
            pending = cont
            let ready = commandWaiters.filter { $0.count <= commandCount }
            commandWaiters.removeAll { $0.count <= commandCount }
            for waiter in ready { waiter.continuation.resume() }
        }
    }

    func complete(failing: Bool) {
        if failing {
            pending?.resume(throwing: HomeProviderError.networkError("Test rejection"))
        } else {
            pending?.resume()
        }
        pending = nil
    }
}
