import Foundation
import Testing
@testable import MijiaCore

/// #300: a MIoT water heater becomes the shared appliance card. The three fixtures are verbatim
/// released instances from miot-spec.org: an electric heater with a mode list, a gas boiler whose
/// power lives in its own service, and an electric heater with no mode.
struct WaterHeaterDeviceTests {
    static let electricModel = "aden.waterheater.ash200"
    static let gasModel = "viomi.waterheater.u19"
    static let noModeModel = "viomi.waterheater.u3"

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

    private func synthesize(
        _ model: String,
        did: String,
        values: [MiotPropKey: JSONValue]
    ) throws -> (device: SmartDevice, spec: MiotDeviceSpec, record: MiDeviceRecord) {
        let spec = try spec(model)
        let record = MiDeviceRecord(did: did, name: "热水器", model: model, isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [model: spec], values: values
        )
        let device = try #require(home.devices.first)
        return (device, spec, record)
    }

    private func electric(
        did: String = "electric",
        isOn: Bool = true,
        target: Double = 60,
        current: Double = 52,
        mode: Int = 1,
        fault: Int = 0
    ) throws -> (device: SmartDevice, spec: MiotDeviceSpec, record: MiDeviceRecord) {
        try synthesize(Self.electricModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .number(Double(fault)),
            .init(did: did, siid: 2, piid: 2): .number(target),
            .init(did: did, siid: 2, piid: 3): .number(current),
            .init(did: did, siid: 2, piid: 4): .number(Double(mode)),
            .init(did: did, siid: 2, piid: 6): .bool(isOn),
        ])
    }

    @Test("An electric water heater becomes an appliance card with the spec's range and step")
    func electricWaterHeaterCard() throws {
        let (device, spec, record) = try electric()

        guard case .appliance(let appliance) = device.kind else {
            Issue.record("water heater was not synthesized as an appliance")
            return
        }
        #expect(device.id == "electric")
        #expect(device.name == "热水器")
        #expect(appliance.entityId == "miot:electric:2")
        #expect(appliance.powerPresent)
        #expect(appliance.powerWritable)
        #expect(appliance.isOn)
        #expect(appliance.currentTemperature == 52)
        #expect(appliance.targetTemperature == 60)
        #expect(appliance.minTemp == 35)
        #expect(appliance.maxTemp == 75)
        #expect(appliance.step == 0.5)
        #expect(appliance.temperatureWritable)
        #expect(appliance.availableModes == ["Standard", "Energy1", "Energy2", "Quick", "ElectricHeating"])
        #expect(appliance.currentMode == "Energy1")
        #expect(appliance.modeWritable)
        #expect(appliance.faultText == nil)
        #expect(appliance.remainingMinutes == nil)
        #expect(appliance.startAction == nil)
        #expect(appliance.pauseAction == nil)
        #expect(device.isActiveOn)
        #expect(device.hasDetailDrawer)
        #expect(device.statusSubtitle.hasPrefix(L10n.temperature(52, fractionDigits: 1)))
        #expect(device.statusSubtitle.contains(L10n.temperature(60)))

        let props = Set(MiotSynthesizer.requiredProps(for: record, spec: spec))
        #expect(props.isSuperset(of: [
            .init(did: "electric", siid: 2, piid: 1),
            .init(did: "electric", siid: 2, piid: 2),
            .init(did: "electric", siid: 2, piid: 3),
            .init(did: "electric", siid: 2, piid: 4),
            .init(did: "electric", siid: 2, piid: 6),
        ]))

        #expect(try MiotCommandMapper.map(.setSwitch(entityId: appliance.entityId, isOn: false), spec: spec)
            == .setProps([.init(did: "electric", siid: 2, piid: 6, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: appliance.entityId, option: "Energy1"), spec: spec)
            == .setProps([.init(did: "electric", siid: 2, piid: 4, value: .number(1))]))
        #expect(try MiotCommandMapper.map(.setClimateTemperature(entityId: appliance.entityId, temperature: 62.5), spec: spec)
            == .setProps([.init(did: "electric", siid: 2, piid: 2, value: .number(62.5))]))
    }

    @Test("A real gas water heater keeps its spec's mode list, range and status")
    func gasWaterHeaterCard() throws {
        let did = "gas"
        let (device, spec, _) = try synthesize(Self.gasModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .number(0),
            .init(did: did, siid: 2, piid: 2): .number(45),
            .init(did: did, siid: 2, piid: 3): .number(48),
            .init(did: did, siid: 2, piid: 4): .number(3),
            .init(did: did, siid: 2, piid: 5): .number(2),
            .init(did: did, siid: 2, piid: 6): .bool(true),
        ])

        guard case .appliance(let appliance) = device.kind else {
            Issue.record("gas water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.entityId == "miot:gas:2")
        #expect(appliance.powerPresent)
        #expect(appliance.powerWritable)
        #expect(appliance.isOn)
        #expect(appliance.currentTemperature == 48)
        #expect(appliance.targetTemperature == 45)
        #expect(appliance.minTemp == 30)
        #expect(appliance.maxTemp == 65)
        #expect(appliance.step == 1)
        #expect(appliance.availableModes == ["Custom", "Children", "Comfortable", "Old Man", "Kitchen"])
        #expect(appliance.currentMode == "Comfortable")
        #expect(appliance.statusText == L10n.tr("已开启", "On"))
        #expect(appliance.faultText == nil)
        #expect(device.statusSubtitle.hasPrefix(L10n.temperature(48, fractionDigits: 1)))

        #expect(try MiotCommandMapper.map(.setSwitch(entityId: appliance.entityId, isOn: false), spec: spec)
            == .setProps([.init(did: "gas", siid: 2, piid: 6, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: appliance.entityId, option: "Comfortable"), spec: spec)
            == .setProps([.init(did: "gas", siid: 2, piid: 4, value: .number(3))]))
        #expect(try MiotCommandMapper.map(.setClimateTemperature(entityId: appliance.entityId, temperature: 50), spec: spec)
            == .setProps([.init(did: "gas", siid: 2, piid: 2, value: .number(50))]))
    }

    @Test("A water heater without a mode hides the mode selector but keeps its status reading")
    func noModeWaterHeaterCard() throws {
        let did = "no-mode"
        let (device, _, _) = try synthesize(Self.noModeModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .number(55),
            .init(did: did, siid: 2, piid: 2): .number(50),
            .init(did: did, siid: 2, piid: 3): .number(1),
            .init(did: did, siid: 2, piid: 4): .bool(true),
        ])

        guard case .appliance(let appliance) = device.kind else {
            Issue.record("water heater without a mode was not synthesized as an appliance")
            return
        }
        #expect(appliance.availableModes.isEmpty)
        #expect(appliance.currentMode == nil)
        #expect(!appliance.modeWritable)
        #expect(appliance.statusText == L10n.tr("待机", "Standby"))
        #expect(appliance.faultText == nil)
        #expect(appliance.targetTemperature == 55)
        #expect(appliance.currentTemperature == 50)
        #expect(device.statusSubtitle.contains(L10n.tr("待机", "Standby")))
    }

    @Test("An appliance whose spec publishes only power hides every other control")
    func powerOnlyAppliance() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:water-heater:0000A02A:power-only:1",
            description: "Water Heater",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:power-only:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:power-only:1", format: "bool", access: ["read", "write"]),
                    ]
                )
            ]
        )
        let record = MiDeviceRecord(did: "power-only", name: "热水器", model: "test.wh.power", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [.init(did: "power-only", siid: 2, piid: 1): .bool(true)]
        )
        let device = try #require(home.devices.first)
        guard case .appliance(let appliance) = device.kind else {
            Issue.record("power-only water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.powerPresent)
        #expect(appliance.isOn)
        #expect(appliance.availableModes.isEmpty)
        #expect(appliance.targetTemperature == nil)
        #expect(appliance.currentTemperature == nil)
        #expect(appliance.statusText == nil)
        #expect(appliance.faultText == nil)
        #expect(!device.hasDetailDrawer)
        #expect(device.isPrimaryToggleable)
    }

    @Test("Read-only target temperature and mode stay readings, not drawer controls")
    func readOnlyControlsStayHidden() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:water-heater:0000A02A:read-only:1",
            description: "Water Heater",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:read-only:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:read-only:1", format: "bool", access: ["read"]),
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:target-temperature:00000021:read-only:1", format: "float", access: ["read"], valueRange: [30, 70, 1]),
                        MiotProperty(iid: 3, type: "urn:miot-spec-v2:property:mode:00000008:read-only:1", format: "uint8", access: ["read"], valueList: [
                            MiotValueListItem(value: 0, description: "Standard")
                        ]),
                    ]
                )
            ]
        )
        let record = MiDeviceRecord(did: "read-only", name: "热水器", model: "test.wh.readonly", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [
                .init(did: "read-only", siid: 2, piid: 1): .bool(true),
                .init(did: "read-only", siid: 2, piid: 2): .number(55),
                .init(did: "read-only", siid: 2, piid: 3): .number(0),
            ]
        )
        let device = try #require(home.devices.first)
        guard case .appliance(let appliance) = device.kind else {
            Issue.record("read-only water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.targetTemperature == 55)
        #expect(!appliance.temperatureWritable)
        #expect(appliance.availableModes == ["Standard"])
        #expect(!appliance.modeWritable)
        #expect(!device.hasDetailDrawer, "no writable control means no drawer")
        #expect(device.statusSubtitle.contains(L10n.temperature(55)))
        #expect(device.statusSubtitle.contains(SmartDevice.localizeApplianceMode("Standard")))
    }

    @Test("Only a non-zero fault code becomes the fault strip")
    func faultStrip() throws {
        let (healthy, _, _) = try electric(did: "healthy", fault: 0)
        let (faulty, _, _) = try electric(did: "faulty", fault: 2)

        guard case .appliance(let healthySpec) = healthy.kind,
              case .appliance(let faultySpec) = faulty.kind else {
            Issue.record("water heaters were not synthesized")
            return
        }
        #expect(healthySpec.faultText == nil)
        #expect(faultySpec.faultText == "2")
        #expect(faulty.hasDetailDrawer)
    }

    @Test("No-argument start and pause actions appear only when the spec declares them")
    func startAndPauseActions() throws {
        let actionSpec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:water-heater:0000A02A:test-wh:1",
            description: "Water Heater",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:test-wh:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:test-wh:1", format: "bool", access: ["read", "write"]),
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:temperature:00000020:test-wh:1", format: "float", access: ["read"]),
                        MiotProperty(iid: 3, type: "urn:miot-spec-v2:property:target-temperature:00000021:test-wh:1", format: "float", access: ["read", "write"], valueRange: [30, 70, 1]),
                        MiotProperty(iid: 4, type: "urn:miot-spec-v2:property:left-time:0000003C:test-wh:1", format: "uint16", access: ["read"]),
                    ],
                    actions: [
                        MiotAction(iid: 1, type: "urn:miot-spec-v2:action:start:00002801:test-wh:1"),
                        MiotAction(iid: 2, type: "urn:miot-spec-v2:action:pause:00002802:test-wh:1"),
                    ]
                )
            ]
        )
        let record = MiDeviceRecord(did: "action-wh", name: "热水器", model: "test.wh.action", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record],
            rooms: [],
            specs: [record.model: actionSpec],
            values: [
                .init(did: "action-wh", siid: 2, piid: 1): .bool(true),
                .init(did: "action-wh", siid: 2, piid: 2): .number(51),
                .init(did: "action-wh", siid: 2, piid: 3): .number(55),
                .init(did: "action-wh", siid: 2, piid: 4): .number(30),
            ]
        )
        let device = try #require(home.devices.first)
        guard case .appliance(let appliance) = device.kind else {
            Issue.record("action water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.remainingMinutes == 30)
        #expect(appliance.startAction?.entityId == "miot-action:action-wh:2:1")
        #expect(appliance.pauseAction?.entityId == "miot-action:action-wh:2:2")
        let startId = try #require(appliance.startAction?.entityId)
        let pauseId = try #require(appliance.pauseAction?.entityId)
        #expect(try MiotCommandMapper.map(.pressButton(entityId: startId), spec: actionSpec)
            == .action(.init(did: "action-wh", siid: 2, aiid: 1)))
        #expect(try MiotCommandMapper.map(.pressButton(entityId: pauseId), spec: actionSpec)
            == .action(.init(did: "action-wh", siid: 2, aiid: 2)))
    }

    @Test("Turning a water heater on or raising its target asks; turning it off is direct")
    func confirmationRules() throws {
        let (device, _, _) = try electric(isOn: false, target: 60)

        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: device))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: device))
        #expect(ManualControlPolicy.requiresConfirmation(.targetTemperature(65), for: device))
        #expect(!ManualControlPolicy.requiresConfirmation(.targetTemperature(55), for: device))
        #expect(ModelActionPolicy.needsConfirmation(
            DeviceIntent(targetId: device.id, operation: .power(true)),
            devices: [device]
        ))
        #expect(!ModelActionPolicy.needsConfirmation(
            DeviceIntent(targetId: device.id, operation: .power(false)),
            devices: [device]
        ))
    }

    @Test("The store asks before power-on and warmer targets, and writes the appliance controls")
    @MainActor
    func storeControls() async throws {
        let (off, _, _) = try electric(did: "off-wh", isOn: false, target: 60)
        let (on, _, _) = try electric(did: "on-wh", isOn: true, target: 60)
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [off, on]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.count < 2 {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(store.devices.count == 2)

        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }
        let offCard = try #require(store.devices.first { $0.id == "off-wh" })
        #expect(store.togglePrimary(offCard) == nil)
        #expect(prompts == 1)
        #expect(await mock.executedCommands.isEmpty)

        let onCard = try #require(store.devices.first { $0.id == "on-wh" })
        #expect(await store.togglePrimary(onCard)?.value == true)
        #expect(prompts == 1)
        #expect(await mock.executedCommands == [.setSwitch(entityId: "miot:on-wh:2", isOn: false)])

        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return true
        }
        #expect(await store.togglePrimary(offCard)?.value == true)
        #expect(prompts == 2)
        #expect(await mock.executedCommands.last == .setSwitch(entityId: "miot:off-wh:2", isOn: true))

        store.stepApplianceTemperature("off-wh", delta: 5)
        await waitUntil { await mock.executedCommands.count == 3 }
        #expect(prompts == 3)
        #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:off-wh:2", temperature: 65))

        store.stepApplianceTemperature("off-wh", delta: -10)
        await waitUntil { await mock.executedCommands.count == 4 }
        #expect(prompts == 3, "lowering the target is de-escalating")
        #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:off-wh:2", temperature: 55))

        store.setApplianceMode("off-wh", mode: "Energy1")
        await waitUntil { await mock.executedCommands.count == 5 }
        #expect(await mock.executedCommands.last == .setSelectOption(entityId: "miot:off-wh:2", option: "Energy1"))
    }

    @Test("Starting an appliance asks first; pausing it stays direct")
    @MainActor
    func storeConfirmsStartAction() async throws {
        let start = ApplianceAction(kind: .start, entityId: "miot-action:action-wh:2:1")
        let pause = ApplianceAction(kind: .pause, entityId: "miot-action:action-wh:2:2")
        let device = SmartDevice(
            id: "action-wh", name: "热水器", roomId: "bath", roomName: "浴室",
            model: "test.wh.action", isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:action-wh:2",
                isOn: false,
                powerPresent: true,
                powerWritable: true,
                targetTemperature: 55,
                currentTemperature: 51,
                minTemp: 30,
                maxTemp: 70,
                step: 1,
                temperatureWritable: true,
                startAction: start,
                pauseAction: pause,
                heats: true
            ))
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.action.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }

        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }
        store.runApplianceAction(device.id, action: start)
        #expect(prompts == 1)
        #expect(await mock.executedCommands.isEmpty)

        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return true
        }
        store.runApplianceAction(device.id, action: start)
        await waitUntil { await mock.executedCommands.count == 1 }
        #expect(prompts == 2)
        #expect(await mock.executedCommands.last == .pressButton(entityId: start.entityId))

        store.runApplianceAction(device.id, action: pause)
        await waitUntil { await mock.executedCommands.count == 2 }
        #expect(prompts == 2, "pausing is de-escalating")
        #expect(await mock.executedCommands.last == .pressButton(entityId: pause.entityId))
    }

    @Test("Demo mode applies appliance start and pause actions to the card")
    func demoApplianceActionsUpdateState() throws {
        let start = ApplianceAction(kind: .start, entityId: "miot-action:demo-wh:2:1")
        let pause = ApplianceAction(kind: .pause, entityId: "miot-action:demo-wh:2:2")
        let device = SmartDevice(
            id: "demo-wh", name: "热水器", roomId: "bath", roomName: "浴室",
            model: "test.wh.demo", isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:demo-wh:2",
                isOn: false,
                powerPresent: true,
                powerWritable: true,
                startAction: start,
                pauseAction: pause
            ))
        )
        let started = DemoHomeProvider.applying(
            .pressButton(entityId: start.entityId),
            to: SynthesizedHome(devices: [device])
        )
        guard case .appliance(let startedSpec) = started.devices.first?.kind else {
            Issue.record("demo appliance disappeared after start")
            return
        }
        #expect(startedSpec.isOn)
        #expect(startedSpec.statusText == L10n.tr("运行中", "Running"))

        let paused = DemoHomeProvider.applying(
            .pressButton(entityId: pause.entityId),
            to: started
        )
        guard case .appliance(let pausedSpec) = paused.devices.first?.kind else {
            Issue.record("demo appliance disappeared after pause")
            return
        }
        #expect(pausedSpec.isOn, "pause does not power the appliance off")
        #expect(pausedSpec.statusText == L10n.tr("已暂停", "Paused"))
    }

    @Test("Writable companion properties win over read-only primary ones and map to their service")
    func writableCompanionProperties() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:water-heater:0000A02A:companion:1",
            description: "Water Heater",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:companion:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:companion:1", format: "bool", access: ["read"]),
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:target-temperature:00000021:companion:1", format: "float", access: ["read"], valueRange: [30, 70, 1]),
                        MiotProperty(iid: 3, type: "urn:miot-spec-v2:property:mode:00000008:companion:1", format: "uint8", access: ["read"], valueList: [
                            MiotValueListItem(value: 0, description: "Eco"),
                            MiotValueListItem(value: 1, description: "Fast")
                        ]),
                    ]
                ),
                MiotService(
                    iid: 3,
                    type: "urn:miot-spec-v2:service:boiler:0000793F:companion:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:companion:1", format: "bool", access: ["read", "write"]),
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:target-temperature:00000021:companion:1", format: "float", access: ["read", "write"], valueRange: [40, 80, 2]),
                        MiotProperty(iid: 3, type: "urn:miot-spec-v2:property:mode:00000008:companion:1", format: "uint8", access: ["read", "write"], valueList: [
                            MiotValueListItem(value: 0, description: "Eco"),
                            MiotValueListItem(value: 1, description: "Fast")
                        ]),
                    ]
                ),
            ]
        )
        let record = MiDeviceRecord(did: "companion", name: "热水器", model: "test.wh.companion", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [
                .init(did: "companion", siid: 2, piid: 1): .bool(false),
                .init(did: "companion", siid: 2, piid: 2): .number(50),
                .init(did: "companion", siid: 2, piid: 3): .number(0),
                .init(did: "companion", siid: 3, piid: 1): .bool(true),
                .init(did: "companion", siid: 3, piid: 2): .number(55),
                .init(did: "companion", siid: 3, piid: 3): .number(1),
            ]
        )
        let device = try #require(home.devices.first)
        guard case .appliance(let appliance) = device.kind else {
            Issue.record("companion water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.entityId == "miot:companion:2")
        #expect(appliance.isOn)
        #expect(appliance.powerWritable)
        #expect(appliance.targetTemperature == 55)
        #expect(appliance.minTemp == 40)
        #expect(appliance.maxTemp == 80)
        #expect(appliance.step == 2)
        #expect(appliance.temperatureWritable)
        #expect(appliance.currentMode == "Fast")
        #expect(appliance.modeWritable)

        #expect(try MiotCommandMapper.map(.setSwitch(entityId: appliance.entityId, isOn: false), spec: spec)
            == .setProps([.init(did: "companion", siid: 3, piid: 1, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setClimateTemperature(entityId: appliance.entityId, temperature: 60), spec: spec)
            == .setProps([.init(did: "companion", siid: 3, piid: 2, value: .number(60))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: appliance.entityId, option: "Fast"), spec: spec)
            == .setProps([.init(did: "companion", siid: 3, piid: 3, value: .number(1))]))
    }

    @Test("Actions in unrelated services are not surfaced as appliance controls")
    func ignoresUnrelatedActions() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:water-heater:0000A02A:unrelated:1",
            description: "Water Heater",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:unrelated:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:unrelated:1", format: "bool", access: ["read", "write"]),
                    ]
                ),
                MiotService(
                    iid: 4,
                    type: "urn:miot-spec-v2:service:custom-service:00007801:unrelated:1",
                    actions: [
                        MiotAction(iid: 1, type: "urn:miot-spec-v2:action:start:00002801:unrelated:1"),
                    ]
                ),
            ]
        )
        let record = MiDeviceRecord(did: "unrelated", name: "热水器", model: "test.wh.unrelated", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [.init(did: "unrelated", siid: 2, piid: 1): .bool(true)]
        )
        let device = try #require(home.devices.first)
        guard case .appliance(let appliance) = device.kind else {
            Issue.record("water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.startAction == nil, "a custom-service start action is not this card's start")
        #expect(appliance.pauseAction == nil)
        #expect(throws: MiotCommandError.self) {
            try MiotCommandMapper.map(.pressButton(entityId: "miot-action:unrelated:4:1"), spec: spec)
        }
    }

    @Test("A boiler becomes a generic card instead of the water heater cut")
    func boilerTypeIsNotClaimed() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:boiler:0000A0E8:test-boiler:1",
            description: "Boiler",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:boiler:0000793F:test-boiler:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:test-boiler:1", format: "bool", access: ["read", "write"]),
                    ]
                ),
                MiotService(
                    iid: 3,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:test-boiler:1",
                    properties: [
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:target-temperature:00000021:test-boiler:1", format: "float", access: ["read", "write"], valueRange: [32, 60, 1]),
                    ]
                ),
            ]
        )
        let record = MiDeviceRecord(did: "boiler", name: "壁挂炉", model: "test.boiler.one", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [
                .init(did: "boiler", siid: 2, piid: 1): .bool(true),
                .init(did: "boiler", siid: 3, piid: 2): .number(45),
            ]
        )
        let device = try #require(home.devices.first)
        guard case .generic(let card) = device.kind else {
            Issue.record("boiler should take the generic fallback, not the water heater cut")
            return
        }
        #expect(card.deviceType == "boiler")
        #expect(card.powerControl == nil)
        #expect(home.deviceFacets[device.id]?.contains {
            $0.serviceName == "water-heater" && $0.isWritable && !$0.writesByDefault
        } == true)
    }

    @Test("A missing target reading hides the stepper instead of inventing the minimum")
    func missingTargetReadingHidesStepper() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:water-heater:0000A02A:no-reading:1",
            description: "Water Heater",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:water-heater:0000783E:no-reading:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:no-reading:1", format: "bool", access: ["read", "write"]),
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:target-temperature:00000021:no-reading:1", format: "float", access: ["read", "write"], valueRange: [40, 80, 2]),
                    ]
                )
            ]
        )
        let record = MiDeviceRecord(did: "no-reading", name: "热水器", model: "test.wh.no-reading", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [.init(did: "no-reading", siid: 2, piid: 1): .bool(true)]
        )
        let device = try #require(home.devices.first)
        guard case .appliance(let appliance) = device.kind else {
            Issue.record("water heater was not synthesized as an appliance")
            return
        }
        #expect(appliance.targetTemperature == nil)
        #expect(!device.hasDetailDrawer, "no target reading means no temperature control")
    }

    @Test("Undoing All Off restores an opted-in water heater")
    @MainActor
    func allOffUndoRestoresWaterHeater() async throws {
        let (device, _, _) = try electric(isOn: true)
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.undo.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.setJoinsAllOff(true, deviceId: device.id)
        #expect(store.allOffTargets.map(\.id) == [device.id])

        store.turnAllOff()
        await waitUntil { await mock.executedCommands.count == 1 }
        #expect(await mock.executedCommands == [.setSwitch(entityId: "miot:electric:2", isOn: false)])
        #expect(store.devices.first?.isActiveOn == false)

        let undo = try #require(store.undoTurnAllOff(), "All Off undo must apply to an appliance")
        await undo.value
        #expect(await mock.executedCommands.last == .setSwitch(entityId: "miot:electric:2", isOn: true))
        #expect(store.devices.first?.isActiveOn == true)
    }

    @Test("A Fahrenheit tap moves a step-2 appliance target")
    @MainActor
    func fahrenheitStepOnCoarseStep() async throws {
        let device = SmartDevice(
            id: "coarse", name: "热水器", roomId: "bath", roomName: "浴室",
            model: "test.wh.coarse", isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:coarse:2",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                targetTemperature: 50,
                currentTemperature: 48,
                minTemp: 40,
                maxTemp: 80,
                step: 2,
                temperatureWritable: true
            ))
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.fahrenheit.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.manualControlConfirmationHandler = { _ in true }

        try await L10n.$overrideTemperatureUnit.withValue(.fahrenheit) {
            store.stepApplianceTemperature(device.id, delta: L10n.celsiusStep(2))
            await waitUntil { await mock.executedCommands.count == 1 }
            #expect(store.devices.first?.applianceTargetTemperature == 52)
            #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:coarse:2", temperature: 52))

            store.stepApplianceTemperature(device.id, delta: -L10n.celsiusStep(2))
            await waitUntil { await mock.executedCommands.count == 2 }
            #expect(store.devices.first?.applianceTargetTemperature == 50)
            #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:coarse:2", temperature: 50))
        }
    }

    @Test("A Fahrenheit tap moves a half-degree appliance target")
    @MainActor
    func fahrenheitStepOnHalfDegreeStep() async throws {
        let (device, _, _) = try electric(target: 40)
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.fahrenheit-half.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.manualControlConfirmationHandler = { _ in true }

        try await L10n.$overrideTemperatureUnit.withValue(.fahrenheit) {
            store.stepApplianceTemperature(device.id, delta: L10n.celsiusStep(0.5))
            await waitUntil { await mock.executedCommands.count == 1 }
            #expect(store.devices.first?.applianceTargetTemperature == 40.5)
            #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:electric:2", temperature: 40.5))

            store.stepApplianceTemperature(device.id, delta: -L10n.celsiusStep(0.5))
            await waitUntil { await mock.executedCommands.count == 2 }
            #expect(store.devices.first?.applianceTargetTemperature == 40)

            store.stepApplianceTemperature(device.id, delta: -L10n.celsiusStep(0.5))
            await waitUntil { await mock.executedCommands.count == 3 }
            #expect(store.devices.first?.applianceTargetTemperature == 39.5)
            #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:electric:2", temperature: 39.5))
        }
    }

    @Test("A 0.1 °C step lands on the adjacent target")
    @MainActor
    func decimalStepKeepsAdjacentTarget() async throws {
        let device = SmartDevice(
            id: "fine", name: "热水器", roomId: "bath", roomName: "浴室",
            model: "test.wh.fine", isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:fine:2",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                targetTemperature: 40.1,
                currentTemperature: 38,
                minTemp: 30,
                maxTemp: 50,
                step: 0.1,
                temperatureWritable: true
            ))
        )
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.decimal.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        store.manualControlConfirmationHandler = { _ in true }

        try await L10n.$overrideTemperatureUnit.withValue(.celsius) {
            store.stepApplianceTemperature(device.id, delta: L10n.celsiusStep(0.1))
            await waitUntil { await mock.executedCommands.count == 1 }
            #expect(store.devices.first?.applianceTargetTemperature == 40.2)
            #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:fine:2", temperature: 40.2))

            store.stepApplianceTemperature(device.id, delta: -L10n.celsiusStep(0.1))
            await waitUntil { await mock.executedCommands.count == 2 }
            #expect(store.devices.first?.applianceTargetTemperature == 40.1)
            #expect(await mock.executedCommands.last == .setClimateTemperature(entityId: "miot:fine:2", temperature: 40.1))
        }
    }

    @Test("Assistant plans still skip appliance cards")
    @MainActor
    func assistantDoesNotControlAppliances() async throws {
        let (device, _, _) = try electric(isOn: true)
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.waterheater.assistant.\(UUID().uuidString)")
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }

        _ = await store.executeIntentPlan(
            IntentPlan(
                intents: [DeviceIntent(targetId: device.id, operation: .power(false))],
                reply: "关闭热水器",
                source: .local
            ),
            recordedUtterance: "关闭热水器",
            origin: .userInitiated
        )
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.devices.first?.isActiveOn == true)
    }

    @Test("A water heater from both sources produces one card")
    func dualSourceDeduplicates() throws {
        let (device, _, record) = try electric()
        var miHome = SynthesizedHome(devices: [device])
        miHome.deviceIdentities = [device.id: DeviceSourceIdentity(
            dids: [record.did],
            model: record.model,
            name: device.name,
            roomName: device.roomName,
            kindTag: .appliance
        )]

        let haDevice = SmartDevice(
            id: "ha-water-heater", name: "热水器", roomId: "bath", roomName: "浴室",
            model: record.model, isOnline: true,
            kind: .climate(ClimateSpec(
                entityId: "climate.water_heater",
                isOn: true,
                hvacMode: "heat",
                availableModes: ["off", "heat"],
                targetTemperature: 60,
                currentTemperature: 52,
                minTemp: 35,
                maxTemp: 75
            ))
        )
        var haHome = SynthesizedHome(devices: [haDevice])
        haHome.deviceIdentities = [haDevice.id: DeviceSourceIdentity(
            dids: [record.did],
            model: record.model,
            name: haDevice.name,
            roomName: haDevice.roomName,
            kindTag: .climate
        )]

        let merged = DeviceDeduplicator.merge(homeAssistant: haHome, xiaomiCloud: miHome)
        #expect(merged.home.devices.count == 1)
        let card = try #require(merged.home.devices.first)
        guard case .appliance = card.kind else {
            Issue.record("the merged card kept the Home Assistant climate card instead of the appliance")
            return
        }
        #expect(merged.routes[card.id]?.backend == .xiaomiCloud)
        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: card))
    }

    @Test("A water heater stays out of All Off unless the user opts in")
    func allOffExcludesWaterHeater() throws {
        let (device, _, _) = try electric()
        #expect(!HomeStore.joinsAllOffByDefault(device))
        let change = try #require(HomeStore.allOffChange(for: device))
        #expect(change.off == .setSwitch(entityId: "miot:electric:2", isOn: false))
        #expect(change.restore == .setSwitch(entityId: "miot:electric:2", isOn: true))
    }

    @MainActor
    private func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<100 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(20))
        }
        Issue.record("condition was not met in time")
    }
}

private extension SmartDevice {
    var applianceTargetTemperature: Double? {
        guard case .appliance(let spec) = kind else { return nil }
        return spec.targetTemperature
    }
}
