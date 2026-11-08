import Foundation
import Testing
@testable import MijiaCore

/// #5: the generic 更多设置 rows a detail drawer mounts. Facets the primary card already reads and
/// the card's own actions stay out, so the section only carries auxiliary controls.
struct FacetControlsTests {
    private static func repoFile(_ relative: String) throws -> URL {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent("Tests/Fixtures").path) {
                return directory.appendingPathComponent(relative)
            }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private static func coverageSpec(_ model: String) throws -> MiotDeviceSpec {
        let data = try Data(contentsOf: repoFile("Tests/Fixtures/miot_coverage_specs.json"))
        let specs = try JSONDecoder().decode([String: MiotDeviceSpec].self, from: data)
        return try #require(specs[model], Comment(rawValue: model))
    }

    private static func home(
        model: String,
        did: String,
        values: [MiotPropKey: JSONValue] = [:]
    ) throws -> SynthesizedHome {
        let spec = try coverageSpec(model)
        return MiotSynthesizer.synthesize(
            devices: [MiDeviceRecord(did: did, name: "device", model: model, isOnline: true)],
            rooms: [],
            specs: [model: spec],
            values: values
        )
    }

    // MARK: - Synthesis

    @Test("Air conditioner exposes sibling-service facets and leaves the card's own controls out")
    func airConditionerFacets() throws {
        let home = try Self.home(
            model: "090615.airc.a07s",
            did: "ac1",
            values: [
                MiotPropKey(did: "ac1", siid: 2, piid: 2): .number(0),
                MiotPropKey(did: "ac1", siid: 3, piid: 2): .number(1),
            ]
        )
        let facets = try #require(home.deviceFacets["ac1"])
        let ids = Set(facets.map(\.id))

        #expect(!ids.contains("2.2"), "mode is the card's primary control")
        #expect(!ids.contains("2.3"), "target temperature is the card's primary control")
        #expect(!ids.contains("9.2"), "fan level is the card's primary control")
        #expect(!ids.contains("6.1"), "the card already shows the room temperature")
        #expect(!ids.contains("2.a1"), "an action of the primary service belongs to the card")
        #expect(ids.contains("6.2"), "humidity is an auxiliary reading the card does not show")
        #expect(ids.contains("3.1") && ids.contains("3.2") && ids.contains("3.3"), "fresh air")
        #expect(ids.contains("4.2"), "floor heat target")
        #expect(ids.contains("3.a1"), "a no-argument action in a sibling service stays available")

        let freshAirMode = try #require(facets.first { $0.id == "3.2" })
        #expect(freshAirMode.kind == .select)
        #expect(freshAirMode.value == .number(1))
        #expect(freshAirMode.selectedOption?.label == "HuanQi")
        #expect(freshAirMode.writesByDefault, "mode is on the low-risk whitelist")
        #expect(freshAirMode.isHighValue)
        #expect(freshAirMode.target == .property(entityId: "miot:ac1:3", piid: 2))

        let floorHeat = try #require(facets.first { $0.id == "4.2" })
        #expect(floorHeat.kind == .slider)
        #expect(floorHeat.range?.min == 0 && floorHeat.range?.max == 55)
        #expect(!floorHeat.writesByDefault, "target temperature is outside the low-risk whitelist")

        let toggle = try #require(facets.first { $0.id == "3.a1" })
        #expect(toggle.kind == .button)
        #expect(toggle.target == .action(entityId: "miot-action:ac1:3:1"))
    }

    @Test("Purifier, camera and light all produce a non-empty 更多设置 list")
    func supportedFamiliesHaveFacets() throws {
        let purifier = try Self.home(model: "1245.airpurifier.dl01", did: "purifier1")
        let purifierFacets = try #require(purifier.deviceFacets["purifier1"])
        #expect(Set(purifierFacets.map(\.id)) == ["3.1", "4.1"])
        #expect(purifierFacets.allSatisfy { !$0.isHighValue })

        let camera = try Self.home(model: "careco.camera.650", did: "cam1")
        let cameraFacets = try #require(camera.deviceFacets["cam1"])
        let cameraIDs = Set(cameraFacets.map(\.id))
        #expect(cameraIDs.contains("5.3"), "detection sensitivity")
        #expect(cameraIDs.contains("5.2"), "alarm interval")
        #expect(cameraIDs.contains("4.1"), "memory card status")
        #expect(cameraIDs.contains("6.1"), "speaker volume")
        #expect(!cameraIDs.contains("2.1"), "power is the card's own toggle")
        #expect(!cameraIDs.contains("5.1"), "motion detection is the card's own toggle")
        #expect(!cameraIDs.contains("20.a2"), "an action that wants an argument has no generic editor")

        let light = try Self.home(model: "090615.light.ailig1", did: "light1")
        let lightFacets = try #require(light.deviceFacets["light1"])
        let lightIDs = Set(lightFacets.map(\.id))
        #expect(lightIDs.contains("3.1"), "setmode flex switch")
        #expect(lightIDs.contains("3.3"), "delay time")
        #expect(!lightIDs.contains("2.2"), "brightness is the card's slider")
        #expect(!lightIDs.contains("2.3"), "color temperature is the card's slider")
    }

    @Test("An action with inputs is not offered as a generic button")
    func argumentedActionsStayOnTheirCard() throws {
        let home = try Self.home(model: "careco.camera.650", did: "cam1")
        let facets = try #require(home.deviceFacets["cam1"])
        #expect(MiotFacetEngine.facets(for: try Self.coverageSpec("careco.camera.650"))
            .contains { $0.arguments?.isEmpty == false }, "the engine does synthesize this action")
        #expect(!facets.contains { $0.id == "20.a2" })
        #expect(facets.contains { $0.name == "pop-up" }, "a no-argument action remains")
    }

    // MARK: - Command mapping

    private func map(_ command: DeviceCommand, model: String) throws -> MiotWrite {
        try MiotCommandMapper.map(command, spec: Self.coverageSpec(model))
    }

    @Test("Generic property writes coerce to the spec's wire format")
    func propertyWriteMapping() throws {
        #expect(
            try map(
                .setMiotProperty(entityId: "miot:ac1:3", piid: 2, value: .number(20)),
                model: "090615.airc.a07s"
            ) == .setProps([.init(did: "ac1", siid: 3, piid: 2, value: .number(20))])
        )
        #expect(
            try map(
                .setMiotProperty(entityId: "miot:ac1:3", piid: 1, value: .number(1)),
                model: "090615.airc.a07s"
            ) == .setProps([.init(did: "ac1", siid: 3, piid: 1, value: .bool(true))])
        )
        #expect(throws: MiotCommandError.self) {
            try map(
                .setMiotProperty(entityId: "miot:ac1:2", piid: 4, value: .number(1)),
                model: "090615.airc.a07s"
            )
        }
        #expect(throws: MiotCommandError.self) {
            try map(
                .setMiotProperty(entityId: "miot:ac1:2", piid: 99, value: .number(1)),
                model: "090615.airc.a07s"
            )
        }
    }

    @Test("Generic actions fire only when the spec action takes no inputs")
    func actionMapping() throws {
        #expect(
            try map(.callMiotAction(entityId: "miot-action:ac1:3:1", arguments: []), model: "090615.airc.a07s")
                == .action(MiotActionCall(did: "ac1", siid: 3, aiid: 1))
        )
        #expect(throws: MiotCommandError.self) {
            try map(.callMiotAction(entityId: "miot-action:cam1:4:3", arguments: []), model: "careco.camera.650")
        }
    }

    @Test("The mains breaker refuses generic facet writes too")
    func mainsBreakerRefusesGenericWrites() throws {
        struct Corpus: Decodable { let specs: [String: MiotDeviceSpec] }
        let data = try Data(contentsOf: Self.repoFile("Tests/Fixtures/miot_golden.json"))
        let spec = try #require(try JSONDecoder().decode(Corpus.self, from: data).specs["sanmei.valve.s1"])
        #expect(throws: MiotCommandError.self) {
            try MiotCommandMapper.map(
                .setMiotProperty(entityId: "miot:breaker:2", piid: 3, value: .bool(false)),
                spec: spec
            )
        }
        let home = MiotSynthesizer.synthesize(
            devices: [MiDeviceRecord(did: "b", name: "总闸", model: "sanmei.valve.s1", isOnline: true)],
            rooms: [],
            specs: ["sanmei.valve.s1": spec],
            values: [:]
        )
        #expect(home.deviceFacets.isEmpty, "the breaker offers no 更多设置 writes")
    }

    @Test("Facet properties are polled without duplicating the card's own reads")
    func facetPropsPollAuxiliaryValues() throws {
        let spec = try Self.coverageSpec("090615.airc.a07s")
        let record = MiDeviceRecord(did: "ac1", name: "空调", model: "090615.airc.a07s", isOnline: true)
        let props = Set(MiotSynthesizer.facetProps(for: record, spec: spec))
        #expect(props.contains(MiotPropKey(did: "ac1", siid: 3, piid: 2)), "fresh-air mode")
        #expect(props.contains(MiotPropKey(did: "ac1", siid: 4, piid: 2)), "floor-heat target")
        #expect(!props.contains(MiotPropKey(did: "ac1", siid: 2, piid: 2)), "card mode is not polled twice")
        #expect(props.contains(MiotPropKey(did: "ac1", siid: 3, piid: 1)), "fresh-air power is read")
    }

    // MARK: - Store writes

    @MainActor
    private func makeStore(
        model: String,
        did: String,
        values: [MiotPropKey: JSONValue] = [:]
    ) async throws -> (HomeStore, MockHomeProvider) {
        let home = try Self.home(model: model, did: did, values: values)
        let mock = MockHomeProvider(snapshot: home)
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.facets.\(UUID().uuidString)")
        store.controlDebounceInterval = .zero
        try await store.waitForTestSnapshot()
        return (store, mock)
    }

    @MainActor
    private func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<200 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    @MainActor
    private func facet(_ store: HomeStore, _ deviceId: String, _ controlId: String) -> FacetControl? {
        store.deviceFacets[deviceId]?.first { $0.id == controlId }
    }

    @Test("A facet write is optimistic and the undo banner restores the old value")
    @MainActor
    func facetWriteIsOptimisticAndUndoable() async throws {
        let (store, mock) = try await makeStore(
            model: "090615.airc.a07s",
            did: "ac1",
            values: [MiotPropKey(did: "ac1", siid: 3, piid: 2): .number(0)]
        )
        store.setFacetValue("ac1", controlId: "3.2", value: .number(1))

        #expect(facet(store, "ac1", "3.2")?.value == .number(1), "the drawer updates before the network")
        store.pauseUndoBannerCountdown()
        #expect(store.undoBanner != nil)

        await waitUntil {
            await mock.executedCommands.contains(
                .setMiotProperty(entityId: "miot:ac1:3", piid: 2, value: .number(1))
            )
        }
        #expect(await mock.executedCommands == [
            .setMiotProperty(entityId: "miot:ac1:3", piid: 2, value: .number(1))
        ])

        let undo = store.undoTurnAllOff()
        await undo?.value
        await waitUntil {
            await mock.executedCommands.contains(
                .setMiotProperty(entityId: "miot:ac1:3", piid: 2, value: .number(0))
            )
        }
        #expect(facet(store, "ac1", "3.2")?.value == .number(0))
    }

    @Test("A facet outside the low-risk whitelist stays closed until the setting is on")
    @MainActor
    func highRiskFacetNeedsOptIn() async throws {
        let (store, mock) = try await makeStore(model: "090615.airc.a07s", did: "ac1")
        store.setFacetValue("ac1", controlId: "4.2", value: .number(24))
        #expect(facet(store, "ac1", "4.2")?.value == nil)
        #expect(await mock.executedCommands.isEmpty)

        store.allowAdvancedFacetWrites = true
        store.manualControlConfirmationHandler = { _ in true }
        store.setFacetValue("ac1", controlId: "4.2", value: .number(24))
        #expect(facet(store, "ac1", "4.2")?.value == .number(24))
        await waitUntil {
            await mock.executedCommands.contains(
                .setMiotProperty(entityId: "miot:ac1:4", piid: 2, value: .number(24))
            )
        }
    }

    @Test("A failed facet write puts the old value back")
    @MainActor
    func failedFacetWriteRollsBack() async throws {
        let (store, mock) = try await makeStore(
            model: "090615.airc.a07s",
            did: "ac1",
            values: [MiotPropKey(did: "ac1", siid: 3, piid: 2): .number(2)]
        )
        await mock.setShouldFail(true)
        store.setFacetValue("ac1", controlId: "3.2", value: .number(0))
        #expect(facet(store, "ac1", "3.2")?.value == .number(0))

        await waitUntil { facet(store, "ac1", "3.2")?.value == .number(2) }
        #expect(facet(store, "ac1", "3.2")?.value == .number(2))
    }

    @Test("A snapshot during the write lock does not undo the optimistic value")
    @MainActor
    func snapshotDuringLockKeepsOptimisticValue() async throws {
        let (store, mock) = try await makeStore(
            model: "090615.airc.a07s",
            did: "ac1",
            values: [MiotPropKey(did: "ac1", siid: 3, piid: 2): .number(0)]
        )
        await mock.holdNextExecute()
        store.setFacetValue("ac1", controlId: "3.2", value: .number(1))
        await waitUntil { await mock.executeStarted == 1 }

        let generation = store.snapshotGeneration
        await mock.updateSnapshot(try Self.home(
            model: "090615.airc.a07s",
            did: "ac1",
            values: [MiotPropKey(did: "ac1", siid: 3, piid: 2): .number(0)]
        ))
        await waitUntil { store.snapshotGeneration > generation }
        #expect(store.snapshotGeneration > generation)
        #expect(facet(store, "ac1", "3.2")?.value == .number(1))
        await mock.releaseExecute()
    }
}
