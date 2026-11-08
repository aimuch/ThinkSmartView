import Foundation
import Testing
@testable import MijiaCore

/// #279: a MIoT `curtain` motor is a cover card, a dual-track motor is one card per curtain
/// service, and both Mijia-direct and Home Assistant copies of the same motor collapse to one card.
/// The fixtures are verbatim released instances from miot-spec.org: `lumi.curtain.hagl05` is a
/// single track; `czmydz.curtain.myf5` publishes a "Drape Curtain" and a "Lace Curtain" service.
struct CurtainDeviceTests {
    static let singleModel = "lumi.curtain.hagl05"
    static let dualModel = "czmydz.curtain.myf5"

    // MARK: Fixtures

    private func spec(_ model: String) throws -> MiotDeviceSpec {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        // Linux copies test sources into scripts/linux-check/.work; fixtures stay at the repo root.
        for _ in 0..<8 {
            let fixture = directory.appendingPathComponent("Tests/Fixtures/miot/\(model).json")
            if FileManager.default.fileExists(atPath: fixture.path) {
                return try JSONDecoder().decode(MiotDeviceSpec.self, from: Data(contentsOf: fixture))
            }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func singleTrackHome(
        did: String = "curtain1",
        position: Int? = 42,
        status: Int = 0
    ) throws -> (SmartDevice, MiotDeviceSpec, MiDeviceRecord) {
        let spec = try spec(Self.singleModel)
        let record = MiDeviceRecord(did: did, name: "客厅窗帘", model: Self.singleModel, isOnline: true)
        var values: [MiotPropKey: JSONValue] = [
            .init(did: did, siid: 2, piid: 6): .number(Double(status)),
        ]
        if let position {
            values[.init(did: did, siid: 2, piid: 3)] = .number(Double(position))
            values[.init(did: did, siid: 2, piid: 7)] = .number(Double(position))
        }
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec], values: values
        )
        let device = try #require(home.devices.first)
        return (device, spec, record)
    }

    private func dualTrackHome(
        drapePosition: Int = 20,
        lacePosition: Int = 90
    ) throws -> (SmartDevice, SmartDevice, MiotDeviceSpec) {
        let spec = try spec(Self.dualModel)
        let record = MiDeviceRecord(did: "dual1", name: "卧室窗帘", model: Self.dualModel, isOnline: true)
        let values: [MiotPropKey: JSONValue] = [
            .init(did: "dual1", siid: 2, piid: 2): .number(2),
            .init(did: "dual1", siid: 2, piid: 3): .number(Double(drapePosition)),
            .init(did: "dual1", siid: 2, piid: 4): .number(Double(drapePosition)),
            .init(did: "dual1", siid: 2, piid: 10): .number(1),
            .init(did: "dual1", siid: 3, piid: 2): .number(2),
            .init(did: "dual1", siid: 3, piid: 3): .number(Double(lacePosition)),
            .init(did: "dual1", siid: 3, piid: 4): .number(Double(lacePosition)),
        ]
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec], values: values
        )
        #expect(home.devices.count == 2)
        let drape = try #require(home.devices.first { $0.id == "dual1.s2" })
        let lace = try #require(home.devices.first { $0.id == "dual1.s3" })
        return (drape, lace, spec)
    }

    // MARK: Synthesis

    @Test("A real single-track curtain becomes a curtain cover card with the spec's motor words")
    func singleTrackCurtainCard() throws {
        let (device, spec, record) = try singleTrackHome()
        guard case .cover(let cover) = device.kind else {
            Issue.record("curtain was not synthesized as a cover")
            return
        }
        #expect(device.id == "curtain1")
        #expect(device.name == "客厅窗帘")
        #expect(cover.subtype == .curtain)
        #expect(cover.entityId == "miot:curtain1:2")
        #expect(cover.positionPercent == 42)
        #expect(cover.positionWritable)
        #expect(cover.openDescription == "Open")
        #expect(cover.closeDescription == "Close")
        #expect(cover.pauseDescription == "Pause")
        #expect(cover.faultText == nil)
        #expect(device.hasDetailDrawer)
        #expect(device.statusSubtitle == "开 42%")

        let props = MiotSynthesizer.requiredProps(for: record, spec: spec)
        #expect(props.contains(.init(did: "curtain1", siid: 2, piid: 6)), "status is polled for motion")
        #expect(props.contains(.init(did: "curtain1", siid: 2, piid: 3)), "current position is polled")
        #expect(props.contains(.init(did: "curtain1", siid: 2, piid: 1)), "the spec's fault property is polled")
        #expect(!props.contains(.init(did: "curtain1", siid: 2, piid: 2)), "write-only motor-control is not polled")

        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: "miot:curtain1:2", option: "Open"), spec: spec)
            == .setProps([.init(did: "curtain1", siid: 2, piid: 2, value: .number(1))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: "miot:curtain1:2", option: "Close"), spec: spec)
            == .setProps([.init(did: "curtain1", siid: 2, piid: 2, value: .number(2))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: "miot:curtain1:2", option: "Pause"), spec: spec)
            == .setProps([.init(did: "curtain1", siid: 2, piid: 2, value: .number(0))]))
        #expect(try MiotCommandMapper.map(.setNumberValue(entityId: "miot:curtain1:2", value: 37), spec: spec)
            == .setProps([.init(did: "curtain1", siid: 2, piid: 7, value: .number(37))]))
    }

    @Test("A real dual-track curtain splits into one card per service, named by the service description")
    func dualTrackCurtainSplitsPerTrack() throws {
        let (drape, lace, spec) = try dualTrackHome()

        #expect(drape.name == "布帘")
        #expect(lace.name == "纱帘")
        guard case .cover(let drapeCover) = drape.kind, case .cover(let laceCover) = lace.kind else {
            Issue.record("dual-track curtain tracks were not synthesized as covers")
            return
        }
        #expect(drapeCover.subtype == .curtain)
        #expect(laceCover.subtype == .curtain)
        #expect(drapeCover.entityId == "miot:dual1:2")
        #expect(laceCover.entityId == "miot:dual1:3")
        #expect(drapeCover.positionPercent == 20)
        #expect(laceCover.positionPercent == 90)
        // The real spec reports its healthy state as code 1, "No Fault".
        #expect(drapeCover.faultText == nil)

        let record = MiDeviceRecord(did: "dual1", name: "卧室窗帘", model: Self.dualModel, isOnline: true)
        let props = MiotSynthesizer.requiredProps(for: record, spec: spec)
        #expect(props.contains(.init(did: "dual1", siid: 2, piid: 3)), "the drape track is polled")
        #expect(props.contains(.init(did: "dual1", siid: 3, piid: 3)), "the sheer track is polled")
        #expect(props.contains(.init(did: "dual1", siid: 2, piid: 10)), "the drape fault is polled")

        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: "miot:dual1:3", option: "Close"), spec: spec)
            == .setProps([.init(did: "dual1", siid: 3, piid: 1, value: .number(1))]))
        #expect(try MiotCommandMapper.map(.setNumberValue(entityId: "miot:dual1:2", value: 55), spec: spec)
            == .setProps([.init(did: "dual1", siid: 2, piid: 4, value: .number(55))]))
    }

    @Test("The unsupported-device scan no longer lists a curtain")
    func curtainIsNotUnsupported() throws {
        let single = try spec(Self.singleModel)
        let dual = try spec(Self.dualModel)
        let records = [
            MiDeviceRecord(did: "curtain1", name: "客厅窗帘", model: Self.singleModel, isOnline: true),
            MiDeviceRecord(did: "dual1", name: "卧室窗帘", model: Self.dualModel, isOnline: true),
        ]
        let specs = [Self.singleModel: single, Self.dualModel: dual]
        let home = MiotSynthesizer.synthesize(devices: records, rooms: [], specs: specs, values: [:])

        #expect(MiotSynthesizer.supportedServiceName(for: single) == "curtain")
        #expect(UnsupportedDeviceCollector.miCloud(devices: records, specs: specs, synthesized: home).isEmpty)
    }

    // MARK: Card tap

    @MainActor
    private func makeStore(_ devices: [SmartDevice]) async throws -> (HomeStore, MockHomeProvider) {
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: devices))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.curtain.\(UUID().uuidString)")
        store.controlDebounceInterval = .zero
        try await store.waitForTestSnapshot()
        #expect(store.devices.count == devices.count)
        return (store, mock)
    }

    @MainActor
    private func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<100 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    @Test("Tapping a moving curtain stops it instead of reversing it")
    @MainActor
    func tapStopsMovingCurtain() async throws {
        let (device, _, _) = try singleTrackHome(position: 40, status: 1)
        let (store, mock) = try await makeStore([device])
        let card = try #require(store.devices.first)

        #expect(await store.togglePrimary(card)?.value == true)
        #expect(await mock.executedCommands == [.setSelectOption(entityId: "miot:curtain1:2", option: "Pause")])
        #expect(store.devices.first?.coverPosition == 40)
    }

    @Test("Tapping a moving curtain with no position reading still stops it")
    @MainActor
    func tapStopsMovingCurtainWithoutPosition() async throws {
        let (device, _, _) = try singleTrackHome(position: nil, status: 1)
        guard case .cover(let cover) = device.kind else {
            Issue.record("curtain was not synthesized as a cover")
            return
        }
        #expect(cover.positionPercent == nil)
        #expect(cover.isMoving)

        let (store, mock) = try await makeStore([device])
        #expect(await store.togglePrimary(try #require(store.devices.first))?.value == true)
        #expect(await mock.executedCommands == [.setSelectOption(entityId: "miot:curtain1:2", option: "Pause")])
        #expect(store.devices.first?.coverPosition == nil)
    }

    @Test("Tapping a closed curtain opens it and tapping an open one closes it")
    @MainActor
    func tapOpensClosedAndClosesOpenCurtain() async throws {
        let (closed, _, _) = try singleTrackHome(position: 0, status: 0)
        let closedStore = try await makeStore([closed])
        #expect(await closedStore.0.togglePrimary(try #require(closedStore.0.devices.first))?.value == true)
        #expect(await closedStore.1.executedCommands == [.setSelectOption(entityId: "miot:curtain1:2", option: "Open")])
        #expect(closedStore.0.devices.first?.coverPosition == 100)

        let (open, _, _) = try singleTrackHome(position: 65, status: 0)
        let openStore = try await makeStore([open])
        #expect(await openStore.0.togglePrimary(try #require(openStore.0.devices.first))?.value == true)
        #expect(await openStore.1.executedCommands == [.setSelectOption(entityId: "miot:curtain1:2", option: "Close")])
        #expect(openStore.0.devices.first?.coverPosition == 0)
    }

    @Test("Each dual-track card controls only its own track")
    @MainActor
    func dualTrackCardsControlOwnTrack() async throws {
        let (drape, lace, _) = try dualTrackHome(drapePosition: 0, lacePosition: 90)
        let (store, mock) = try await makeStore([drape, lace])

        let drapeCard = try #require(store.devices.first { $0.id == "dual1.s2" })
        #expect(await store.togglePrimary(drapeCard)?.value == true)
        let laceCard = try #require(store.devices.first { $0.id == "dual1.s3" })
        #expect(await store.togglePrimary(laceCard)?.value == true)

        #expect(await mock.executedCommands == [
            .setSelectOption(entityId: "miot:dual1:2", option: "Open"),
            .setSelectOption(entityId: "miot:dual1:3", option: "Close"),
        ])
        #expect(store.devices.first { $0.id == "dual1.s2" }?.coverPosition == 100)
        #expect(store.devices.first { $0.id == "dual1.s3" }?.coverPosition == 0)
    }

    // MARK: Drawer and read-back

    @Test("The cover drawer writes open, pause, close and a target position")
    @MainActor
    func drawerControlsWriteCoverCommands() async throws {
        let (closed, _, _) = try singleTrackHome(position: 0, status: 0)
        let (store, mock) = try await makeStore([closed])
        let card = try #require(store.devices.first)

        store.setCoverMotor(card.id, description: "Open")
        await waitUntil { await mock.executedCommands.count == 1 }
        #expect(await mock.executedCommands == [.setSelectOption(entityId: "miot:curtain1:2", option: "Open")])
        #expect(store.devices.first?.coverPosition == 100)

        store.setCoverMotor(card.id, description: "Pause")
        await waitUntil { await mock.executedCommands.count == 2 }
        #expect(await mock.executedCommands.last == .setSelectOption(entityId: "miot:curtain1:2", option: "Pause"))

        store.setCoverMotor(card.id, description: "Close")
        await waitUntil { await mock.executedCommands.count == 3 }
        #expect(await mock.executedCommands.last == .setSelectOption(entityId: "miot:curtain1:2", option: "Close"))
        #expect(store.devices.first?.coverPosition == 0)

        store.setCoverPosition(card.id, percent: 37)
        await waitUntil { await mock.executedCommands.count == 4 }
        #expect(await mock.executedCommands.last == .setNumberValue(entityId: "miot:curtain1:2", value: 37))
        #expect(store.devices.first?.coverPosition == 37)
    }

    @Test("Tapping a curtain after a slider move stops it instead of closing it")
    @MainActor
    func tapAfterSliderMoveStopsCurtain() async throws {
        let (closed, _, _) = try singleTrackHome(position: 0, status: 0)
        let (store, mock) = try await makeStore([closed])
        let card = try #require(store.devices.first)

        store.setCoverPosition(card.id, percent: 70)
        await waitUntil { await mock.executedCommands == [.setNumberValue(entityId: "miot:curtain1:2", value: 70)] }

        let moved = try #require(store.devices.first)
        #expect(await store.togglePrimary(moved)?.value == true)
        #expect(await mock.executedCommands.last == .setSelectOption(entityId: "miot:curtain1:2", option: "Pause"))
        #expect(store.devices.first?.coverPosition == 70)
    }

    @Test("A curtain card shows the position the device reports after a poll")
    @MainActor
    func curtainCardShowsReadBackPosition() async throws {
        let (closed, _, _) = try singleTrackHome(position: 0, status: 0)
        let (store, mock) = try await makeStore([closed])
        #expect(store.devices.first?.coverPosition == 0)

        let (opened, _, _) = try singleTrackHome(position: 73, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [opened]))
        await waitUntil { store.devices.first?.coverPosition == 73 }

        #expect(store.devices.first?.coverPosition == 73)
        #expect(store.devices.first?.statusSubtitle == "开 73%")
    }

    @Test("A dual-track motor reached through both sources shows only its two track cards")
    func dualSourceDualTrackDeduplicates() throws {
        let (drape, lace, _) = try dualTrackHome()
        var miHome = SynthesizedHome(devices: [drape, lace])
        miHome.deviceIdentities = [
            drape.id: DeviceSourceIdentity(
                dids: ["dual1"], model: Self.dualModel, name: drape.name,
                roomName: drape.roomName, kindTag: .cover
            ),
            lace.id: DeviceSourceIdentity(
                dids: ["dual1"], model: Self.dualModel, name: lace.name,
                roomName: lace.roomName, kindTag: .cover
            ),
        ]

        let haSensor = SmartDevice(
            id: "ha-dual", name: "卧室窗帘", roomId: "bed", roomName: "卧室",
            model: Self.dualModel, isOnline: true,
            kind: .sensor(SensorSpec(battery: 80))
        )
        var haHome = SynthesizedHome(devices: [haSensor])
        haHome.deviceIdentities = [haSensor.id: DeviceSourceIdentity(
            dids: ["dual1"], model: Self.dualModel, name: haSensor.name,
            roomName: haSensor.roomName, kindTag: .sensor
        )]

        let merged = DeviceDeduplicator.merge(homeAssistant: haHome, xiaomiCloud: miHome)
        #expect(merged.home.devices.count == 2)
        #expect(merged.home.devices.allSatisfy { if case .cover = $0.kind { true } else { false } })
    }

    @Test("An HA card sharing only the dual-track motor's MAC is not a third card")
    func haMacOnlyDuplicateIsNotAThirdCard() throws {
        let (drape, lace, _) = try dualTrackHome()
        var miHome = SynthesizedHome(devices: [drape, lace])
        miHome.deviceIdentities = [
            drape.id: DeviceSourceIdentity(
                dids: ["dual1"], macAddresses: ["aabbccddeeff"], model: Self.dualModel,
                name: drape.name, roomName: drape.roomName, kindTag: .cover
            ),
            lace.id: DeviceSourceIdentity(
                dids: ["dual1"], macAddresses: ["aabbccddeeff"], model: Self.dualModel,
                name: lace.name, roomName: lace.roomName, kindTag: .cover
            ),
        ]

        let haSensor = SmartDevice(
            id: "ha-mac", name: "窗帘电量", roomId: "living", roomName: "客厅",
            model: "sensor.battery", isOnline: true,
            kind: .sensor(SensorSpec(battery: 80))
        )
        var haHome = SynthesizedHome(devices: [haSensor])
        haHome.deviceIdentities = [haSensor.id: DeviceSourceIdentity(
            macAddresses: ["AA-BB-CC-DD-EE-FF"], model: haSensor.model,
            name: haSensor.name, roomName: haSensor.roomName, kindTag: .sensor
        )]

        let merged = DeviceDeduplicator.merge(homeAssistant: haHome, xiaomiCloud: miHome)
        #expect(Set(merged.home.devices.map(\.id)) == ["dual1.s2", "dual1.s3"])
    }

    // MARK: Dual source

    @Test("A curtain reached through both Mijia and Home Assistant keeps one card")
    func dualSourceCurtainDeduplicates() throws {
        let (device, _, record) = try singleTrackHome()
        var miHome = SynthesizedHome(devices: [device])
        miHome.deviceIdentities = [device.id: DeviceSourceIdentity(
            dids: [record.did],
            model: record.model,
            name: device.name,
            roomName: device.roomName,
            kindTag: .cover
        )]

        // Home Assistant's Xiaomi integration exposes the curtain's battery as a sensor card.
        let haSensor = SmartDevice(
            id: "ha-curtain", name: "客厅窗帘", roomId: "living", roomName: "客厅",
            model: Self.singleModel, isOnline: true,
            kind: .sensor(SensorSpec(battery: 80))
        )
        var haHome = SynthesizedHome(devices: [haSensor])
        haHome.deviceIdentities = [haSensor.id: DeviceSourceIdentity(
            dids: [record.did],
            model: Self.singleModel,
            name: haSensor.name,
            roomName: haSensor.roomName,
            kindTag: .sensor
        )]

        let merged = DeviceDeduplicator.merge(homeAssistant: haHome, xiaomiCloud: miHome)
        #expect(merged.home.devices.count == 1)
        let card = try #require(merged.home.devices.first)
        guard case .cover = card.kind else {
            Issue.record("the merged card kept the Home Assistant sensor instead of the curtain")
            return
        }
        #expect(merged.routes[card.id]?.backend == .xiaomiCloud)
        #expect(merged.routes[card.id]?.sourceDeviceID == record.did)
    }
}

private extension SmartDevice {
    var coverPosition: Int? {
        guard case .cover(let spec) = kind else { return nil }
        return spec.positionPercent
    }
}
