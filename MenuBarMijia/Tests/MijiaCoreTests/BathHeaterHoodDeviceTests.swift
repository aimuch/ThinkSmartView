import Foundation
import Testing
@testable import MijiaCore

/// #4: a bath heater and a hood publish their main function across several MIoT services.
/// These fixtures are verbatim released instances from miot-spec.org: two bath heaters whose
/// heat lives in `ptc-bath-heater` with a separate `light` and `fan-control`, and two hoods whose
/// exhaust lives in `hood` with a separate `fan-control` and `light`.
struct BathHeaterHoodDeviceTests {
    static let bathHeaterModels = ["xiaomi.bhf_light.v1", "leishi.bhf_light.smart"]
    static let hoodModels = ["viomi.hood.c26", "alsmai.hood.a31203"]

    private func spec(_ model: String) throws -> MiotDeviceSpec {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let fixture = directory.appendingPathComponent("Tests/Fixtures/miot/\(model).json")
            if FileManager.default.fileExists(atPath: fixture.path) {
                return try JSONDecoder().decode(MiotDeviceSpec.self, from: Data(contentsOf: fixture))
            }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func synthesize(
        _ model: String,
        did: String,
        name: String,
        values: [MiotPropKey: JSONValue]
    ) throws -> (home: SynthesizedHome, device: SmartDevice, spec: MiotDeviceSpec, record: MiDeviceRecord) {
        let spec = try spec(model)
        let record = MiDeviceRecord(did: did, name: name, model: model, isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [model: spec], values: values
        )
        let device = try #require(home.devices.first)
        return (home, device, spec, record)
    }

    private func appliance(_ device: SmartDevice) throws -> ApplianceSpec {
        guard case .appliance(let spec) = device.kind else {
            Issue.record("\(device.model) was not synthesized as an appliance card")
            throw CocoaError(.featureUnsupported)
        }
        return spec
    }

    @Test("A bath heater aggregates heat, mode, target temperature, fan level and light")
    func bathHeaterAggregatesItsServices() throws {
        let did = "bh1"
        let (home, device, spec, record) = try synthesize(
            Self.bathHeaterModels[0], did: did, name: "浴霸", values: [
                .init(did: did, siid: 2, piid: 1): .bool(true),
                .init(did: did, siid: 3, piid: 1): .number(4),
                .init(did: did, siid: 3, piid: 3): .bool(true),
                .init(did: did, siid: 3, piid: 8): .number(40),
                .init(did: did, siid: 5, piid: 3): .number(3),
            ]
        )
        let card = try appliance(device)

        #expect(home.devices.count == 1)
        #expect(device.id == did)
        #expect(card.entityId == "miot:\(did):3")
        #expect(card.powerEntityId == "miot:\(did):3")
        #expect(card.powerPresent)
        #expect(card.powerWritable)
        #expect(card.isOn)
        #expect(card.currentMode == "Quick Heat")
        #expect(card.availableModes == ["Idle", "Dry", "Defog", "Quick Defog", "Quick Heat", "Other"])
        #expect(card.modeWritable)
        #expect(card.targetTemperature == 40)
        #expect(card.minTemp == 25)
        #expect(card.maxTemp == 45)
        #expect(card.step == 1)
        #expect(card.temperatureWritable)
        #expect(card.light?.entityId == "miot:\(did):2")
        #expect(card.light?.isOn == true)
        #expect(card.light?.writable == true)
        #expect(card.fanLevel?.entityId == "miot:\(did):5")
        #expect(card.fanLevel?.current == "High")
        #expect(card.fanLevel?.options == ["Low", "High"])
        #expect(card.fanLevel?.writable == true)
        #expect(card.symbolName == "heater.vertical")
        #expect(device.isActiveOn)
        #expect(device.hasDetailDrawer)
        #expect(device.statusSubtitle.contains(SmartDevice.localizeApplianceMode("Quick Heat")))
        #expect(device.statusSubtitle.contains(SmartDevice.localizeApplianceFanLevel("High")))

        let props = Set(MiotSynthesizer.requiredProps(for: record, spec: spec))
        #expect(props.isSuperset(of: [
            .init(did: did, siid: 2, piid: 1),
            .init(did: did, siid: 3, piid: 1),
            .init(did: did, siid: 3, piid: 3),
            .init(did: did, siid: 3, piid: 8),
            .init(did: did, siid: 5, piid: 3),
        ]))

        #expect(try MiotCommandMapper.map(.setSwitch(entityId: card.powerEntityId, isOn: false), spec: spec)
            == .setProps([.init(did: did, siid: 3, piid: 3, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setSwitch(entityId: "miot:\(did):2", isOn: true), spec: spec)
            == .setProps([.init(did: did, siid: 2, piid: 1, value: .bool(true))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: card.entityId, option: "Quick Heat"), spec: spec)
            == .setProps([.init(did: did, siid: 3, piid: 1, value: .number(4))]))
        #expect(try MiotCommandMapper.map(.setClimateTemperature(entityId: card.entityId, temperature: 41), spec: spec)
            == .setProps([.init(did: did, siid: 3, piid: 8, value: .number(41))]))
        #expect(try MiotCommandMapper.map(.setClimateFanMode(entityId: "miot:\(did):5", fanMode: "High"), spec: spec)
            == .setProps([.init(did: did, siid: 5, piid: 3, value: .number(3))]))
    }

    @Test("A bath heater's room reading comes from its PTC service and joins the environment strip")
    func bathHeaterRoomReading() throws {
        let did = "bh2"
        let (_, device, _, _) = try synthesize(
            Self.bathHeaterModels[1], did: did, name: "浴霸", values: [
                .init(did: did, siid: 2, piid: 1): .bool(false),
                .init(did: did, siid: 4, piid: 2): .number(2),
                .init(did: did, siid: 4, piid: 3): .number(2),
                .init(did: did, siid: 4, piid: 4): .number(38),
                .init(did: did, siid: 4, piid: 5): .number(26),
                .init(did: did, siid: 4, piid: 6): .bool(true),
            ]
        )
        let card = try appliance(device)

        #expect(card.entityId == "miot:\(did):4")
        #expect(card.currentTemperature == 26)
        #expect(card.targetTemperature == 38)
        #expect(card.currentMode == "Shower")
        #expect(card.statusText == L10n.tr("运行中", "Running"))
        #expect(card.light?.isOn == false)
        #expect(card.roomReading)
        #expect(device.environmentReading?.temperature == 26)
        #expect(device.environmentReading?.humidity == nil)
    }

    @Test("A hood aggregates exhaust power, fan level and light")
    func hoodAggregatesItsServices() throws {
        let did = "hood1"
        let (home, device, spec, record) = try synthesize(
            Self.hoodModels[0], did: did, name: "油烟机", values: [
                .init(did: did, siid: 2, piid: 1): .bool(true),
                .init(did: did, siid: 2, piid: 2): .number(0),
                .init(did: did, siid: 2, piid: 7): .number(2),
                .init(did: did, siid: 6, piid: 1): .bool(true),
                .init(did: did, siid: 10, piid: 2): .number(1),
            ]
        )
        let card = try appliance(device)

        #expect(home.devices.count == 1)
        #expect(card.entityId == "miot:\(did):2")
        #expect(card.powerEntityId == "miot:\(did):2")
        #expect(card.powerPresent)
        #expect(card.powerWritable)
        #expect(card.isOn)
        #expect(card.fanLevel?.entityId == "miot:\(did):10")
        #expect(card.fanLevel?.current == "High")
        #expect(card.fanLevel?.options == ["Low", "High", "Stir Fry"])
        #expect(card.fanLevel?.writable == true)
        #expect(card.light?.entityId == "miot:\(did):6")
        #expect(card.light?.isOn == true)
        #expect(card.light?.writable == true)
        #expect(card.symbolName == "wind")
        #expect(device.isActiveOn)
        #expect(device.hasDetailDrawer)
        #expect(device.statusSubtitle.contains(SmartDevice.localizeApplianceFanLevel("High")))

        let props = Set(MiotSynthesizer.requiredProps(for: record, spec: spec))
        #expect(props.isSuperset(of: [
            .init(did: did, siid: 2, piid: 1),
            .init(did: did, siid: 6, piid: 1),
            .init(did: did, siid: 10, piid: 2),
        ]))

        #expect(try MiotCommandMapper.map(.setSwitch(entityId: card.powerEntityId, isOn: false), spec: spec)
            == .setProps([.init(did: did, siid: 2, piid: 1, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setSwitch(entityId: "miot:\(did):6", isOn: false), spec: spec)
            == .setProps([.init(did: did, siid: 6, piid: 1, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setClimateFanMode(entityId: "miot:\(did):10", fanMode: "Stir Fry"), spec: spec)
            == .setProps([.init(did: did, siid: 10, piid: 2, value: .number(2))]))
    }

    @Test("A hood whose fan-control starts at Level1 keeps the hood's own power switch")
    func hoodFanLevelsStartAtOne() throws {
        let did = "hood2"
        let (_, device, spec, _) = try synthesize(
            Self.hoodModels[1], did: did, name: "油烟机", values: [
                .init(did: did, siid: 2, piid: 1): .bool(false),
                .init(did: did, siid: 6, piid: 1): .bool(false),
                .init(did: did, siid: 7, piid: 2): .number(10),
            ]
        )
        let card = try appliance(device)

        #expect(card.entityId == "miot:\(did):2")
        #expect(card.isOn == false)
        #expect(card.fanLevel?.entityId == "miot:\(did):7")
        #expect(card.fanLevel?.current == "High")
        #expect(card.fanLevel?.options.count == 10)
        #expect(try MiotCommandMapper.map(.setClimateFanMode(entityId: "miot:\(did):7", fanMode: "High"), spec: spec)
            == .setProps([.init(did: did, siid: 7, piid: 2, value: .number(10))]))
    }

    @Test("The store writes a bath heater's light and fan level on their own services")
    @MainActor
    func storeWritesAggregatedControls() async throws {
        let did = "bh5"
        let (_, device, _, _) = try synthesize(
            Self.bathHeaterModels[0], did: did, name: "浴霸", values: [
                .init(did: did, siid: 2, piid: 1): .bool(true),
                .init(did: did, siid: 3, piid: 1): .number(4),
                .init(did: did, siid: 3, piid: 3): .bool(true),
                .init(did: did, siid: 3, piid: 8): .number(40),
                .init(did: did, siid: 5, piid: 3): .number(3),
            ]
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.bathheater.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }

        store.setApplianceLightPower(did, isOn: false)
        await waitUntil { await mock.executedCommands.count == 1 }
        #expect(await mock.executedCommands.last == .setSwitch(entityId: "miot:\(did):2", isOn: false))
        #expect(store.devices.first { $0.id == did }?.applianceCard?.light?.isOn == false)

        store.setApplianceFanLevel(did, level: "Low")
        await waitUntil { await mock.executedCommands.count == 2 }
        #expect(await mock.executedCommands.last == .setClimateFanMode(entityId: "miot:\(did):5", fanMode: "Low"))
        #expect(store.devices.first { $0.id == did }?.applianceCard?.fanLevel?.current == "Low")
    }

    @MainActor
    private func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<100 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(20))
        }
        Issue.record("condition was not met in time")
    }

    @Test("Undo after All Off restores an appliance whose power lives on a companion service")
    @MainActor
    func undoAllOffUsesPowerService() async throws {
        let device = SmartDevice(
            id: "undo-appliance",
            name: "浴霸",
            roomId: "bath",
            roomName: "浴室",
            model: "xiaomi.bhf_light.v1",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:undo-appliance:3",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                powerEntityId: "miot:undo-appliance:5"
            ))
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.appliance.undo.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.setJoinsAllOff(true, deviceId: device.id)
        store.pauseUndoBannerCountdown()
        store.turnAllOff()
        await waitUntil { await mock.executedCommands.count == 1 }
        #expect(await mock.executedCommands == [.setSwitch(entityId: "miot:undo-appliance:5", isOn: false)])
        #expect(store.devices.first?.isActiveOn == false)

        let undo = store.undoTurnAllOff()
        await undo?.value
        #expect(await mock.executedCommands.last == .setSwitch(entityId: "miot:undo-appliance:5", isOn: true))
        #expect(store.devices.first?.isActiveOn == true)
    }

    @Test("A bath heater's heating property wins over a companion fan switch")
    func bathHeaterPowerPrefersPrimaryHeating() throws {
        let spec = MiotDeviceSpec(type: "urn:miot-spec-v2:device:bath-heater:0000A028:test:1", services: [
            MiotService(iid: 2, type: "urn:miot-spec-v2:service:light:00007802:test:1", properties: [
                boolProperty(1, "on"),
            ]),
            MiotService(iid: 3, type: "urn:miot-spec-v2:service:ptc-bath-heater:00007836:test:1", properties: [
                MiotProperty(
                    iid: 1,
                    type: "urn:miot-spec-v2:property:mode:00000008:test:1",
                    format: "uint8",
                    access: ["read", "write"],
                    valueList: [
                        MiotValueListItem(value: 0, description: "Idle"),
                        MiotValueListItem(value: 3, description: "Heat"),
                    ]
                ),
                boolProperty(3, "heating"),
                MiotProperty(
                    iid: 8,
                    type: "urn:miot-spec-v2:property:target-temperature:00000021:test:1",
                    format: "float",
                    access: ["read", "write"],
                    valueRange: [25, 45, 1]
                ),
            ]),
            MiotService(iid: 5, type: "urn:miot-spec-v2:service:fan-control:00007808:test:1", properties: [
                boolProperty(3, "on"),
                MiotProperty(
                    iid: 4,
                    type: "urn:miot-spec-v2:property:fan-level:00000016:test:1",
                    format: "uint8",
                    access: ["read", "write"],
                    valueList: [
                        MiotValueListItem(value: 1, description: "Low"),
                        MiotValueListItem(value: 3, description: "High"),
                    ]
                ),
            ]),
        ])
        let did = "bh-power"
        let record = MiDeviceRecord(did: did, name: "浴霸", model: "test.bath.heater", isOnline: true)
        let home = MiotSynthesizer.synthesize(devices: [record], rooms: [], specs: [record.model: spec], values: [
            .init(did: did, siid: 2, piid: 1): .bool(true),
            .init(did: did, siid: 3, piid: 1): .number(3),
            .init(did: did, siid: 3, piid: 3): .bool(true),
            .init(did: did, siid: 3, piid: 8): .number(40),
            .init(did: did, siid: 5, piid: 3): .bool(true),
            .init(did: did, siid: 5, piid: 4): .number(3),
        ])
        let device = try #require(home.devices.first)
        let card = try appliance(device)

        #expect(card.powerEntityId == "miot:\(did):3")
        #expect(card.isOn)
        #expect(card.fanLevel?.entityId == "miot:\(did):5")
        #expect(try MiotCommandMapper.map(.setSwitch(entityId: card.powerEntityId, isOn: false), spec: spec)
            == .setProps([.init(did: did, siid: 3, piid: 3, value: .bool(false))]))
    }

    @Test("A hood's exhaust power is not a high-risk action, while bath heat is")
    func applianceHazardFollowsTheHeatFlag() throws {
        let bath = try synthesize(
            Self.bathHeaterModels[0], did: "haz-bath", name: "浴霸", values: [
                .init(did: "haz-bath", siid: 3, piid: 1): .number(0),
                .init(did: "haz-bath", siid: 3, piid: 3): .bool(false),
            ]
        ).device
        let hood = try synthesize(
            Self.hoodModels[0], did: "haz-hood", name: "油烟机", values: [
                .init(did: "haz-hood", siid: 2, piid: 1): .bool(false),
            ]
        ).device

        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: bath))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: bath))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(true), for: hood))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: hood))

        #expect(!ApplianceSpec.modeStartsHeating("Idle"))
        #expect(!ApplianceSpec.modeStartsHeating("Fan"))
        #expect(!ApplianceSpec.modeStartsHeating("Ventilate"))
        #expect(ApplianceSpec.modeStartsHeating("Shower"))
        #expect(ApplianceSpec.modeStartsHeating("Quick Heat"))
    }

    @Test("Switching a bath heater from standby into heat asks, and a hood power-on stays direct")
    @MainActor
    func storeConfirmsHeatModesButNotHoodPower() async throws {
        let bathDid = "mode-bath"
        let (_, bathDevice, _, _) = try synthesize(
            Self.bathHeaterModels[0], did: bathDid, name: "浴霸", values: [
                .init(did: bathDid, siid: 2, piid: 1): .bool(false),
                .init(did: bathDid, siid: 3, piid: 1): .number(0),
                .init(did: bathDid, siid: 3, piid: 3): .bool(false),
                .init(did: bathDid, siid: 3, piid: 8): .number(40),
                .init(did: bathDid, siid: 5, piid: 3): .number(3),
            ]
        )
        let hoodDid = "mode-hood"
        let (_, hoodDevice, _, _) = try synthesize(
            Self.hoodModels[0], did: hoodDid, name: "油烟机", values: [
                .init(did: hoodDid, siid: 2, piid: 1): .bool(false),
                .init(did: hoodDid, siid: 6, piid: 1): .bool(false),
                .init(did: hoodDid, siid: 10, piid: 2): .number(1),
            ]
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [bathDevice, hoodDevice]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.appliance.hazard.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.count < 2 {
            try await Task.sleep(for: .milliseconds(10))
        }

        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }
        store.setApplianceMode(bathDid, mode: "Quick Heat")
        #expect(prompts == 1)
        #expect(await mock.executedCommands.isEmpty)

        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return true
        }
        store.setApplianceMode(bathDid, mode: "Quick Heat")
        await waitUntil { await mock.executedCommands.count == 1 }
        #expect(await mock.executedCommands.last == .setSelectOption(entityId: "miot:\(bathDid):3", option: "Quick Heat"))

        prompts = 0
        let hoodCard = try #require(store.devices.first { $0.id == hoodDid })
        #expect(await store.togglePrimary(hoodCard)?.value == true)
        #expect(prompts == 0)
        #expect(await mock.executedCommands.last == .setSwitch(entityId: "miot:\(hoodDid):2", isOn: true))
    }

    @Test("Appliance command routing includes mode and temperature companion services")
    func applianceCommandDidsCoverAllServices() throws {
        let device = SmartDevice(
            id: "routing",
            name: "浴霸",
            roomId: "bath",
            roomName: "浴室",
            model: "test.appliance.routing",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:main-did:2",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                modeEntityId: "miot:mode-did:4",
                temperatureEntityId: "miot:temp-did:5"
            ))
        )
        let dids = Set(device.miotCommandDids)
        #expect(dids.isSuperset(of: ["main-did", "mode-did", "temp-did"]))
    }

    @Test("A mode list without a value list does not shadow the writable HVAC mode")
    func mapperPicksTheWritableModeList() throws {
        let spec = MiotDeviceSpec(type: "urn:miot-spec-v2:device:air-conditioner:0000A004:test:1", services: [
            MiotService(iid: 2, type: "urn:miot-spec-v2:service:air-conditioner:0000780A:test:1", properties: [
                boolProperty(1, "on"),
                MiotProperty(
                    iid: 2,
                    type: "urn:miot-spec-v2:property:mode:00000008:test:1",
                    format: "uint8",
                    access: ["read", "write"]
                ),
                MiotProperty(
                    iid: 3,
                    type: "urn:miot-spec-v2:property:mode:00000008:test:1",
                    format: "uint8",
                    access: ["read", "write"],
                    valueList: [
                        MiotValueListItem(value: 1, description: "Cool"),
                        MiotValueListItem(value: 2, description: "Heat"),
                    ]
                ),
            ]),
        ])
        #expect(try MiotCommandMapper.map(.setClimateMode(entityId: "miot:ac:2", hvacMode: "cool"), spec: spec)
            == .setProps([
                .init(did: "ac", siid: 2, piid: 1, value: .bool(true)),
                .init(did: "ac", siid: 2, piid: 3, value: .number(1)),
            ]))
    }

    @Test("Fan level writes skip a read-only duplicate")
    func mapperSkipsReadOnlyFanLevel() throws {
        let spec = MiotDeviceSpec(type: "urn:miot-spec-v2:device:hood:0000A01B:test:1", services: [
            MiotService(iid: 7, type: "urn:miot-spec-v2:service:fan-control:00007808:test:1", properties: [
                MiotProperty(
                    iid: 2,
                    type: "urn:miot-spec-v2:property:fan-level:00000016:test:1",
                    format: "uint8",
                    access: ["read"],
                    valueList: [
                        MiotValueListItem(value: 1, description: "Low"),
                        MiotValueListItem(value: 3, description: "High"),
                    ]
                ),
                MiotProperty(
                    iid: 3,
                    type: "urn:miot-spec-v2:property:fan-level:00000016:test:1",
                    format: "uint8",
                    access: ["read", "write"],
                    valueList: [
                        MiotValueListItem(value: 1, description: "Low"),
                        MiotValueListItem(value: 3, description: "High"),
                    ]
                ),
            ]),
        ])
        #expect(try MiotCommandMapper.map(.setClimateFanMode(entityId: "miot:hood:7", fanMode: "High"), spec: spec)
            == .setProps([.init(did: "hood", siid: 7, piid: 3, value: .number(3))]))
    }

    private func boolProperty(_ iid: Int, _ name: String) -> MiotProperty {
        MiotProperty(
            iid: iid,
            type: "urn:miot-spec-v2:property:\(name):00000006:test:1",
            format: "bool",
            access: ["read", "write"]
        )
    }

    @Test("A bath heater or hood's light service never becomes its own card")
    func companionLightDoesNotBecomeACard() throws {
        let cases: [(model: String, service: String, did: String, name: String)] = [
            (Self.bathHeaterModels[0], "ptc-bath-heater", "bh3", "浴霸"),
            (Self.bathHeaterModels[1], "ptc-bath-heater", "bh4", "浴霸"),
            (Self.hoodModels[0], "hood", "hood3", "油烟机"),
            (Self.hoodModels[1], "hood", "hood4", "油烟机"),
        ]
        for item in cases {
            let spec = try spec(item.model)
            #expect(MiotSynthesizer.supportedServiceName(for: spec) == item.service, "\(item.model)")
            let record = MiDeviceRecord(did: item.did, name: item.name, model: item.model, isOnline: true)
            let home = MiotSynthesizer.synthesize(
                devices: [record], rooms: [], specs: [item.model: spec], values: [:]
            )
            #expect(home.devices.count == 1, "\(item.model) produced extra cards")
            let kind = try #require(home.devices.first?.kind)
            if case .light = kind {
                Issue.record("\(item.model) still draws its companion light as the main card")
            }
            if case .appliance = kind {} else {
                Issue.record("\(item.model) is not an appliance card")
            }
        }
    }
}

private extension SmartDevice {
    var applianceCard: ApplianceSpec? {
        guard case .appliance(let spec) = kind else { return nil }
        return spec
    }
}
