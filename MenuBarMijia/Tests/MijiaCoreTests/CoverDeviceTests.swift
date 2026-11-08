import Foundation
import Testing
@testable import MijiaCore

/// #278: a cover in the open position is not an ActiveDevice, so it must stay out of the panel and
/// menu-bar counts while 全关 keeps ignoring it.
struct CoverDeviceTests {
    static let light = SmartDevice(
        id: "light_study", name: "书房灯", roomId: "study", roomName: "书房",
        model: "yeelink.light.lamp4", isOnline: true,
        kind: .light(LightSpec(entityId: "light.study", isOn: true))
    )

    static let openCover = SmartDevice(
        id: "wd1", name: "卧室窗户", roomId: "bed", roomName: "卧室",
        model: "linp.wopener.wd1lb", isOnline: true,
        kind: .cover(CoverSpec(
            entityId: "miot:wd1:2",
            subtype: .windowOpener,
            positionPercent: 42,
            positionWritable: true,
            openDescription: "Open",
            closeDescription: "Close",
            pauseDescription: "Pause"
        ))
    )

    @MainActor
    private func makeStore(_ devices: [SmartDevice]) async throws -> (HomeStore, MockHomeProvider) {
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: devices))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.cover.\(UUID().uuidString)")
        try await store.waitForTestSnapshot()
        #expect(store.devices.count == devices.count)
        return (store, mock)
    }

    @Test("An open cover is not counted as an active device on the panel or menu bar")
    @MainActor
    func openCoverIsNotActive() async throws {
        let (store, _) = try await makeStore([Self.light, Self.openCover])
        #expect(store.devices.first { $0.id == Self.openCover.id }?.isActiveOn == true)

        #expect(store.activeDeviceCount == 1)
        store.menuBarTargetId = "active:count"
        #expect(store.menuBarText == "1开")
        store.menuBarDisplayMode = .activeCount
        #expect(store.menuBarText == "1开")
    }

    @Test("Assistant and GrokBall active summaries follow the ActiveDevice rule")
    @MainActor
    func activeSummariesIgnoreOpenCover() async throws {
        let (store, _) = try await makeStore([Self.openCover])
        #expect(store.buildAssistantContext(mention: .none).activeDevicesSummary == "全屋设备均关闭")

        let lateNight = try #require(Calendar.current.date(
            bySettingHour: 23, minute: 30, second: 0, of: Date()
        ))
        #expect(store.grokBallEmotionId(
            speechListening: false, isPointerTracking: false, now: lateNight
        ) == GrokBallEmotionResolver.sleeping)
    }

    @Test("全关 leaves an open cover alone")
    @MainActor
    func allOffIgnoresOpenCover() async throws {
        let (store, _) = try await makeStore([Self.light, Self.openCover])
        #expect(!store.allOffTargets.contains { $0.id == Self.openCover.id })

        store.turnAllOff()
        #expect(store.devices.first { $0.id == Self.light.id }?.isActiveOn == false)
        #expect(store.devices.first { $0.id == Self.openCover.id }?.isActiveOn == true)
    }

    // MARK: - Home Assistant cover synthesis & controls (#280)

    private struct HASnapshotFixture: Decodable {
        struct RawState: Decodable {
            let entity_id: String
            let state: String
            let attributes: [String: JSONValue]
        }
        let areas: [HAAreaEntry]
        let devices: [HADeviceEntry]
        let entities: [HAEntityEntry]
        let states: [RawState]
    }

    private func fixtureURL(_ relativePath: String) throws -> URL {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = directory.appendingPathComponent(relativePath)
            if FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func loadHACoverSnapshot() throws -> (SynthesizedHome, HASnapshotFixture, [String: HAEntityState]) {
        let url = try fixtureURL("Tests/Fixtures/ha_cover_snapshot.json")
        let raw = try JSONDecoder().decode(HASnapshotFixture.self, from: Data(contentsOf: url))
        let stateMap = Dictionary(
            uniqueKeysWithValues: raw.states.map {
                ($0.entity_id, HAEntityState(entityId: $0.entity_id, state: $0.state, attributes: $0.attributes))
            }
        )
        let home = DeviceSynthesizer.synthesize(
            areas: raw.areas,
            devices: raw.devices,
            entities: raw.entities,
            states: stateMap
        )
        return (home, raw, stateMap)
    }

    @Test("Home Assistant cover.* fixture maps every device_class, name fallback, and default subtype")
    func haCoverSnapshotSubtypeMapping() throws {
        let (home, raw, stateMap) = try loadHACoverSnapshot()
        let byId = Dictionary(uniqueKeysWithValues: home.devices.map { ($0.id, $0) })

        let expectedSubtypes: [(id: String, expected: CoverSubtype)] = [
            // Explicit device_class -> .curtain
            ("ha_class_curtain", .curtain),
            ("ha_class_blind", .curtain),
            ("ha_class_shade", .curtain),
            ("ha_class_shutter", .curtain),
            ("ha_class_awning", .curtain),
            ("ha_class_damper", .curtain),
            // Explicit device_class -> .windowOpener
            ("ha_class_window", .windowOpener),
            // Explicit device_class -> .garageDoor
            ("ha_class_garage", .garageDoor),
            ("ha_class_gate", .garageDoor),
            ("ha_class_door", .garageDoor),
            // Missing device_class -> name keyword inference
            ("ha_name_cn_curtain", .curtain),
            ("ha_name_en_curtain", .curtain),
            ("ha_name_en_blind", .curtain),
            ("ha_name_cn_garage", .garageDoor),
            ("ha_name_cn_gate", .garageDoor),
            ("ha_name_en_garage", .garageDoor),
            ("ha_name_en_gate", .garageDoor),
            ("ha_name_cn_window", .windowOpener),
            ("ha_name_en_window", .windowOpener),
            // Unrecognized device_class and name -> .windowOpener conservative default
            ("ha_fallback_unknown", .windowOpener),
        ]

        #expect(home.devices.count == expectedSubtypes.count)
        for item in expectedSubtypes {
            let device = try #require(byId[item.id], "Missing synthesized cover \(item.id)")
            guard case .cover(let spec) = device.kind else {
                Issue.record("\(item.id) did not synthesize as .cover")
                continue
            }
            #expect(spec.subtype == item.expected, "\(item.id) expected \(item.expected), got \(spec.subtype)")
        }

        let curtain = try #require(byId["ha_class_curtain"])
        #expect(curtain.name == "客厅布帘", "Verbose duplicate '  窗帘' suffix is stripped")
        if case .cover(let spec) = curtain.kind {
            #expect(spec.entityId == "cover.class_curtain")
            #expect(spec.positionPercent == 60)
            #expect(spec.positionWritable)
            #expect(spec.batteryPercent == 88)
            #expect(spec.openDescription == "Open")
            #expect(spec.closeDescription == "Close")
            #expect(spec.pauseDescription == "Pause")
        }

        let unsupported = UnsupportedDeviceCollector.homeAssistant(
            devices: raw.devices,
            entities: raw.entities,
            states: stateMap,
            synthesized: home
        )
        #expect(unsupported.isEmpty)
    }

    @Test("Garage/gate confirms open and close, window confirms open only, curtain confirms neither, and stop never confirms")
    @MainActor
    func haCoverRiskConfirmationAndStopRules() async throws {
        let (home, _, _) = try loadHACoverSnapshot()
        let byId = Dictionary(uniqueKeysWithValues: home.devices.map { ($0.id, $0) })
        let curtain = try #require(byId["ha_class_curtain"])
        let window = try #require(byId["ha_class_window"])
        let fallback = try #require(byId["ha_fallback_unknown"])
        let garage = try #require(byId["ha_class_garage"])
        let gate = try #require(byId["ha_class_gate"])

        // 1. Curtain: neither open nor close prompts; tapping a moving curtain stops it without prompting.
        let (curtainStore, curtainMock) = try await makeStore([curtain])
        var prompts = 0
        curtainStore.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }
        // Curtain starts at 60% (open) -> tap closes it without confirmation
        let curtainCard = try #require(curtainStore.devices.first)
        #expect(await curtainStore.togglePrimary(curtainCard)?.value == true)
        #expect(prompts == 0)
        #expect(await curtainMock.executedCommands == [.setSelectOption(entityId: "cover.class_curtain", option: "Close")])

        // Curtain is now moving (.closing) -> next tap stops it without confirmation
        let movingCurtain = try #require(curtainStore.devices.first)
        #expect(await curtainStore.togglePrimary(movingCurtain)?.value == true)
        #expect(prompts == 0)
        #expect(await curtainMock.executedCommands.last == .setSelectOption(entityId: "cover.class_curtain", option: "Pause"))

        // 2. Window opener & unrecognized fallback: open prompts, close does not.
        for candidate in [window, fallback] {
            let (winStore, winMock) = try await makeStore([candidate])
            var winPrompts = 0
            winStore.manualControlConfirmationHandler = { _ in
                winPrompts += 1
                return false
            }
            let closedWin = try #require(winStore.devices.first)
            #expect(winStore.togglePrimary(closedWin) == nil, "Cancelled open on \(candidate.id) must not execute")
            #expect(winPrompts == 1)
            #expect(await winMock.executedCommands.isEmpty)

            winStore.manualControlConfirmationHandler = { _ in
                winPrompts += 1
                return true
            }
            #expect(await winStore.togglePrimary(closedWin)?.value == true)
            #expect(winPrompts == 2)

            let openWin = try #require(winStore.devices.first)
            winStore.manualControlConfirmationHandler = { _ in
                winPrompts += 1
                return false
            }
            #expect(await winStore.togglePrimary(openWin)?.value == true, "Closing \(candidate.id) must not ask")
            #expect(winPrompts == 2)
        }

        // 3. Garage door (closed) & gate (open): both open and close require confirmation; pause never asks.
        let (garageStore, garageMock) = try await makeStore([garage, gate])
        var garagePrompts = 0
        garageStore.manualControlConfirmationHandler = { _ in
            garagePrompts += 1
            return false
        }
        let closedGarage = try #require(garageStore.devices.first { $0.id == "ha_class_garage" })
        let openGate = try #require(garageStore.devices.first { $0.id == "ha_class_gate" })
        #expect(garageStore.togglePrimary(closedGarage) == nil, "Opening garage door must ask")
        #expect(garageStore.togglePrimary(openGate) == nil, "Closing gate must ask")
        #expect(garagePrompts == 2)
        #expect(await garageMock.executedCommands.isEmpty)

        // Drawer Pause on garage door does not ask for confirmation
        garageStore.setCoverMotor("ha_class_garage", description: "Pause")
        for _ in 0..<50 where await garageMock.executedCommands.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(garagePrompts == 2)
        #expect(await garageMock.executedCommands == [.setSelectOption(entityId: "cover.class_garage", option: "Pause")])

        // Approving open on garage and close on gate dispatches both commands
        garageStore.manualControlConfirmationHandler = { _ in
            garagePrompts += 1
            return true
        }
        #expect(await garageStore.togglePrimary(closedGarage)?.value == true)
        #expect(await garageStore.togglePrimary(openGate)?.value == true)
        #expect(garagePrompts == 4)
        let cmds = await garageMock.executedCommands
        #expect(cmds.contains(.setSelectOption(entityId: "cover.class_garage", option: "Open")))
        #expect(cmds.contains(.setSelectOption(entityId: "cover.class_gate", option: "Close")))
    }

    @Test("Positionless cover exposes open/pause/close only and HAWebSocketProvider maps cover.* services")
    @MainActor
    func positionlessCoverAndHAServiceMapping() async throws {
        let (home, _, _) = try loadHACoverSnapshot()
        let shutter = try #require(home.devices.first { $0.id == "ha_class_shutter" })
        guard case .cover(let spec) = shutter.kind else {
            Issue.record("shutter did not synthesize as .cover")
            return
        }
        #expect(!spec.positionWritable, "supported_features 11 (OPEN|CLOSE|STOP) has no SET_POSITION")
        #expect(spec.positionPercent == 0, "state 'closed' maps to 0% for toggle direction")
        #expect(spec.openDescription == "Open")
        #expect(spec.pauseDescription == "Pause")
        #expect(spec.closeDescription == "Close")
        #expect(spec.canMove)
        #expect(shutter.hasDetailDrawer)

        let (store, mock) = try await makeStore([shutter])
        store.controlDebounceInterval = .zero
        store.setCoverPosition(shutter.id, percent: 50)
        try await Task.sleep(for: .milliseconds(30))
        #expect(await mock.executedCommands.isEmpty, "Positionless cover ignores slider writes")

        store.setCoverMotor(shutter.id, description: "Open")
        for _ in 0..<50 where await mock.executedCommands.count < 1 {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.setCoverMotor(shutter.id, description: "Pause")
        for _ in 0..<50 where await mock.executedCommands.count < 2 {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.setCoverMotor(shutter.id, description: "Close")
        for _ in 0..<50 where await mock.executedCommands.count < 3 {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(await mock.executedCommands == [
            .setSelectOption(entityId: "cover.class_shutter", option: "Open"),
            .setSelectOption(entityId: "cover.class_shutter", option: "Pause"),
            .setSelectOption(entityId: "cover.class_shutter", option: "Close"),
        ])

        let openSvc = HAWebSocketProvider.mapCommandToService(.setSelectOption(entityId: "cover.class_shutter", option: "Open"))
        #expect(openSvc.domain == "cover" && openSvc.service == "open_cover")
        #expect(openSvc.payload["entity_id"] as? String == "cover.class_shutter")

        let closeSvc = HAWebSocketProvider.mapCommandToService(.setSelectOption(entityId: "cover.class_shutter", option: "Close"))
        #expect(closeSvc.domain == "cover" && closeSvc.service == "close_cover")
        #expect(closeSvc.payload["entity_id"] as? String == "cover.class_shutter")

        let stopSvc = HAWebSocketProvider.mapCommandToService(.setSelectOption(entityId: "cover.class_shutter", option: "Pause"))
        #expect(stopSvc.domain == "cover" && stopSvc.service == "stop_cover")
        #expect(stopSvc.payload["entity_id"] as? String == "cover.class_shutter")

        let posSvc = HAWebSocketProvider.mapCommandToService(.setNumberValue(entityId: "cover.class_curtain", value: 64))
        #expect(posSvc.domain == "cover" && posSvc.service == "set_cover_position")
        #expect(posSvc.payload["entity_id"] as? String == "cover.class_curtain")
        #expect(posSvc.payload["position"] as? Int == 64)
    }

    @Test("A curtain present as cover.* in Home Assistant and lumi.curtain.hagl05 in Mijia merges into one card")
    func dualSourceHACoverAndMiCurtainMergeIntoOneCard() throws {
        let (haFullHome, _, _) = try loadHACoverSnapshot()
        let haCurtain = try #require(haFullHome.devices.first { $0.id == "ha_class_curtain" })
        let haIdentity = try #require(haFullHome.deviceIdentities["ha_class_curtain"])
        var haHome = SynthesizedHome(devices: [haCurtain])
        haHome.deviceIdentities = ["ha_class_curtain": haIdentity]

        let miSpecURL = try fixtureURL("Tests/Fixtures/miot/lumi.curtain.hagl05.json")
        let miSpec = try JSONDecoder().decode(MiotDeviceSpec.self, from: Data(contentsOf: miSpecURL))
        let miRecord = MiDeviceRecord(did: "curtain1", name: "客厅布帘", model: "lumi.curtain.hagl05", isOnline: true)
        let miHome = MiotSynthesizer.synthesize(
            devices: [miRecord],
            rooms: [],
            specs: [miRecord.model: miSpec],
            values: [
                .init(did: "curtain1", siid: 2, piid: 3): .number(60),
                .init(did: "curtain1", siid: 2, piid: 6): .number(0),
            ]
        )
        #expect(miHome.devices.count == 1)

        let merged = DeviceDeduplicator.merge(homeAssistant: haHome, xiaomiCloud: miHome)
        #expect(merged.home.devices.count == 1)
        let card = try #require(merged.home.devices.first)
        #expect(merged.home.pairedDeviceIDs.contains(card.id))
        guard case .cover(let cover) = card.kind else {
            Issue.record("Merged card is not a .cover")
            return
        }
        #expect(cover.subtype == .curtain)
    }

    @Test("A Home Assistant device with multiple cover.* entities splits into one card per entity and respects SET_POSITION-only features")
    @MainActor
    func haMultiCoverDeviceSplitsAndRespectsFeatureBits() async throws {
        let dualDevice = HADeviceEntry(id: "ha_dual", area_id: "bedroom", name: "卧室双轨窗帘", model: "czmydz.curtain.myf5")
        let entities = [
            HAEntityEntry(entity_id: "cover.drape", device_id: "ha_dual", name: "卧室布帘"),
            HAEntityEntry(entity_id: "cover.sheer", device_id: "ha_dual", name: "卧室纱帘"),
            HAEntityEntry(entity_id: "sensor.drape_battery", device_id: "ha_dual", name: "卧室布帘 电量"),
            HAEntityEntry(entity_id: "sensor.sheer_battery", device_id: "ha_dual", name: "卧室纱帘 电量"),
        ]
        let states: [String: HAEntityState] = [
            "cover.drape": HAEntityState(
                entityId: "cover.drape",
                state: "closed",
                attributes: ["device_class": .string("curtain"), "current_position": .number(0), "supported_features": .number(4)]
            ),
            "cover.sheer": HAEntityState(
                entityId: "cover.sheer",
                state: "open",
                attributes: ["device_class": .string("curtain"), "current_position": .number(80), "supported_features": .number(15)]
            ),
            "sensor.drape_battery": HAEntityState(
                entityId: "sensor.drape_battery",
                state: "72",
                attributes: ["device_class": .string("battery")]
            ),
            "sensor.sheer_battery": HAEntityState(
                entityId: "sensor.sheer_battery",
                state: "41",
                attributes: ["device_class": .string("battery")]
            ),
        ]
        let home = DeviceSynthesizer.synthesize(
            areas: [HAAreaEntry(area_id: "bedroom", name: "卧室")],
            devices: [dualDevice],
            entities: entities,
            states: states
        )
        #expect(home.devices.count == 2)
        let drape = try #require(home.devices.first { $0.id == "ha_dual#cover.drape" })
        let sheer = try #require(home.devices.first { $0.id == "ha_dual#cover.sheer" })
        #expect(drape.name == "卧室布帘")
        #expect(sheer.name == "卧室纱帘")

        if case .cover(let drapeSpec) = drape.kind {
            #expect(drapeSpec.positionWritable, "supported_features 4 (SET_POSITION) enables position slider")
            #expect(drapeSpec.openDescription == nil)
            #expect(drapeSpec.closeDescription == nil)
            #expect(drapeSpec.pauseDescription == nil)
            #expect(drapeSpec.batteryPercent == 72)
        } else {
            Issue.record("drape did not synthesize as .cover")
        }
        if case .cover(let sheerSpec) = sheer.kind {
            #expect(sheerSpec.batteryPercent == 41)
        } else {
            Issue.record("sheer did not synthesize as .cover")
        }

        let unsupported = UnsupportedDeviceCollector.homeAssistant(
            devices: [dualDevice],
            entities: entities,
            states: states,
            synthesized: home
        )
        #expect(unsupported.isEmpty)

        // Tapping a SET_POSITION-only cover expands the drawer instead of sending open_cover/close_cover
        let (store, mock) = try await makeStore([drape, sheer])
        let drapeCard = try #require(store.devices.first { $0.id == "ha_dual#cover.drape" })
        #expect(store.togglePrimary(drapeCard) == nil)
        #expect(store.expandedDeviceId == "ha_dual#cover.drape")
        #expect(await mock.executedCommands.isEmpty)
    }
}
