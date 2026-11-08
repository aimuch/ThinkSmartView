import Foundation
import Testing
@testable import MijiaCore

/// #3: the shared appliance card (#300) covers washers, dryers and dishwashers. The six fixtures
/// are verbatim released instances from miot-spec.org, captured with the #295 coverage corpus:
/// two washers, a dryer whose remaining time lives on a companion service, and two dishwashers
/// whose run-stage word is misspelled (`Rinsh`).
struct LaundryDeviceTests {
    static let washerModel = "bshcn.washer.0au00w"
    static let washerStageModel = "mibx5.washer.f27vn"
    static let dryerModel = "bshcn.dry.2j7x10"
    static let clothesDryerModel = "coc.dryer.fdpsm"
    static let dishwasherModel = "bshcn.dishwasher.23eb33"
    static let dishwasherAltModel = "bshcn.dishwasher.zb69mc"

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
        let record = MiDeviceRecord(did: did, name: displayName(model), model: model, isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [model: spec], values: values
        )
        let device = try #require(home.devices.first)
        return (device, spec, record)
    }

    /// A card takes the family name from the model the spec belongs to, so a failure message names
    /// the device the assertion is about.
    private func displayName(_ model: String) -> String {
        switch model {
        case Self.dryerModel, Self.clothesDryerModel: return L10n.tr("烘干机", "Dryer")
        case Self.dishwasherModel, Self.dishwasherAltModel: return L10n.tr("洗碗机", "Dishwasher")
        default: return L10n.tr("洗衣机", "Washer")
        }
    }

    private func appliance(_ device: SmartDevice) throws -> ApplianceSpec {
        guard case .appliance(let spec) = device.kind else {
            throw ApplianceExpectation.notAnAppliance(device.kind)
        }
        return spec
    }

    private enum ApplianceExpectation: Error {
        case notAnAppliance(DeviceKind)
    }

    @Test("A washer becomes an appliance card with its program list, status and remaining time")
    func washerCard() throws {
        let did = "w1"
        let (device, spec, record) = try synthesize(Self.washerModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .number(42),
            .init(did: did, siid: 2, piid: 17): .bool(true),
            .init(did: did, siid: 2, piid: 18): .number(1),
            .init(did: did, siid: 2, piid: 24): .number(1),
            .init(did: did, siid: 2, piid: 26): .number(0),
        ])
        let washer = try appliance(device)

        #expect(washer.entityId == "miot:w1:2")
        #expect(washer.powerPresent)
        #expect(washer.powerWritable)
        #expect(washer.isOn)
        #expect(washer.currentMode == "Daily Wash")
        #expect(washer.availableModes.count == 10)
        #expect(washer.availableModes.first == "Daily Wash")
        #expect(washer.modeWritable)
        #expect(!washer.temperatureWritable)
        #expect(washer.targetTemperature == nil)
        #expect(washer.statusText == L10n.tr("运行中", "Running"))
        #expect(washer.remainingMinutes == 42)
        #expect(washer.faultText == nil)
        #expect(washer.startAction?.entityId == "miot-action:w1:2:2")
        #expect(washer.pauseAction?.entityId == "miot-action:w1:2:3")
        #expect(device.isActiveOn)
        #expect(device.hasDetailDrawer)
        #expect(
            device.statusSubtitle
                == L10n.tr("Daily Wash · 运行中 · 剩余 42 分", "Daily Wash · Running · 42 min left")
        )

        let props = Set(MiotSynthesizer.requiredProps(for: record, spec: spec))
        #expect(props.isSuperset(of: [
            .init(did: did, siid: 2, piid: 1),
            .init(did: did, siid: 2, piid: 17),
            .init(did: did, siid: 2, piid: 18),
            .init(did: did, siid: 2, piid: 24),
            .init(did: did, siid: 2, piid: 26),
        ]))

        let start = try #require(washer.startAction?.entityId)
        let pause = try #require(washer.pauseAction?.entityId)
        #expect(try MiotCommandMapper.map(.setSwitch(entityId: washer.entityId, isOn: false), spec: spec)
            == .setProps([.init(did: did, siid: 2, piid: 17, value: .bool(false))]))
        #expect(try MiotCommandMapper.map(.setSelectOption(entityId: washer.entityId, option: "Quick Wash"), spec: spec)
            == .setProps([.init(did: did, siid: 2, piid: 18, value: .number(2))]))
        #expect(try MiotCommandMapper.map(.pressButton(entityId: start), spec: spec)
            == .action(.init(did: did, siid: 2, aiid: 2)))
        #expect(try MiotCommandMapper.map(.pressButton(entityId: pause), spec: spec)
            == .action(.init(did: did, siid: 2, aiid: 3)))
    }

    @Test("A washer reports the wash stage and exposes its full program list")
    func washerStageWord() throws {
        let did = "w2"
        let (device, _, _) = try synthesize(Self.washerStageModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .bool(true),
            .init(did: did, siid: 2, piid: 2): .number(3),
            .init(did: did, siid: 2, piid: 3): .number(4),
            .init(did: did, siid: 2, piid: 4): .number(0),
            .init(did: did, siid: 2, piid: 10): .number(42),
            .init(did: did, siid: 2, piid: 11): .number(6),
        ])
        let washer = try appliance(device)

        #expect(washer.statusText == L10n.tr("洗涤中", "Washing"))
        #expect(washer.remainingMinutes == 42)
        #expect(washer.availableModes.count == 25, "the whole program list is exposed, the drawer scrolls it")
        #expect(washer.currentMode == "Delicate Wash")
        #expect(device.statusSubtitle.contains(L10n.tr("洗涤中 · 剩余 42 分", "Washing · 42 min left")))
    }

    @Test("A paused washer stays paused while the run stage lingers")
    func pausedWasher() throws {
        let did = "w3"
        let (device, _, _) = try synthesize(Self.washerStageModel, did: did, values: [
            .init(did: did, siid: 2, piid: 2): .number(2),
            .init(did: did, siid: 2, piid: 11): .number(6),
        ])
        #expect(try appliance(device).statusText == L10n.tr("已暂停", "Paused"))
    }

    @Test("A dryer starts through its own start-drying action")
    func dryerCard() throws {
        let did = "d1"
        let (device, spec, _) = try synthesize(Self.dryerModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .bool(true),
            .init(did: did, siid: 2, piid: 2): .number(1),
            .init(did: did, siid: 2, piid: 3): .number(1),
            .init(did: did, siid: 2, piid: 5): .number(80),
        ])
        let dryer = try appliance(device)

        #expect(dryer.currentMode == "Mix")
        #expect(dryer.availableModes.count == 20)
        #expect(dryer.statusText == L10n.tr("运行中", "Running"))
        #expect(dryer.remainingMinutes == 80)
        #expect(dryer.startAction?.entityId == "miot-action:d1:2:1")
        #expect(dryer.pauseAction?.entityId == "miot-action:d1:2:2")

        let start = try #require(dryer.startAction?.entityId)
        #expect(try MiotCommandMapper.map(.pressButton(entityId: start), spec: spec)
            == .action(.init(did: did, siid: 2, aiid: 1)))
    }

    @Test("A clothes dryer without a power switch still shows its remaining time and fault")
    func clothesDryerCard() throws {
        let did = "c1"
        let running = try synthesize(Self.clothesDryerModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .number(0),
            .init(did: did, siid: 3, piid: 1): .number(120),
        ])
        let dryer = try appliance(running.device)

        #expect(dryer.remainingMinutes == 120, "the remaining time lives on the companion service")
        #expect(!dryer.powerPresent)
        #expect(!dryer.isOn)
        #expect(dryer.availableModes.isEmpty)
        #expect(dryer.statusText == nil)
        #expect(dryer.startAction == nil)
        #expect(dryer.pauseAction == nil)
        #expect(dryer.faultText == nil)
        #expect(running.device.hasDetailDrawer, "remaining time alone earns the drawer")

        let faulty = try synthesize(Self.clothesDryerModel, did: "c2", values: [
            .init(did: "c2", siid: 2, piid: 1): .number(2),
        ])
        #expect(try appliance(faulty.device).faultText == "2")
    }

    @Test("A dishwasher shows its wash stage, programme and remaining time")
    func dishwasherCard() throws {
        let did = "dw1"
        let (device, spec, _) = try synthesize(Self.dishwasherModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .bool(true),
            .init(did: did, siid: 2, piid: 2): .number(1),
            .init(did: did, siid: 2, piid: 3): .number(0),
            .init(did: did, siid: 2, piid: 4): .number(30),
            .init(did: did, siid: 2, piid: 5): .number(42),
            .init(did: did, siid: 2, piid: 15): .number(2),
        ])
        let dishwasher = try appliance(device)

        #expect(dishwasher.currentMode == "Daily Wash")
        #expect(dishwasher.availableModes == ["Smart", "Energy Saving", "Quick Wash", "Daily Wash", "Ultra Clean Washing"])
        #expect(dishwasher.statusText == L10n.tr("洗涤中", "Washing"))
        #expect(dishwasher.remainingMinutes == 42)
        #expect(dishwasher.startAction?.entityId == "miot-action:dw1:2:1")
        #expect(dishwasher.pauseAction?.entityId == "miot-action:dw1:2:2")
        #expect(
            device.statusSubtitle
                == L10n.tr("Daily Wash · 洗涤中 · 剩余 42 分", "Daily Wash · Washing · 42 min left")
        )

        // `reserve` has no inputs but is not a start, so the card never offers it.
        #expect(dishwasher.startAction?.entityId != "miot-action:dw1:2:5")
        let start = try #require(dishwasher.startAction?.entityId)
        #expect(try MiotCommandMapper.map(.pressButton(entityId: start), spec: spec)
            == .action(.init(did: did, siid: 2, aiid: 1)))
    }

    @Test("Dishwasher stage words tolerate the released spec's typos, and preservation wins over a stale stage")
    func dishwasherStageWords() throws {
        let washing = try synthesize(Self.dishwasherModel, did: "dw2", values: [
            .init(did: "dw2", siid: 2, piid: 2): .number(1),
            .init(did: "dw2", siid: 2, piid: 15): .number(3),
        ])
        #expect(try appliance(washing.device).statusText == L10n.tr("漂洗中", "Rinsing"), "`Rinsh` is the released spelling")

        let preserving = try synthesize(Self.dishwasherAltModel, did: "dw3", values: [
            .init(did: "dw3", siid: 2, piid: 2): .number(4),
            .init(did: "dw3", siid: 2, piid: 15): .number(4),
            .init(did: "dw3", siid: 2, piid: 3): .number(2),
        ])
        let preserved = try appliance(preserving.device)
        #expect(preserved.statusText == L10n.tr("保管中", "Keeping Fresh"))
        #expect(preserved.faultText == "AquaStopOccured")
    }

    @Test("Status words are classified by name, not by an exact spelling")
    func statusWordClassification() {
        #expect(ApplianceActivity("Busy") == .running)
        #expect(ApplianceActivity("Idle") == .standby)
        #expect(ApplianceActivity("Inactive") == .standby)
        #expect(ApplianceActivity("Off") == .standby)
        #expect(ApplianceActivity("Not Run") == .standby)
        #expect(ApplianceActivity("Ready") == .standby)
        #expect(ApplianceActivity("Paused") == .paused)
        #expect(ApplianceActivity("Aborting") == .aborting)
        #expect(ApplianceActivity("Delay") == .scheduled)
        #expect(ApplianceActivity("Performing Scheduled Task") == .scheduled)
        #expect(ApplianceActivity("Completed") == .completed)
        #expect(ApplianceActivity("Clean Cavity Completed") == .completed)
        #expect(ApplianceActivity("Washing Completed") == .completed)
        #expect(ApplianceActivity("Prewashing") == .prewash)
        #expect(ApplianceActivity("Washing") == .washing)
        #expect(ApplianceActivity("Rinsing") == .rinsing)
        #expect(ApplianceActivity("Rinsh") == .rinsing)
        #expect(ApplianceActivity("RINSE") == .rinsing)
        #expect(ApplianceActivity("Spinning") == .spinning)
        #expect(ApplianceActivity("Draining") == .draining)
        #expect(ApplianceActivity("Dry") == .drying)
        #expect(ApplianceActivity("Drying") == .drying)
        #expect(ApplianceActivity("Soaking") == .soaking)
        #expect(ApplianceActivity("Anti-crease Soaking") == .caring)
        #expect(ApplianceActivity("Blowing And Shaking") == .caring)
        #expect(ApplianceActivity("Steam Sterilization") == .sterilizing)
        #expect(ApplianceActivity("Weighing") == .weighing)
        #expect(ApplianceActivity("Preservation") == .preserving)
        #expect(ApplianceActivity("In Fault") == .fault)
        #expect(ApplianceActivity("Error") == .fault)
        #expect(ApplianceActivity("No Faults") == nil, "a healthy code is not a fault state")
        #expect(ApplianceActivity("洗涤中") == .washing)
        #expect(ApplianceActivity("排水中") == .draining)
        #expect(ApplianceActivity("故障") == .fault)
        #expect(ApplianceActivity("已暂停") == .paused)
        #expect(ApplianceActivity("") == nil)
        #expect(ApplianceActivity(nil) == nil)
        #expect(ApplianceActivity("Something Else") == nil)
    }

    @Test("Only the laundry families use the wash vocabulary")
    func activityVocabularyIsScoped() {
        #expect(MiotSynthesizer.activityServices == [
            "washer", "dryer", "clothes-dryer", "cleanfly-dryer", "dishwasher",
        ])
        #expect(MiotSynthesizer.applianceStatusText(service: "washer", status: "Washing", runStatus: nil)
            == L10n.tr("洗涤中", "Washing"))
        #expect(MiotSynthesizer.applianceStatusText(service: "washer", status: "Busy", runStatus: "Rinsh")
            == L10n.tr("漂洗中", "Rinsing"))
        // The water heater keeps its own words.
        #expect(MiotSynthesizer.applianceStatusText(service: "water-heater", status: "Wash", runStatus: nil)
            == L10n.tr("洗浴中", "Washing"))
        // A bath heater or hood keeps #4's mapping instead of being read as a wash phase.
        #expect(MiotSynthesizer.applianceStatusText(service: "hood", status: "Closed", runStatus: nil)
            == L10n.tr("待机", "Standby"))
        #expect(MiotSynthesizer.applianceStatusText(service: "hood", status: "Draining", runStatus: nil) == "Draining")
    }

    @Test("A drain stage reads as draining, and a fault word outranks the stage it left behind")
    func drainingAndFaultCard() throws {
        let spec = Self.wordSpec(
            status: [MiotValueListItem(value: 0, description: "Idle"),
                     MiotValueListItem(value: 1, description: "Busy"),
                     MiotValueListItem(value: 2, description: "In Fault")],
            runStatus: [MiotValueListItem(value: 1, description: "Draining"),
                        MiotValueListItem(value: 2, description: "Washing")]
        )
        let record = MiDeviceRecord(did: "drain", name: displayName(Self.dishwasherModel), model: "test.dishwasher.words", isOnline: true)

        func statusText(_ values: [MiotPropKey: JSONValue]) throws -> String? {
            let home = MiotSynthesizer.synthesize(
                devices: [record], rooms: [], specs: [record.model: spec], values: values
            )
            return try appliance(try #require(home.devices.first)).statusText
        }

        #expect(try statusText([.init(did: "drain", siid: 2, piid: 1): .number(1), .init(did: "drain", siid: 2, piid: 2): .number(1)])
            == L10n.tr("排水中", "Draining"))
        #expect(try statusText([.init(did: "drain", siid: 2, piid: 1): .number(2), .init(did: "drain", siid: 2, piid: 2): .number(2)])
            == L10n.tr("故障", "Fault"), "a fault outranks the wash stage it interrupted")
        #expect(try statusText([.init(did: "drain", siid: 2, piid: 1): .number(1), .init(did: "drain", siid: 2, piid: 2): .number(2)])
            == L10n.tr("洗涤中", "Washing"))
    }

    @Test("An unknown status word keeps the spec's own text instead of blanking the line")
    func unknownStatusKeepsTheWord() throws {
        let spec = Self.wordSpec(
            status: [MiotValueListItem(value: 1, description: "Bubble Time")],
            runStatus: nil
        )
        let record = MiDeviceRecord(did: "bubble", name: displayName(Self.washerModel), model: "test.washer.bubble", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [.init(did: "bubble", siid: 2, piid: 1): .number(1)]
        )
        let device = try #require(home.devices.first)
        #expect(try appliance(device).statusText == "Bubble Time")
        #expect(device.statusSubtitle.contains("Bubble Time"), "the card keeps a status line")
    }

    @Test("A remaining time in seconds or hours is shown in minutes")
    func remainingTimeUnits() throws {
        func remaining(_ unit: String?, value: Double) throws -> Int? {
            let spec = Self.leftTimeSpec(unit: unit)
            let record = MiDeviceRecord(did: "left", name: displayName(Self.washerModel), model: "test.washer.left", isOnline: true)
            let home = MiotSynthesizer.synthesize(
                devices: [record], rooms: [], specs: [record.model: spec],
                values: [.init(did: "left", siid: 2, piid: 1): .number(value)]
            )
            return try appliance(try #require(home.devices.first)).remainingMinutes
        }

        #expect(try remaining(nil, value: 42) == 42)
        #expect(try remaining("minutes", value: 42) == 42)
        #expect(try remaining("seconds", value: 90) == 2, "90 s rounds up to two minutes")
        #expect(try remaining("seconds", value: 60) == 1)
        #expect(try remaining("hours", value: 1) == 60)
        #expect(try remaining("minutes", value: 0) == nil)
    }

    @Test("A stop action stops the cycle directly and never shadows start")
    func stopAction() throws {
        let did = "dw-stop"
        let (device, spec, _) = try synthesize(Self.dishwasherModel, did: did, values: [
            .init(did: did, siid: 2, piid: 1): .bool(true),
            .init(did: did, siid: 2, piid: 2): .number(1),
            .init(did: did, siid: 2, piid: 15): .number(2),
        ])
        let dishwasher = try appliance(device)

        #expect(dishwasher.startAction?.entityId == "miot-action:dw-stop:2:1")
        #expect(dishwasher.pauseAction?.entityId == "miot-action:dw-stop:2:2")
        #expect(dishwasher.stopAction?.entityId == "miot-action:dw-stop:2:3")
        #expect(dishwasher.stopAction?.kind == .stop)
        #expect(device.hasDetailDrawer)

        let stop = try #require(dishwasher.stopAction?.entityId)
        #expect(try MiotCommandMapper.map(.pressButton(entityId: stop), spec: spec)
            == .action(.init(did: did, siid: 2, aiid: 3)))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: device))
        #expect(ApplianceActionKind.matching("stop-washing") == .stop)
        #expect(ApplianceActionKind.matching("stop-dry") == .stop)
    }

    @Test("The appliance card's strings are translated into Traditional Chinese")
    func traditionalChineseCardStrings() {
        L10n.$overrideLanguage.withValue(.zhHant) {
            #expect(L10n.tr("状态", "Status") == "狀態")
            #expect(L10n.tr("剩余时间", "Remaining") == "剩餘時間")
            #expect(L10n.tr("开始", "Start") == "開始")
            #expect(L10n.tr("剩余 42 分", "42 min left") == "剩餘 42 分")
            #expect(L10n.tr("42 分钟", "42 min") == "42 分鐘")
            #expect(L10n.tr("目标温度", "Target Temp") == "目標溫度")
            #expect(L10n.format("当前 %@", "Now %@", "52.0°C") == "目前 52.0°C")
            #expect(L10n.format("目标 %@", "Target %@", "60.0°C") == "目標 60.0°C")
        }
    }

    private static func wordSpec(
        status: [MiotValueListItem],
        runStatus: [MiotValueListItem]?
    ) -> MiotDeviceSpec {
        var properties = [
            MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:status:00000007:words:1", format: "uint8", access: ["read"], valueList: status),
        ]
        if let runStatus {
            properties.append(MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:run-status:0000011E:words:1", format: "uint8", access: ["read"], valueList: runStatus))
        }
        return MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:dishwasher:0000A034:words:1",
            description: "Dishwasher",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:dishwasher:00007835:words:1",
                    properties: properties
                )
            ]
        )
    }

    private static func leftTimeSpec(unit: String?) -> MiotDeviceSpec {
        MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:washer:0000A01F:left-time:1",
            description: "Washer",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:washer:00007834:left-time:1",
                    properties: [
                        MiotProperty(
                            iid: 1,
                            type: "urn:miot-spec-v2:property:left-time:0000003C:left-time:1",
                            format: "uint32",
                            access: ["read"],
                            unit: unit
                        ),
                    ]
                )
            ]
        )
    }

    @Test("Actions that take arguments are ignored instead of failing the whole card")
    func actionsWithArgumentsAreIgnored() throws {
        let spec = MiotDeviceSpec(
            type: "urn:miot-spec-v2:device:washer:0000A01F:with-args:1",
            description: "Washer",
            services: [
                MiotService(
                    iid: 2,
                    type: "urn:miot-spec-v2:service:washer:00007834:with-args:1",
                    properties: [
                        MiotProperty(iid: 1, type: "urn:miot-spec-v2:property:on:00000006:with-args:1", format: "bool", access: ["read", "write"]),
                        MiotProperty(iid: 2, type: "urn:miot-spec-v2:property:status:00000007:with-args:1", format: "uint8", access: ["read"], valueList: [
                            MiotValueListItem(value: 1, description: "Busy")
                        ]),
                        MiotProperty(iid: 3, type: "urn:miot-spec-v2:property:mode:00000008:with-args:1", format: "uint8", access: ["read", "write"], valueList: [
                            MiotValueListItem(value: 1, description: "Daily Wash")
                        ]),
                        MiotProperty(iid: 4, type: "urn:miot-spec-v2:property:left-time:0000003C:with-args:1", format: "uint16", access: ["read"]),
                    ],
                    actions: [
                        MiotAction(iid: 1, type: "urn:miot-spec-v2:action:start-wash:00002824:with-args:1", inputProperties: [3]),
                        MiotAction(iid: 2, type: "urn:miot-spec-v2:action:pause:0000280C:with-args:1", inputProperties: [4]),
                        MiotAction(iid: 3, type: "urn:miot-spec-v2:action:reserve:00002825:with-args:1", inputProperties: [4, 3]),
                    ]
                )
            ]
        )
        let record = MiDeviceRecord(did: "args", name: displayName(Self.washerModel), model: "test.washer.args", isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [
                .init(did: "args", siid: 2, piid: 1): .bool(true),
                .init(did: "args", siid: 2, piid: 2): .number(1),
                .init(did: "args", siid: 2, piid: 3): .number(1),
                .init(did: "args", siid: 2, piid: 4): .number(30),
            ]
        )
        let device = try #require(home.devices.first)
        let washer = try appliance(device)

        #expect(washer.currentMode == "Daily Wash")
        #expect(washer.statusText == L10n.tr("运行中", "Running"))
        #expect(washer.remainingMinutes == 30)
        #expect(washer.startAction == nil, "an action with arguments is not offered")
        #expect(washer.pauseAction == nil)
        #expect(throws: MiotCommandError.self) {
            _ = try MiotCommandMapper.map(.pressButton(entityId: "miot-action:args:2:1"), spec: spec)
        }
    }

    @Test("Starting a wash asks first; pausing it runs directly")
    @MainActor
    func startAsksPauseIsDirect() async throws {
        let spec = try spec(Self.washerStageModel)
        let record = MiDeviceRecord(did: "ask", name: displayName(Self.washerStageModel), model: Self.washerStageModel, isOnline: true)
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [record.model: spec],
            values: [
                .init(did: "ask", siid: 2, piid: 1): .bool(false),
                .init(did: "ask", siid: 2, piid: 2): .number(1),
                .init(did: "ask", siid: 2, piid: 3): .number(1),
            ]
        )
        let device = try #require(home.devices.first)
        let washer = try appliance(device)
        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: device))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: device))

        let start = try #require(washer.startAction)
        let pause = try #require(washer.pauseAction)
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [device]))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.laundry.\(UUID().uuidString)")
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

        let stop = try #require(washer.stopAction)
        store.runApplianceAction(device.id, action: stop)
        await waitUntil { await mock.executedCommands.count == 3 }
        #expect(prompts == 2, "stopping is de-escalating")
        #expect(await mock.executedCommands.last == .pressButton(entityId: stop.entityId))
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
