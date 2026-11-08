import Foundation
import Testing
@testable import MijiaCore

/// Smart fish tanks and pet feeders on the generic card (#7): water temperature on the tile, the
/// pump and light under 更多设置, a portioned feeding action behind a second confirmation, and
/// pump conditions that say what is wrong instead of hiding behind a fold.
struct FishTankPetFeederTests {
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
        return try #require(specs[model], "\(model)")
    }

    private static func home(
        model: String,
        did: String,
        values: [MiotPropKey: JSONValue] = [:]
    ) throws -> SynthesizedHome {
        let spec = try coverageSpec(model)
        return MiotSynthesizer.synthesize(
            devices: [MiDeviceRecord(did: did, name: did, model: model, isOnline: true)],
            rooms: [],
            specs: [model: spec],
            values: values
        )
    }

    private static func generic(_ home: SynthesizedHome) throws -> (SmartDevice, GenericSpec) {
        let device = try #require(home.devices.first)
        guard case .generic(let spec) = device.kind else {
            Issue.record("expected a generic card, got \(device.kind)")
            throw CocoaError(.featureUnsupported)
        }
        return (device, spec)
    }

    private static func key(_ did: String, _ siid: Int, _ piid: Int) -> MiotPropKey {
        MiotPropKey(did: did, siid: siid, piid: piid)
    }

    /// `siid.piid` of the facet carrying a short name, so a spec whose ids differ between models
    /// still reads the same value key in a test.
    private static func address(_ spec: MiotDeviceSpec, _ name: String, model: String) throws -> (siid: Int, piid: Int) {
        let controls = MiotFacetEngine.controls(
            for: spec,
            did: "probe",
            values: [:],
            primaryServiceId: nil,
            model: model
        )
        let control = try #require(controls.first { $0.name == name }, "\(model) \(name)")
        let parts = control.id.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 2 else {
            Issue.record("\(model): unexpected facet id \(control.id)")
            throw CocoaError(.featureUnsupported)
        }
        return (parts[0], parts[1])
    }

    // MARK: - Card and drawer

    @Test("Fish tanks show the main switch and water temperature, and keep highlights to eight")
    func fishTankCards() throws {
        for (model, sample) in [
            ("xiaomi.fishbowl.m300", 24.5),
            ("xiaomi.fishbowl.m200", 24.0),
            ("hfjh.fishbowl.m100", 24.0),
            ("209754.fishbowl.tank01", 24.0),
        ] {
            let did = "tank"
            let spec = try Self.coverageSpec(model)
            let temperature = try Self.address(spec, "temperature", model: model)
            let home = try Self.home(model: model, did: did, values: [
                Self.key(did, 2, 1): .bool(true),
                Self.key(did, temperature.siid, temperature.piid): .number(sample),
            ])
            let (device, card) = try Self.generic(home)

            #expect(card.deviceType == "fish-tank", "\(model)")
            #expect(card.powerControl != nil, "\(model)")
            #expect(card.isOn, "\(model)")
            #expect(card.readouts.count == 1, "\(model)")
            #expect(card.readouts.first?.value.contains(String(sample)) == true, "\(model)")

            let facets = try #require(home.deviceFacets[device.id], "\(model)")
            let highlighted = facets.filter(\.isHighValue)
            #expect(!highlighted.isEmpty, "\(model)")
            #expect(highlighted.count <= DeviceProfiles.current.facets.maxVisible, "\(model)")
            #expect(highlighted.contains { $0.name == "water-pump" }, "\(model)")
            #expect(highlighted.contains { $0.name == "pet-food-out" }, "\(model)")
            #expect(facets.contains { $0.name.contains("warn") || $0.name == "upper-temperature" }, "\(model)")
        }
    }

    @Test("Vendor-private properties never reach the drawer", arguments: [
        "xiaomi.fishbowl.m300", "xiaomi.fishbowl.m200", "hfjh.fishbowl.m100", "209754.fishbowl.tank01",
    ])
    func internalPropertiesStayOut(_ model: String) throws {
        let home = try Self.home(model: model, did: "tank")
        let (device, _) = try Self.generic(home)
        let facets = try #require(home.deviceFacets[device.id])
        let hiddenNames = [
            "bind-timestamp", "trigger-status", "no-reminder-status", "light-mode-list",
            "light-options-list", "code", "scenario-a", "scenario-b", "enable-time-period",
        ]
        for facet in facets {
            #expect(!hiddenNames.contains(facet.name), "\(model) \(facet.name)")
            if facet.serviceName.hasSuffix("-custom") {
                // A noisy vendor service only survives through its named exceptions.
                #expect(
                    MiotFacetEngine.matches(facet.name, anyOf: DeviceProfiles.current.noise.keepInNoisyServices),
                    "\(model) \(facet.serviceName).\(facet.name)"
                )
            }
        }
    }

    @Test("A pump on low water, blocked or faulted says so; closed and opened stay quiet", arguments: [
        (0, "未插入" as String?), (1, nil), (2, nil), (3, "低水位"), (4, "堵塞"), (5, "故障"), (6, "低水位"),
    ])
    func pumpAlerts(status: Int, expected: String?) throws {
        // m300 publishes all seven pump states, including the second low-water value.
        let model = "xiaomi.fishbowl.m300"
        let spec = try Self.coverageSpec(model)
        let pump = try Self.address(spec, "water-pump-status", model: model)
        let home = try Self.home(model: model, did: "tank", values: [
            Self.key("tank", pump.siid, pump.piid): .number(Double(status)),
        ])
        let (device, card) = try Self.generic(home)
        guard let expected else {
            #expect(card.alerts.isEmpty, "status \(status)")
            return
        }
        #expect(card.alerts.contains { $0.contains(expected) }, "status \(status): \(card.alerts)")
        #expect(device.statusSubtitle.contains(expected), "status \(status): \(device.statusSubtitle)")
    }

    @Test("A vendor alarm reading also becomes an alert")
    func alarmReadingAlerts() throws {
        let home = try Self.home(model: "209754.fishbowl.tank01", did: "tank", values: [
            Self.key("tank", 9, 5): .number(1),
        ])
        let (_, card) = try Self.generic(home)
        #expect(card.alerts.contains { $0.contains("异常") }, "\(card.alerts)")
    }

    @Test("Word boundaries keep Flow and No Faults out of the alerts")
    func alertWordBoundaries() {
        #expect(MiotFacetEngine.isAlertLabel("Low"))
        #expect(MiotFacetEngine.isAlertLabel("Low1"))
        #expect(MiotFacetEngine.isAlertLabel("Blocked"))
        #expect(MiotFacetEngine.isAlertLabel("Not Inserted"))
        #expect(!MiotFacetEngine.isAlertLabel("Flow"))
        #expect(!MiotFacetEngine.isAlertLabel("No Faults"))
        #expect(!MiotFacetEngine.isAlertLabel("Normal"))
    }

    // MARK: - Feeding

    @Test("Feeding offers the spec's portions and carries today's total into the confirmation")
    func feedingConfirmationCarriesTodaysTotal() throws {
        let home = try Self.home(model: "xiaomi.fishbowl.m200", did: "tank", values: [
            Self.key("tank", 5, 2): .number(12),
        ])
        let (device, _) = try Self.generic(home)
        let facets = try #require(home.deviceFacets[device.id])
        let feed = try #require(facets.first { $0.name == "pet-food-out" })

        #expect(feed.kind == .button)
        #expect(feed.portionArgumentIndex == 0)
        #expect(feed.arguments.first?.range?.min == 1)
        #expect(feed.arguments.first?.range?.max == 3)
        #expect(feed.fedToday == "12")
        #expect(feed.isConfirmedWrite, "feeding is offered without the advanced-writes opt-in")
        #expect(!feed.writesByDefault, "it stays outside the low-risk whitelist")

        try L10n.$overrideLanguage.withValue(.zhHans) {
            let prompt = ManualControlPolicy.confirmation(facet: feed, value: nil, portion: 2, for: device)
            let message = try #require(prompt?.message)
            #expect(message.contains("2 份"), "\(message)")
            #expect(message.contains("12"), "\(message)")
            #expect(prompt?.title.contains("投喂") == true)
        }
    }

    @Test("The portion is clamped to the spec's range and enum inputs pick the app source")
    func feedingArguments() throws {
        let m200 = try Self.coverageSpec("xiaomi.fishbowl.m200")
        let m200Feed = try #require(MiotFacetEngine.controls(
            for: m200, did: "tank", values: [:], primaryServiceId: nil, model: "xiaomi.fishbowl.m200"
        ).first { $0.name == "pet-food-out" })
        #expect(HomeStore.facetActionArguments(m200Feed, portion: 2) == [.number(2)])
        #expect(HomeStore.facetActionArguments(m200Feed, portion: 99) == [.number(3)])
        #expect(HomeStore.facetActionArguments(m200Feed, portion: 0) == [.number(1)])

        let m300 = try Self.coverageSpec("xiaomi.fishbowl.m300")
        let m300Controls = MiotFacetEngine.controls(
            for: m300, did: "tank", values: [:], primaryServiceId: nil, model: "xiaomi.fishbowl.m300"
        )
        let mainFeed = try #require(m300Controls.first { $0.id == "2.a1" })
        #expect(mainFeed.arguments.count == 1)
        #expect(HomeStore.facetActionArguments(mainFeed, portion: 4) == [.number(4)])

        // The feeder service publishes the same action with a second input: the vendor's feeding
        // type, where an app dispense is `Remote` rather than a key press. Only the first match
        // becomes the drawer's feeding editor, so this one is read straight from the facet list.
        let feederAction = try #require(
            MiotFacetEngine.facets(for: m300, model: "xiaomi.fishbowl.m300").first { $0.id == "5.a1" }
        )
        let typeArgument = try #require(feederAction.arguments?.last)
        #expect(feederAction.arguments?.count == 2)
        #expect(MiotFacetEngine.defaultActionArgument(typeArgument) == .number(1))
    }

    @Test("An unusable spec range degrades to no portions instead of trapping or expanding")
    func extremePortionRange() {
        // A cloud typo such as `1e20` used to trap in `Int(_:)` or allocate an endless array (§11).
        let huge = MiotFacetRange(min: 0, max: 1e20, step: 1)
        #expect(huge.validPortionOptions().isEmpty)
        #expect(huge.clampedPortion(5) == nil)
        #expect(MiotFacetRange(min: 1, max: .infinity, step: 1).clampedPortion(2) == nil)
        #expect(MiotFacetRange(min: 1, max: 3, step: 0).validPortionOptions().isEmpty)
        #expect(MiotFacetRange(min: 1, max: 3, step: .nan).clampedPortion(2) == nil)
        #expect(MiotFacetRange(min: 2, max: 1, step: 1).validPortionOptions().isEmpty)

        // A wide but convertible range keeps the picker bounded; the shipped 30-portion feeder
        // (`binuo.feeder.y1`) still sees every value.
        let wide = MiotFacetRange(min: 1, max: 1_000, step: 1)
        #expect(wide.validPortionOptions() == Array(1...64))
        #expect(MiotFacetRange(min: 1, max: 30, step: 1).validPortionOptions() == Array(1...30))
        #expect(wide.validPortionOptions(maxCount: 3) == [1, 2, 3])
        #expect(wide.clampedPortion(nil) == 1)
        #expect(wide.clampedPortion(0) == 1)
        #expect(wide.clampedPortion(50) == 50)
        #expect(wide.clampedPortion(5_000) == 1_000)

        // The drawer, the confirmation and the command builder share one clamp.
        func facet(_ range: MiotFacetRange) -> FacetControl {
            FacetControl(
                id: "5.a1",
                kind: .button,
                name: "pet-food-out",
                title: nil,
                serviceName: "feeder",
                isWritable: true,
                writesByDefault: false,
                isHighValue: true,
                arguments: [
                    MiotFacetArgument(propertyId: 1, name: "portion", format: "uint8", range: range, options: nil),
                ],
                portionArgumentIndex: 0
            )
        }
        let unbounded = facet(huge)
        #expect(unbounded.portionOptions.isEmpty)
        #expect(unbounded.clampedPortion(4) == 4)
        #expect(HomeStore.facetActionArguments(unbounded, portion: 4) == [.number(4)])

        let bounded = facet(wide)
        #expect(bounded.portionOptions.first == 1)
        #expect(HomeStore.facetActionArguments(bounded, portion: 5_000) == [.number(1_000)])
    }

    @Test("The MIoT mapper clamps the portion and rejects a wrong argument count")
    func feedingMapping() throws {
        let spec = try Self.coverageSpec("xiaomi.fishbowl.m200")
        #expect(
            try MiotCommandMapper.map(
                .callMiotAction(entityId: "miot-action:tank:2:1", arguments: [.number(9)]),
                spec: spec
            ) == .action(MiotActionCall(did: "tank", siid: 2, aiid: 1, inputs: [.number(3)]))
        )
        #expect(throws: MiotCommandError.self) {
            try MiotCommandMapper.map(
                .callMiotAction(entityId: "miot-action:tank:2:1", arguments: []),
                spec: spec
            )
        }

        // The m300 feeder service takes the portion plus the vendor's feeding type.
        let m300 = try Self.coverageSpec("xiaomi.fishbowl.m300")
        #expect(
            try MiotCommandMapper.map(
                .callMiotAction(entityId: "miot-action:tank:5:1", arguments: [.number(2), .number(1)]),
                spec: m300
            ) == .action(MiotActionCall(did: "tank", siid: 5, aiid: 1, inputs: [.number(2), .number(1)]))
        )
        #expect(throws: MiotCommandError.self) {
            try MiotCommandMapper.map(
                .callMiotAction(entityId: "miot-action:tank:5:1", arguments: [.number(2), .number(9)]),
                spec: m300
            )
        }
    }

    @Test("Feeding sends nothing until the confirmation is accepted")
    @MainActor
    func feedingAsksBeforeSending() async throws {
        let home = try Self.home(model: "xiaomi.fishbowl.m200", did: "tank", values: [
            Self.key("tank", 5, 2): .number(12),
        ])
        let mock = MockHomeProvider(snapshot: home)
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.fish.\(UUID().uuidString)")
        store.controlDebounceInterval = .zero
        try await store.waitForTestSnapshot()
        let feed = try #require(store.deviceFacets["tank"]?.first { $0.name == "pet-food-out" })
        #expect(!store.allowAdvancedFacetWrites, "feeding must not require the advanced-writes opt-in")

        var asked = 0
        store.manualControlConfirmationHandler = { prompt in
            asked += 1
            #expect(prompt.message.contains("12"))
            return false
        }
        store.runFacetAction("tank", controlId: feed.id, portion: 2)
        #expect(asked == 1)
        #expect(await mock.executedCommands.isEmpty)

        store.manualControlConfirmationHandler = { _ in true }
        store.runFacetAction("tank", controlId: feed.id, portion: 2)
        await Self.waitUntil {
            await mock.executedCommands.contains(
                .callMiotAction(entityId: "miot-action:tank:2:1", arguments: [.number(2)])
            )
        }
        #expect(await mock.executedCommands == [
            .callMiotAction(entityId: "miot-action:tank:2:1", arguments: [.number(2)]),
        ])
        #expect(store.undoBanner == nil, "dispensed food cannot be undone")
    }

    // MARK: - Pet feeders

    @Test("A pet feeder shows its food level, offers portions and reports a blockage")
    func feederCard() throws {
        let home = try Self.home(model: "binuo.feeder.y1", did: "feeder", values: [
            Self.key("feeder", 2, 1): .number(1),
            Self.key("feeder", 2, 5): .number(1),
        ])
        let (device, card) = try Self.generic(home)
        #expect(card.deviceType == "pet-feeder")
        #expect(card.readouts.map(\.value) == ["Normal"])
        #expect(card.alerts.contains { $0.contains("堵塞") }, "\(card.alerts)")

        let facets = try #require(home.deviceFacets[device.id])
        #expect(facets.filter(\.isHighValue).count <= 8)
        let feed = try #require(facets.first { $0.name == "pet-food-out" })
        #expect(feed.arguments.first?.range?.max == 30)
        #expect(feed.portionArgumentIndex == 0)
        #expect(feed.fedToday == nil, "this model reports no daily total")

        try L10n.$overrideLanguage.withValue(.zhHans) {
            let prompt = ManualControlPolicy.confirmation(facet: feed, value: nil, portion: 2, for: device)
            let message = try #require(prompt?.message)
            #expect(message.contains("2 份"), "\(message)")
            #expect(message.contains("未上报今日已投喂份数"), "\(message)")
        }
    }

    @Test("A second feeder keeps its desiccant controls and does not flag No Faults")
    func videoFeeder() throws {
        let home = try Self.home(model: "xiaomi.feeder.iv2002", did: "feeder", values: [
            Self.key("feeder", 2, 1): .number(0),
            Self.key("feeder", 4, 1): .number(40),
        ])
        let (device, card) = try Self.generic(home)
        #expect(card.deviceType == "pet-feeder")
        #expect(card.alerts.isEmpty, "\(card.alerts)")
        let facets = try #require(home.deviceFacets[device.id])
        #expect(facets.contains { $0.name == "desiccant-left-level" && $0.value == .number(40) })
        #expect(facets.contains { $0.name == "pet-food-out" && $0.portionArgumentIndex == 0 })
        #expect(!facets.contains { $0.name == "feeder-schedule" }, "the vendor schedule stays private")
    }

    // MARK: - Helpers

    @MainActor
    private static func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<200 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }
}
