import Foundation
import Testing
@testable import MijiaCore

/// `sanmei.valve.s1` (#265): a mains breaker / recloser shown as a read-only power meter.
struct MainsBreakerTests {
    private static func goldenSpec(_ model: String) throws -> MiotDeviceSpec {
        struct Corpus: Decodable { let specs: [String: MiotDeviceSpec] }
        let data = try Data(contentsOf: repoFile("Tests/Fixtures/miot_golden.json"))
        return try #require(try JSONDecoder().decode(Corpus.self, from: data).specs[model])
    }

    private static func record(_ did: String, model: String) -> MiDeviceRecord {
        MiDeviceRecord(did: did, name: "总闸", model: model, isOnline: true, splitParentDid: nil, splitSiid: nil)
    }

    @Test("The breaker is a read-only plug; a water valve becomes a read-only generic card")
    func synthesizesReadOnlyMeter() throws {
        let breaker = try Self.goldenSpec("sanmei.valve.s1")
        let water = try Self.goldenSpec("golden.valve.water")
        #expect(MiotSynthesizer.supportedServiceName(for: breaker) == "electronic-valve")
        #expect(MiotSynthesizer.supportedServiceName(for: water) == nil)

        let home = MiotSynthesizer.synthesize(
            devices: [Self.record("b", model: "sanmei.valve.s1"), Self.record("w", model: "golden.valve.water")],
            rooms: [],
            specs: ["sanmei.valve.s1": breaker, "golden.valve.water": water],
            values: [
                MiotPropKey(did: "b", siid: 2, piid: 3): .bool(true),
                MiotPropKey(did: "b", siid: 3, piid: 6): .number(2400),
                MiotPropKey(did: "w", siid: 2, piid: 1): .bool(true),
            ]
        )
        #expect(Set(home.devices.map(\.id)) == ["b", "w"])
        let device = try #require(home.devices.first { $0.id == "b" })
        guard case .plug(let spec) = device.kind else {
            Issue.record("breaker should be a plug-shaped meter")
            return
        }
        #expect(spec.isOn)
        #expect(spec.powerWatts == 2400)
        #expect(spec.energyKwh == nil, "power-consumption has no unit, so it is never guessed (#129)")
        #expect(!spec.powerWritable)
        #expect(!spec.countsInTotalPower)
        #expect(spec.childLockEntityId == nil)
        #expect(!device.isPrimaryToggleable)
        #expect(!HomeStore.joinsAllOffByDefault(device), "plugs stay out of 全关 by default")
        #expect(!device.kind.canTogglePower, "and even an override cannot add a read-only switch")

        let valve = try #require(home.devices.first { $0.id == "w" })
        guard case .generic(let card) = valve.kind else {
            Issue.record("water valve should take the generic fallback")
            return
        }
        #expect(card.powerControl == nil)
        #expect(!valve.kind.canTogglePower, "opening a water valve is not a low-risk write")
        #expect(home.deviceFacets["w"]?.contains { $0.name == "on" && !$0.writesByDefault } == true)
    }

    @Test("Every write to the breaker is refused, even where the spec says writable")
    func commandMapperRefusesWrites() throws {
        let spec = try Self.goldenSpec("sanmei.valve.s1")
        let commands: [DeviceCommand] = [
            .setSwitch(entityId: "miot:b:2", isOn: false),
            .setSwitch(entityId: "miot:b:2", isOn: true),
            .setSwitch(entityId: "miot:b:5", isOn: true),
        ]
        for command in commands {
            #expect(throws: MiotCommandError.self) { try MiotCommandMapper.map(command, spec: spec) }
        }
    }

    @Test("The breaker stays out of the home total and never offers a turn-off reminder")
    @MainActor
    func excludedFromTotalAndReminders() async throws {
        let breaker = SmartDevice(
            id: "breaker", name: "总闸", roomId: "hall", roomName: "玄关", model: "sanmei.valve.s1", isOnline: true,
            kind: .plug(PlugSpec(
                switchEntityId: "miot:breaker:2", isOn: true, powerWatts: 2_400,
                powerWritable: false, countsInTotalPower: false
            ))
        )
        let kettle = SmartDevice(
            id: "kettle", name: "水壶插座", roomId: "kitchen", roomName: "厨房", model: "cuco.plug.v3", isOnline: true,
            kind: .plug(PlugSpec(switchEntityId: "miot:kettle:2", isOn: true, powerWatts: 1_500))
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [breaker, kettle]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.breaker.\(UUID().uuidString)")
        for _ in 0..<100 where store.devices.count < 2 {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(store.devices.count == 2)
        #expect(store.totalPowerWatts == 1_500)

        let start = Date(timeIntervalSince1970: 1_800_000_000)
        _ = store.evaluateSafetyAlerts(now: start)
        let later = store.evaluateSafetyAlerts(now: start.addingTimeInterval(46 * 60))
        #expect(!later.contains { $0.deviceId == "breaker" })
        #expect(later.contains { $0.id == "longrun:kettle" })

        #expect(store.togglePrimary(breaker) == nil)
        #expect(await mock.executedCommands.isEmpty)
    }
}

private func repoFile(_ relative: String) throws -> URL {
    var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    for _ in 0..<8 {
        let candidate = directory.appendingPathComponent(relative)
        if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
        directory.deleteLastPathComponent()
    }
    throw CocoaError(.fileNoSuchFile)
}
