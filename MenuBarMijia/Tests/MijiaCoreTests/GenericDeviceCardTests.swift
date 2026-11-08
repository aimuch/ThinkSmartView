import Foundation
import Testing
@testable import MijiaCore

/// The fallback card for MIoT device types without a dedicated builder (#6).
struct GenericDeviceCardTests {
    private func fixture(_ relative: String) throws -> Data {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let file = directory.appendingPathComponent(relative)
            if FileManager.default.fileExists(atPath: file.path) { return try Data(contentsOf: file) }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func coverageSpec(_ model: String) throws -> MiotDeviceSpec {
        let specs = try JSONDecoder().decode(
            [String: MiotDeviceSpec].self,
            from: fixture("Tests/Fixtures/miot_coverage_specs.json")
        )
        return try #require(specs[model], "\(model)")
    }

    private func record(_ did: String, model: String) -> MiDeviceRecord {
        MiDeviceRecord(did: did, name: did, model: model, isOnline: true)
    }

    private func generic(_ home: SynthesizedHome) throws -> (SmartDevice, GenericSpec) {
        let device = try #require(home.devices.first)
        guard case .generic(let spec) = device.kind else {
            Issue.record("expected a generic card, got \(device.kind)")
            throw CocoaError(.featureUnsupported)
        }
        return (device, spec)
    }

    @Test("A controllable type without a dedicated builder keeps its readings and main switch")
    func controllableGenericCard() throws {
        let model = "209754.fishbowl.tank01"
        let spec = try coverageSpec(model)
        let did = "tank"
        let controls = MiotFacetEngine.controls(for: spec, did: did, values: [:], primaryServiceId: nil)
        let reading = try #require(controls.first { $0.kind == .reading })
        let parts = reading.id.split(separator: ".").compactMap { Int($0) }
        let key = MiotPropKey(did: did, siid: parts[0], piid: parts[1])
        let home = MiotSynthesizer.synthesize(
            devices: [record(did, model: model)],
            rooms: [],
            specs: [model: spec],
            values: [key: .number(24.5), MiotPropKey(did: did, siid: 2, piid: 1): .bool(true)]
        )

        let (device, card) = try generic(home)
        #expect(device.id == did)
        #expect(card.deviceType == "fish-tank")
        #expect(!card.isReadOnly)
        #expect(card.powerControl != nil)
        #expect(card.isOn)
        #expect(device.isPrimaryToggleable)
        #expect(!card.readouts.isEmpty)
        #expect(card.readouts.count <= 2)
        #expect(home.deviceFacets[device.id]?.isEmpty == false)
        #expect(card.powerControl?.writesByDefault == true)
        #expect(home.deviceFacets[device.id]?.contains { $0.id == card.powerControl?.id } == false,
                "the main switch is not repeated under More Settings")
    }

    @Test("A read-only type becomes a generic card with no power control")
    func readOnlyGenericCard() throws {
        let model = "xiaomi.plc.v1"
        let spec = try coverageSpec(model)
        let home = MiotSynthesizer.synthesize(
            devices: [record("repeater", model: model)],
            rooms: [],
            specs: [model: spec],
            values: [:]
        )

        let (device, card) = try generic(home)
        #expect(card.isReadOnly)
        #expect(card.powerControl == nil)
        #expect(!device.isPrimaryToggleable)
        #expect(!device.kind.acceptsControlWrites)
    }

    @Test("Router, lock, and heating services keep generic writes closed by default")
    func highRiskGenericCardsStayClosed() throws {
        let cases: [(model: String, service: String?)] = [
            ("isa.router.mr01hl", nil),
            ("shuse.roller.sr01", "lock"),
            ("macro.boiler.combgl", "water-heater"),
        ]
        for testCase in cases {
            let spec = try coverageSpec(testCase.model)
            let home = MiotSynthesizer.synthesize(
                devices: [record("risk", model: testCase.model)],
                rooms: [],
                specs: [testCase.model: spec],
                values: [:]
            )
            let (device, card) = try generic(home)
            let controls = try #require(home.deviceFacets[device.id])
            if let service = testCase.service {
                let risky = controls.filter { $0.serviceName == service && $0.isWritable }
                #expect(!risky.isEmpty, "\(testCase.model)")
                #expect(risky.allSatisfy { !$0.writesByDefault }, "\(testCase.model)")
            } else {
                #expect(controls.filter(\.isWritable).allSatisfy { !$0.writesByDefault }, "\(testCase.model)")
                #expect(card.powerControl == nil, "\(testCase.model)")
            }
        }
    }

    /// #13 (GH#319): a custom room whose only devices are a router and a NAS disappeared, because
    /// neither model had a dedicated builder and the room list comes from synthesized cards.
    @Test("A room holding only a router and a NAS keeps both generic cards (#13)")
    func routerAndNASKeepTheirRoom() throws {
        let routerModel = "isa.router.mr01hl"
        let nasModel = "xiaomi.station.rp05"
        let specs = [
            routerModel: try coverageSpec(routerModel),
            nasModel: try coverageSpec(nasModel),
        ]
        let records = [record("router", model: routerModel), record("nas", model: nasModel)]
        // The NAS publishes only `indicator-light.on`; switching it on must not become device power.
        let nasLight = MiotPropKey(did: "nas", siid: 5, piid: 1)
        let home = MiotSynthesizer.synthesize(
            devices: records,
            rooms: [MiRoom(id: "room-studio", name: "工作室", dids: ["router", "nas"])],
            specs: specs,
            values: [nasLight: .bool(true)]
        )

        #expect(Set(home.devices.map(\.id)) == ["router", "nas"])
        #expect(home.devices.allSatisfy { device in
            if case .generic = device.kind { return true }
            return false
        })
        #expect(Set(home.devices.map(\.roomName)) == ["工作室"])
        #expect(home.rooms.contains { $0.name == "工作室" })
        #expect(UnsupportedDeviceCollector.miCloud(devices: records, specs: specs, synthesized: home).isEmpty)

        let router = try #require(home.devices.first { $0.id == "router" })
        guard case .generic(let routerCard) = router.kind else {
            Issue.record("expected a generic router card, got \(router.kind)")
            return
        }
        #expect(routerCard.powerControl == nil)
        let routerWrites = (home.deviceFacets["router"] ?? []).filter(\.isWritable)
        #expect(!routerWrites.isEmpty, "the router exposes writable network settings")
        #expect(routerWrites.allSatisfy { !$0.writesByDefault }, "every router write stays closed by default")

        let nas = try #require(home.devices.first { $0.id == "nas" })
        guard case .generic(let nasCard) = nas.kind else {
            Issue.record("expected a generic NAS card, got \(nas.kind)")
            return
        }
        #expect(nasCard.powerControl == nil, "the indicator light is not the NAS main switch")
        #expect(!nasCard.isOn)
        #expect(!nas.isPrimaryToggleable)
    }

    @Test("Generic cards poll every readable facet")
    func pollsGenericFacetValues() throws {
        let model = "209754.fishbowl.tank01"
        let spec = try coverageSpec(model)
        let device = record("tank", model: model)
        let keys = MiotSynthesizer.facetProps(for: device, spec: spec)
        #expect(!keys.isEmpty)
        #expect(MiotSynthesizer.requiredProps(for: device, spec: spec).isEmpty)
        #expect(keys.contains { $0.siid == 2 && $0.piid == 2 } || keys.contains { key in
            spec.service(iid: key.siid)?.properties.first(where: { $0.iid == key.piid })?.isReadable == true
        })
    }

    @Test("Companion lights never become the generic card's main switch", arguments: [
        ("tata.milkheat.bl1108", "milk", 2, 1, JSONValue.bool(true)),
        ("xiaomi.station.rp05", "nas", 2, 1, JSONValue.bool(true)),
        ("miot.dimmer.hhh", "dimmer", 2, 1, JSONValue.bool(true)),
        ("shuse.roller.sr01", "roller", 2, 1, JSONValue.number(1)),
    ])
    func companionLightIsNotMainPower(model: String, did: String, siid: Int, piid: Int, value: JSONValue) throws {
        let spec = try coverageSpec(model)
        let home = MiotSynthesizer.synthesize(
            devices: [record(did, model: model)],
            rooms: [],
            specs: [model: spec],
            values: [MiotPropKey(did: did, siid: siid, piid: piid): value]
        )
        let (_, card) = try generic(home)
        #expect(card.powerControl == nil, "\(model)")
        #expect(!card.isOn, "\(model)")
        #expect(home.devices.first?.kind.canTogglePower == false, "\(model)")
    }

    @Test("Generic high-risk facets need confirmation, while heating off stays direct")
    func genericHighRiskFacetConfirmation() {
        let router = SmartDevice(
            id: "router", name: "路由器", roomId: "room", roomName: "客厅",
            model: "isa.router.mr01hl", isOnline: true,
            kind: .generic(GenericSpec(
                deviceType: "router", typeTitle: "路由器", isReadOnly: false
            ))
        )
        let on = FacetControl(
            id: "2.1", kind: .toggle, name: "on", title: "开关", serviceName: "router",
            value: .bool(false), isWritable: true, writesByDefault: false, isHighValue: false,
            target: .property(entityId: "miot:router:2", piid: 1)
        )
        #expect(ManualControlPolicy.confirmation(facet: on, value: .bool(true), for: router) != nil)
        #expect(ManualControlPolicy.confirmation(facet: on, value: .bool(false), for: router) != nil)

        let boiler = SmartDevice(
            id: "boiler", name: "壁挂炉", roomId: "room", roomName: "客厅",
            model: "macro.boiler.combgl", isOnline: true,
            kind: .generic(GenericSpec(
                deviceType: "boiler", typeTitle: "壁挂炉", isReadOnly: false
            ))
        )
        let boilerOn = FacetControl(
            id: "2.1", kind: .toggle, name: "on", title: "开关", serviceName: "boiler",
            value: .bool(true), isWritable: true, writesByDefault: false, isHighValue: false,
            target: .property(entityId: "miot:boiler:2", piid: 1)
        )
        #expect(ManualControlPolicy.confirmation(facet: boilerOn, value: .bool(false), for: boiler) == nil)
    }

    @Test("A generic card with only a main switch has no drawer")
    func soleMainSwitchHasNoDrawer() {
        let power = FacetControl(
            id: "2.1", kind: .toggle, name: "on", title: "开关", serviceName: "fish-tank",
            value: .bool(false), isWritable: true, writesByDefault: true, isHighValue: false,
            target: .property(entityId: "miot:tank:2", piid: 1)
        )
        let device = SmartDevice(
            id: "tank", name: "鱼缸", roomId: "room", roomName: "客厅",
            model: "acme.fishbowl.v1", isOnline: true,
            kind: .generic(GenericSpec(
                deviceType: "fish-tank", typeTitle: "鱼缸",
                powerControl: power, isReadOnly: false
            ))
        )
        #expect(!device.hasDetailDrawer)

        let readOnly = SmartDevice(
            id: "scale", name: "体重秤", roomId: "room", roomName: "客厅",
            model: "acme.scale.v1", isOnline: true,
            kind: .generic(GenericSpec(
                deviceType: "scale", typeTitle: "体重秤", isReadOnly: true,
                readouts: [.init(value: "65.2 kg")]
            ))
        )
        #expect(readOnly.hasDetailDrawer)
    }

    @Test("Generic cards stay out of All Off and the assistant")
    @MainActor
    func allOffAndAssistantExcludeGenericCards() async throws {
        let power = FacetControl(
            id: "2.1", kind: .toggle, name: "on", title: "开关", serviceName: "fish-tank",
            value: .bool(true), isWritable: true, writesByDefault: true, isHighValue: false,
            target: .property(entityId: "miot:tank:2", piid: 1)
        )
        let card = GenericSpec(
            deviceType: "fish-tank",
            typeTitle: "鱼缸",
            isOn: true,
            powerControl: power,
            isReadOnly: false,
            readouts: [.init(value: "24.5 °C")]
        )
        let device = SmartDevice(
            id: "tank", name: "鱼缸", roomId: "living", roomName: "客厅",
            model: "acme.fishbowl.v1", isOnline: true, kind: .generic(card)
        )
        var home = SynthesizedHome(devices: [device])
        home.deviceFacets = [device.id: [power]]
        let store = HomeStore(
            provider: MockHomeProvider(snapshot: home),
            defaultsSuiteName: "test.generic.\(UUID().uuidString)"
        )
        try await store.waitForTestSnapshot()
        store.controlDebounceInterval = .zero

        #expect(!HomeStore.joinsAllOffByDefault(device))
        #expect(HomeStore.allOffChange(for: device) == nil)
        #expect(store.allOffTargets.isEmpty)
        #expect(!IntentDeviceCategory(device).isAssistantControllable)
        #expect(IntentValidator.normalize(.power(false), for: device) == nil)
        #expect(IntentValidator.normalize(.power(true), for: device) == nil)

        let toggle = try #require(store.togglePrimary(store.devices[0]))
        _ = await toggle.value
        #expect(store.devices[0].isActiveOn == false)
        #expect(store.deviceFacets["tank"]?.first?.value == .bool(false))
    }
}
