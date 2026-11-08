import Foundation
import Testing
@testable import MijiaCore

struct FacetDrawerAccessTests {
    private func fixture(_ relative: String) throws -> Data {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let file = directory.appendingPathComponent("Tests/Fixtures/\(relative)")
            if FileManager.default.fileExists(atPath: file.path) { return try Data(contentsOf: file) }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    @Test("Cover auxiliary settings omit the motor already controlled by the card", arguments: [
        "lumi.curtain.hagl05", "090615.curtain.1mcu01", "czmydz.curtain.myf5", "hotata.airer.q31zm",
    ])
    func coverMotorIsNotRepeated(model: String) throws {
        let spec = try JSONDecoder().decode(MiotDeviceSpec.self, from: fixture("miot/\(model).json"))
        let record = MiDeviceRecord(did: "cover1", name: "cover", model: model, isOnline: true)
        let home = MiotSynthesizer.synthesize(devices: [record], rooms: [], specs: [model: spec], values: [:])
        #expect(!home.devices.isEmpty)
        #expect(home.deviceFacets.values.flatMap { $0 }.allSatisfy { $0.name != "motor-control" })
        let motors = spec.services.flatMap { service in
            service.properties.filter { $0.shortName == "motor-control" }.map {
                MiotPropKey(did: "cover1", siid: service.iid, piid: $0.iid)
            }
        }
        #expect(!motors.isEmpty)
        let polled = Set(MiotSynthesizer.requiredProps(for: record, spec: spec))
        #expect(motors.allSatisfy { !polled.contains($0) }, "write-only motor controls are consumed, not polled")
        if model == "090615.curtain.1mcu01" {
            #expect(home.deviceFacets.values.flatMap { $0 }.contains { $0.name == "mode" }, "calibration stays available as an auxiliary setting")
        }
    }

    @Test("A mains breaker does not poll hidden auxiliary settings")
    func breakerDoesNotPollFacets() throws {
        struct Corpus: Decodable { let specs: [String: MiotDeviceSpec] }
        let corpus = try JSONDecoder().decode(Corpus.self, from: fixture("miot_golden.json"))
        let spec = try #require(corpus.specs["sanmei.valve.s1"])
        let record = MiDeviceRecord(did: "breaker", name: "breaker", model: "sanmei.valve.s1", isOnline: true)
        #expect(MiotSynthesizer.facetProps(for: record, spec: spec).isEmpty)
    }

    @Test("A card without primary details opens its auxiliary settings")
    @MainActor
    func facetOnlyCardCanExpand() async throws {
        let device = SmartDevice(
            id: "sensor1", name: "sensor", roomId: "room", roomName: "room", model: "sensor",
            isOnline: true, kind: .sensor(SensorSpec())
        )
        var home = SynthesizedHome(devices: [device])
        home.deviceFacets[device.id] = [FacetControl(
            id: "2.1", kind: .reading, name: "battery-level", title: "Battery", serviceName: "battery",
            value: .number(90), isWritable: false, writesByDefault: false, isHighValue: false
        )]
        let store = HomeStore(provider: MockHomeProvider(snapshot: home), defaultsSuiteName: "test.facet.drawer.\(UUID().uuidString)")
        try await store.waitForTestSnapshot()
        #expect(!device.hasDetailDrawer)
        #expect(store.hasDetailDrawer(for: device))
        store.togglePrimary(device)
        #expect(store.expandedDeviceId == device.id)
        store.expandedDeviceId = nil
        store.devices[0].kind = .plug(PlugSpec(switchEntityId: "switch.test", isOn: true))
        #expect(!store.devices[0].hasDetailDrawer)
        store.focusedDeviceId = device.id
        store.adjustFocusedDevice(delta: 1)
        #expect(store.expandedDeviceId == device.id)
    }
}
