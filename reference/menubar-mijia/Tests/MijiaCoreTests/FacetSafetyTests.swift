import Foundation
import Testing
@testable import MijiaCore

struct FacetSafetyTests {
    private static func device(_ category: String) -> SmartDevice {
        let kind: DeviceKind
        let model: String
        switch category {
        case "heater", "water-heater", "climate":
            kind = .climate(ClimateSpec(
                entityId: "miot:safety:2", isOn: true, hvacMode: "heat",
                availableModes: ["off", "heat"], targetTemperature: 50, minTemp: 16, maxTemp: 75
            ))
            model = category == "heater" ? "xiaomi.heater.v1" : "xiaomi.aircondition.v1"
        case "camera":
            kind = .camera(CameraSpec(entityId: "miot:safety:2", isOn: true))
            model = "test.camera.v1"
        case "vacuum":
            kind = .vacuum(VacuumSpec(entityId: "miot:safety:2", status: "docked"))
            model = "test.vacuum.v1"
        default:
            kind = .plug(PlugSpec(switchEntityId: "miot:safety:2", isOn: true))
            model = "test.plug.v1"
        }
        return SmartDevice(
            id: "safety", name: category == "water-heater" ? "热水器" : category,
            roomId: "test", roomName: "test", model: model, isOnline: true, kind: kind
        )
    }

    private static func facet(
        name: String = "on", service: String = "auxiliary", kind: MiotFacetKind = .toggle,
        current: JSONValue = .bool(false), whitelisted: Bool = false
    ) -> FacetControl {
        FacetControl(
            id: kind == .button ? "3.a1" : "3.2", kind: kind,
            name: name, title: "辅助设置", serviceName: service, value: current,
            isWritable: true, writesByDefault: whitelisted, isHighValue: true,
            target: kind == .button ? .action(entityId: "miot-action:safety:3:1")
                : .property(entityId: "miot:safety:3", piid: 2)
        )
    }

    @MainActor
    private func makeStore(_ device: SmartDevice, facets: [FacetControl]) async throws -> (HomeStore, MockHomeProvider) {
        var home = SynthesizedHome(devices: [device])
        home.deviceFacets = ["safety": facets]
        let mock = MockHomeProvider(snapshot: home)
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.facet.safety.\(UUID())")
        try await store.waitForTestSnapshot()
        store.allowAdvancedFacetWrites = true
        store.controlDebounceInterval = .zero
        return (store, mock)
    }

    @Test("Dangerous power-on and action writes send nothing without confirmation", arguments: [
        "heater", "water-heater", "plug", "camera", "vacuum"
    ])
    @MainActor
    func hazardousFacetsRequireConfirmation(_ category: String) async throws {
        let property = Self.facet()
        let action = Self.facet(kind: .button)
        let (store, mock) = try await makeStore(Self.device(category), facets: [property, action])
        var prompts = 0
        store.manualControlConfirmationHandler = { prompt in
            prompts += 1
            #expect(prompt.message.contains("辅助设置"))
            return false
        }
        store.setFacetValue("safety", controlId: property.id, value: .bool(true))
        store.runFacetAction("safety", controlId: action.id)
        #expect(prompts == 2)
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.deviceFacets["safety"]?.first?.value == .bool(false))
        #expect(store.undoBanner == nil)
    }

    private static func genericDevice(_ deviceType: String) -> SmartDevice {
        SmartDevice(
            id: "safety", name: deviceType, roomId: "test", roomName: "test",
            model: "generic.\(deviceType)", isOnline: true,
            kind: .generic(GenericSpec(
                deviceType: deviceType,
                typeTitle: deviceType,
                isOn: false,
                powerControl: nil,
                isReadOnly: false
            ))
        )
    }

    @Test("Generic high-risk facets ask again after advanced writes are enabled", arguments: [
        ("garbage-disposal", "on"), ("alertor", "alarm"), ("germicidal-lamp", "on"),
        ("router", "on"), ("lock", "on"), ("multifunction-cooking-pot", "on"),
        ("baby-bottle-warmer", "on"),
    ])
    @MainActor
    func genericHighRiskFacetsConfirm(_ deviceType: String, _ name: String) async throws {
        let facet = Self.facet(name: name, service: deviceType, current: .bool(false), whitelisted: false)
        let (store, mock) = try await makeStore(Self.genericDevice(deviceType), facets: [facet])
        var prompt: ManualControlConfirmation?
        store.manualControlConfirmationHandler = { confirmation in
            prompt = confirmation
            return false
        }
        store.setFacetValue("safety", controlId: facet.id, value: .bool(true))
        #expect(prompt != nil, "\(deviceType) must confirm a high-risk write")
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.deviceFacets["safety"]?.first?.value == .bool(false))
    }

    @Test("Generic heating and appliance off directions stay direct", arguments: [
        "boiler", "multifunction-cooking-pot", "baby-bottle-warmer",
    ])
    func genericHighRiskOffStaysDirect(_ deviceType: String) {
        let facet = Self.facet(name: "on", service: deviceType, current: .bool(true), whitelisted: false)
        #expect(ManualControlPolicy.confirmation(
            facet: facet,
            value: .bool(false),
            for: Self.genericDevice(deviceType)
        ) == nil)
    }

    @Test("Advanced low-risk auxiliary controls execute without a confirmation handler", arguments: [
        ("plug", "brightness"), ("vacuum", "fan-level"), ("vacuum", "suction"),
        ("camera", "child-lock"), ("camera", "delay-time"), ("camera", "buzzer")
    ])
    @MainActor
    func advancedLowRiskWritesDoNotConfirm(_ category: String, _ name: String) async throws {
        let facet = Self.facet(name: name, kind: .slider, current: .number(1))
        let (store, mock) = try await makeStore(Self.device(category), facets: [facet])
        store.setFacetValue("safety", controlId: facet.id, value: .number(2))
        for task in Array(store.debounceTasks.values) { await task.value }
        #expect(await mock.executedCommands == [.setMiotProperty(entityId: "miot:safety:3", piid: 2, value: .number(2))])
        #expect(store.deviceFacets["safety"]?.first?.value == .number(2))
    }

    @Test("Non-heater indicator and buzzer power do not act as main-device power", arguments: ["indicator-light", "buzzer"])
    func lowRiskServicesDoNotConfirm(_ service: String) {
        let facet = Self.facet(service: service)
        #expect(ManualControlPolicy.confirmation(facet: facet, value: .bool(true), for: Self.device("plug")) == nil)
    }

    @Test("Heaters confirm non-off writes but not indicator controls or power-off")
    func heaterKeepsAnyChangeExceptOff() {
        let heater = Self.device("heater")
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(name: "brightness"), value: .number(2), for: heater) != nil)
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(name: "heater-power"), value: .bool(true), for: heater) != nil)
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(name: "heater-power"), value: .bool(false), for: heater) == nil)
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(service: "indicator-light"), value: .bool(true), for: heater) == nil)
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(), value: .bool(false), for: heater) == nil)
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(service: "indicator-light"), value: .bool(false), for: heater) == nil)
        #expect(ManualControlPolicy.confirmation(facet: Self.facet(name: "mode"), value: .string("off"), for: heater) == nil)
    }

    @Test("Water-heater target changes compare with the facet temperature, not the card")
    func waterHeaterTemperatureUsesFacetOrigin() {
        let facet = Self.facet(name: "target-temperature", current: .number(40))
        let device = Self.device("water-heater")
        #expect(ManualControlPolicy.confirmation(facet: facet, value: .number(45), for: device) != nil)
        #expect(ManualControlPolicy.confirmation(facet: facet, value: .number(35), for: device) == nil)
    }

    @Test("Heating child services confirm power-on and hotter targets only", arguments: ["heater", "ptc", "floor-heat"])
    @MainActor
    func heatingChildServiceRequiresConfirmation(_ service: String) async throws {
        let power = Self.facet(service: service)
        let target = Self.facet(name: "target-temperature", service: service, current: .number(30))
        let device = Self.device("climate")
        #expect(ManualControlPolicy.confirmation(facet: power, value: .bool(true), for: device) != nil)
        #expect(ManualControlPolicy.confirmation(facet: power, value: .bool(false), for: device) == nil)
        #expect(ManualControlPolicy.confirmation(facet: target, value: .number(31), for: device) != nil)
        #expect(ManualControlPolicy.confirmation(facet: target, value: .number(29), for: device) == nil)
        let (store, mock) = try await makeStore(device, facets: [target])
        store.setFacetValue("safety", controlId: target.id, value: .number(31))
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.deviceFacets["safety"]?.first?.value == .number(30))
    }

    @Test("Curtain calibration mode stays disabled without advanced writes")
    func curtainCalibrationIsNotWhitelisted() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let data = try Data(contentsOf: root.appendingPathComponent("Tests/Fixtures/miot_coverage_specs.json"))
        let specs = try JSONDecoder().decode([String: MiotDeviceSpec].self, from: data)
        let spec = try #require(specs["090615.curtain.1mcu01"])
        let controls = MiotFacetEngine.controls(for: spec, did: "curtain", values: [:], primaryServiceId: 2)
        let mode = try #require(controls.first { $0.id == "2.5" })
        #expect(mode.options.map(\.label) == ["CalibStop", "CalibStart"])
        #expect(mode.isWritable)
        #expect(!mode.writesByDefault)
        #expect(ManualControlPolicy.confirmation(facet: mode, value: .number(1), for: ManualControlSafetyTests.cover(.curtain)) != nil)
    }
}
