import Foundation
import Testing
@testable import MijiaCore

/// GLOSSARY `BareCategoryCommand`: 「关灯」 looks only at the same-category devices that are on.
struct BareCategoryCommandTests {
    static func light(_ id: String, _ name: String, room: String, isOn: Bool) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: room, roomName: room,
            model: "yeelink.light.lamp4", isOnline: true,
            kind: .light(LightSpec(entityId: "light.\(id)", isOn: isOn))
        )
    }

    static func climate(_ id: String, _ name: String, room: String, isOn: Bool) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: room, roomName: room,
            model: "xiaomi.aircondition.mt0", isOnline: true,
            kind: .climate(ClimateSpec(
                entityId: "climate.\(id)",
                isOn: isOn,
                hvacMode: isOn ? "cool" : "off",
                availableModes: ["off", "cool", "heat"],
                targetTemperature: 24
            ))
        )
    }

    static let studyCeiling = light("dev_study_ceiling", "书房吸顶灯", room: "书房", isOn: true)
    static let studyDesk = light("dev_study_desk", "书房台灯", room: "书房", isOn: true)
    static let bedroomLamp = light("dev_bedroom_lamp", "主卧床头灯", room: "主卧", isOn: true)
    static let kitchenLight = light("dev_kitchen_light", "厨房灯", room: "厨房", isOn: false)
    static let livingAC = climate("dev_living_ac", "客厅中央空调", room: "客厅", isOn: true)
    static let bedroomAC = climate("dev_bedroom_ac", "主卧挂机空调", room: "主卧", isOn: false)
    static let livingFan = SmartDevice(
        id: "dev_living_fan", name: "客厅循环扇", roomId: "客厅", roomName: "客厅",
        model: "dmaker.fan.p5", isOnline: true,
        kind: .fan(FanSpec(
            entityId: "fan.living", isOn: true, presetMode: "Level1",
            availablePresetModes: ["Level1", "Level2", "Level3"], isOscillating: false
        ))
    )
    static let purifier = SmartDevice(
        id: "dev_living_purifier", name: "客厅空净机", roomId: "客厅", roomName: "客厅",
        model: "zhimi.airp.rma3", isOnline: true,
        kind: .fan(FanSpec(entityId: "fan.purifier", isOn: true, availablePresetModes: ["Auto"]))
    )

    static func fan(_ id: String, _ name: String, room: String, isOn: Bool) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: room, roomName: room,
            model: "dmaker.fan.p5", isOnline: true,
            kind: .fan(FanSpec(
                entityId: "fan.\(id)", isOn: isOn, presetMode: "Level1",
                availablePresetModes: ["Level1", "Level2", "Level3"], isOscillating: false
            ))
        )
    }

    static let devices = [
        studyCeiling, studyDesk, bedroomLamp, kitchenLight,
        livingAC, bedroomAC, livingFan, purifier
    ]

    static func parse(_ text: String, panelRoomName: String? = nil, usageOrder: [String] = []) -> IntentPlan? {
        LocalIntentParser.parse(
            text,
            devices: devices,
            scenes: [],
            // These fixtures use the room name as the room id.
            panelRoom: panelRoomName.map { BareCategoryCommand.Room(id: $0, name: $0) },
            usageOrder: usageOrder
        )
    }

    @Test("关灯 with several lights on offers the on lights plus one 全部关掉")
    func severalLightsOnOffersCandidates() throws {
        let plan = try #require(Self.parse("关灯"))
        #expect(plan.source == .local)
        #expect(plan.intents.isEmpty)
        #expect(plan.reply == "开着：书房吸顶灯、书房台灯、主卧床头灯。请说出房间或设备名")
        #expect(plan.suggestions.map(\.title) == [
            "关闭书房吸顶灯",
            "关闭书房台灯",
            "关闭主卧床头灯",
            "全部关掉"
        ])
        #expect(plan.suggestions.last?.intents == [
            DeviceIntent(targetId: "dev_study_ceiling", operation: .power(false)),
            DeviceIntent(targetId: "dev_study_desk", operation: .power(false)),
            DeviceIntent(targetId: "dev_bedroom_lamp", operation: .power(false))
        ])
    }

    @Test("关灯 with only one light on closes that light instead of the dark ones")
    func oneLightOnRunsDirectly() throws {
        let onlyBedroomOn = LocalIntentParser.parse(
            "关灯",
            devices: [Self.studyCeiling.off(), Self.studyDesk.off(), Self.bedroomLamp, Self.kitchenLight],
            scenes: []
        )
        let resolved = try #require(onlyBedroomOn)
        #expect(resolved.intents == [DeviceIntent(targetId: "dev_bedroom_lamp", operation: .power(false))])
        #expect(resolved.reply == "已关闭主卧床头灯")
    }

    @Test("关灯 with every light off answers instead of sending a command")
    func nothingOnAnswers() throws {
        let allOff = [Self.studyCeiling.off(), Self.studyDesk.off(), Self.bedroomLamp.off(), Self.kitchenLight]
        let plan = try #require(LocalIntentParser.parse("关灯", devices: allOff, scenes: []))
        #expect(plan.intents.isEmpty)
        #expect(plan.suggestions.isEmpty)
        #expect(plan.reply == "灯都关着")
    }

    @Test("关灯 follows the room the panel shows, and says so when that room is dark")
    func panelRoomDecides() throws {
        let study = try #require(Self.parse("关灯", panelRoomName: "书房"))
        #expect(study.intents == [
            DeviceIntent(targetId: "dev_study_ceiling", operation: .power(false)),
            DeviceIntent(targetId: "dev_study_desk", operation: .power(false))
        ])
        #expect(study.suggestions.isEmpty)

        let kitchen = try #require(Self.parse("关灯", panelRoomName: "厨房"))
        #expect(kitchen.intents.isEmpty)
        #expect(kitchen.suggestions.isEmpty)
        #expect(kitchen.reply == "厨房的灯都关着")
    }

    @Test("Candidates cap at four devices and one 全部关掉 closes all of them")
    func candidatesCapAtFour() throws {
        let extras = (1...3).map { index in
            Self.light("dev_extra_\(index)", "额外灯\(index)", room: "房间\(index)", isOn: true)
        }
        let manyOn = [Self.studyCeiling, Self.studyDesk, Self.bedroomLamp] + extras
        let plan = try #require(LocalIntentParser.parse("关灯", devices: manyOn, scenes: []))
        #expect(plan.suggestions.count == 5)
        #expect(plan.suggestions.dropLast().allSatisfy { $0.intents.count == 1 })
        #expect(plan.suggestions.last?.intents.count == 6)
    }

    @Test("Candidate order follows the habit order, and device order otherwise")
    func candidatesFollowHabitOrder() throws {
        let plan = try #require(Self.parse("关灯", usageOrder: ["dev_bedroom_lamp", "dev_study_desk"]))
        #expect(plan.suggestions.map(\.title) == [
            "关闭主卧床头灯",
            "关闭书房台灯",
            "关闭书房吸顶灯",
            "全部关掉"
        ])
    }

    @Test("关空调 and 关风扇 follow the same rule for their own category")
    func otherCategoriesFollowTheSameRule() throws {
        let ac = try #require(Self.parse("关空调"))
        #expect(ac.intents == [DeviceIntent(targetId: "dev_living_ac", operation: .power(false))])

        let fan = try #require(Self.parse("关风扇"))
        #expect(fan.intents == [DeviceIntent(targetId: "dev_living_fan", operation: .power(false))])

        let purifier = try #require(Self.parse("关空气净化器"))
        #expect(purifier.intents == [DeviceIntent(targetId: "dev_living_purifier", operation: .power(false))])
    }

    @Test("Several running air conditioners offer candidates like lights")
    func otherCategoryCandidates() throws {
        let secondAC = Self.climate("dev_bedroom_ac_on", "次卧壁挂机", room: "次卧", isOn: true)
        let plan = try #require(LocalIntentParser.parse(
            "关空调",
            devices: [Self.livingAC, Self.bedroomAC, secondAC],
            scenes: []
        ))
        #expect(plan.intents.isEmpty)
        #expect(plan.suggestions.map(\.title) == [
            "关闭客厅中央空调",
            "关闭次卧壁挂机",
            "全部关掉"
        ])
        #expect(plan.suggestions.last?.intents.count == 2)
    }

    @Test("A category word that is also a room-stripped device name still offers candidates")
    func categoryWordCollidesWithDeviceNames() throws {
        let livingAC = Self.climate("dev_living_ac", "客厅空调", room: "客厅", isOn: true)
        let bedroomAC = Self.climate("dev_bedroom_ac", "主卧空调", room: "主卧", isOn: true)
        let ac = try #require(LocalIntentParser.parse("关空调", devices: [livingAC, bedroomAC], scenes: []))
        #expect(ac.intents.isEmpty)
        #expect(ac.suggestions.map(\.title) == ["关闭客厅空调", "关闭主卧空调", "全部关掉"])
        #expect(ac.reply == "开着：客厅空调、主卧空调。请说出房间或设备名")

        let livingFan = Self.fan("dev_living_fan", "客厅风扇", room: "客厅", isOn: true)
        let bedroomFan = Self.fan("dev_bedroom_fan", "主卧风扇", room: "主卧", isOn: true)
        let fan = try #require(LocalIntentParser.parse("关风扇", devices: [livingFan, bedroomFan], scenes: []))
        #expect(fan.intents.isEmpty)
        #expect(fan.suggestions.map(\.title) == ["关闭客厅风扇", "关闭主卧风扇", "全部关掉"])
    }

    @Test("关空调 ignores an off device whose stripped name is the category word")
    func offDeviceSharingCategoryWordIsIgnored() throws {
        let offAC = Self.climate("dev_living_ac", "客厅空调", room: "客厅", isOn: false)
        let onAC = Self.climate("dev_bedroom_ac", "主卧挂机", room: "主卧", isOn: true)
        let plan = try #require(LocalIntentParser.parse("关空调", devices: [offAC, onAC], scenes: []))
        #expect(plan.intents == [DeviceIntent(targetId: "dev_bedroom_ac", operation: .power(false))])
    }

    @Test("An on device whose power is read-only is reported, not called off")
    func readOnlyActiveDeviceIsReported() throws {
        let camera = SmartDevice(
            id: "dev_camera", name: "客厅摄像机", roomId: "living", roomName: "客厅",
            model: "chuangmi.camera.81ac1", isOnline: true,
            kind: .camera(CameraSpec(
                entityId: "miot:dev_camera:2",
                isOn: true,
                availableNightShots: ["Off", "On", "Auto"],
                powerWritable: false
            ))
        )
        let plan = try #require(LocalIntentParser.parse("关监控", devices: [camera], scenes: []))
        #expect(plan.intents.isEmpty)
        #expect(plan.suggestions.isEmpty)
        #expect(plan.reply == "摄像机开着，但无法关闭")

        // A writable camera still closes normally.
        let writable = SmartDevice(
            id: "dev_camera_2", name: "书房摄像机", roomId: "study", roomName: "书房",
            model: "chuangmi.camera.81ac1", isOnline: true,
            kind: .camera(CameraSpec(entityId: "miot:dev_camera_2:2", isOn: true))
        )
        let closes = try #require(LocalIntentParser.parse("关监控", devices: [writable], scenes: []))
        #expect(closes.intents == [DeviceIntent(targetId: "dev_camera_2", operation: .power(false))])
    }

    @Test("English bare-category closes use the same room rule")
    func englishUsesTheSameRule() throws {
        let plan = try #require(Self.parse("turn off the lights"))
        #expect(plan.intents.isEmpty)
        #expect(plan.suggestions.count == 4)

        let study = try #require(Self.parse("turn off the lights", panelRoomName: "书房"))
        #expect(study.intents.map(\.targetId) == ["dev_study_ceiling", "dev_study_desk"])
    }

    @Test("Scope words keep their existing behavior")
    func scopeUnchanged() throws {
        let scoped = try #require(Self.parse("关掉所有的灯"))
        #expect(scoped.intents.map(\.targetId) == [
            "dev_study_ceiling",
            "dev_study_desk",
            "dev_bedroom_lamp",
            "dev_kitchen_light"
        ])

        let every = try #require(Self.parse("全部关灯"))
        #expect(every.intents.count == 4)

        let everyOn = try #require(Self.parse("打开所有的灯"))
        #expect(everyOn.intents.count == 4)
        #expect(everyOn.intents.allSatisfy { $0.operation == .power(true) })
    }

    // MARK: - Opening (#274)

    static let darkLights = [
        studyCeiling.off(), studyDesk.off(), bedroomLamp.off(), kitchenLight
    ]

    static func parse(_ text: String, devices: [SmartDevice], panelRoomName: String? = nil) -> IntentPlan? {
        LocalIntentParser.parse(
            text,
            devices: devices,
            scenes: [],
            panelRoom: panelRoomName.map { BareCategoryCommand.Room(id: $0, name: $0) }
        )
    }

    @Test("开灯 with several lights off offers three candidates plus one 全部打开")
    func severalLightsOffOffersCandidates() throws {
        let plan = try #require(Self.parse("开灯", devices: Self.darkLights))
        #expect(plan.source == .local)
        #expect(plan.intents.isEmpty)
        #expect(plan.reply == "关着：书房吸顶灯、书房台灯、主卧床头灯、厨房灯。请说出房间或设备名")
        #expect(plan.suggestions.map(\.title) == [
            "打开书房吸顶灯",
            "打开书房台灯",
            "打开主卧床头灯",
            "全部打开"
        ])
        #expect(plan.suggestions.last?.intents == [
            DeviceIntent(targetId: "dev_study_ceiling", operation: .power(true)),
            DeviceIntent(targetId: "dev_study_desk", operation: .power(true)),
            DeviceIntent(targetId: "dev_bedroom_lamp", operation: .power(true)),
            DeviceIntent(targetId: "dev_kitchen_light", operation: .power(true))
        ])
    }

    @Test("开灯 opens the room the panel shows and answers when that room is already lit")
    func panelRoomDecidesOpen() throws {
        // The study is already lit; two lights elsewhere are off.
        let mixed = [Self.studyCeiling, Self.studyDesk, Self.bedroomLamp.off(), Self.kitchenLight]
        let study = try #require(Self.parse("开灯", devices: mixed, panelRoomName: "书房"))
        #expect(study.intents.isEmpty)
        #expect(study.reply == "书房的灯都开着")

        let kitchen = try #require(Self.parse("开灯", devices: mixed, panelRoomName: "厨房"))
        #expect(kitchen.intents == [
            DeviceIntent(targetId: "dev_kitchen_light", operation: .power(true))
        ])

        let both = try #require(Self.parse("开灯", devices: Self.darkLights, panelRoomName: "书房"))
        #expect(both.intents.map(\.targetId) == ["dev_study_ceiling", "dev_study_desk"])
        #expect(both.suggestions.isEmpty)
    }

    @Test("开灯 with one same-category device runs it directly")
    func oneDeviceOpensDirectly() throws {
        let plan = try #require(Self.parse("开灯", devices: [Self.studyCeiling.off()]))
        #expect(plan.intents == [
            DeviceIntent(targetId: "dev_study_ceiling", operation: .power(true))
        ])
        #expect(plan.reply == "已打开书房吸顶灯")
        #expect(plan.suggestions.isEmpty)
    }

    @Test("开灯 with every light already on answers instead of touching it")
    func everythingOnAnswers() throws {
        let plan = try #require(Self.parse(
            "开灯",
            devices: [Self.studyCeiling, Self.studyDesk, Self.bedroomLamp]
        ))
        #expect(plan.intents.isEmpty)
        #expect(plan.suggestions.isEmpty)
        #expect(plan.reply == "灯都开着")
    }

    @Test("Open candidate order follows the habit order")
    func openCandidatesFollowHabitOrder() throws {
        let plan = try #require(LocalIntentParser.parse(
            "开灯",
            devices: Self.darkLights,
            scenes: [],
            usageOrder: ["dev_bedroom_lamp", "dev_kitchen_light"]
        ))
        #expect(plan.suggestions.map(\.title) == [
            "打开主卧床头灯",
            "打开厨房灯",
            "打开书房吸顶灯",
            "全部打开"
        ])
        #expect(plan.suggestions.last?.intents.count == 4)
    }

    @Test("English bare-category opens use the same room and candidate rule")
    func englishOpensUseTheSameRule() throws {
        let candidates = try #require(Self.parse("turn on the lights", devices: Self.darkLights))
        #expect(candidates.intents.isEmpty)
        #expect(candidates.suggestions.map(\.title) == [
            "打开书房吸顶灯",
            "打开书房台灯",
            "打开主卧床头灯",
            "全部打开"
        ])

        let study = try #require(Self.parse(
            "turn on the lights",
            devices: Self.darkLights,
            panelRoomName: "书房"
        ))
        #expect(study.intents.map(\.targetId) == ["dev_study_ceiling", "dev_study_desk"])
    }

    @Test("High-risk devices are offered as open candidates")
    func hazardousDevicesAreCandidates() throws {
        let rooms = [("dev_plug_study", "书房插座", "study", "书房"),
                     ("dev_plug_bedroom", "主卧插座", "bedroom", "主卧"),
                     ("dev_plug_living", "客厅插座", "living", "客厅")]
        let plugs = rooms.map { id, name, roomId, roomName in
            SmartDevice(
                id: id, name: name, roomId: roomId, roomName: roomName,
                model: "cuco.plug.v3", isOnline: true,
                kind: .plug(PlugSpec(switchEntityId: "switch.\(id)", isOn: false))
            )
        }
        let plan = try #require(LocalIntentParser.parse("打开插座", devices: plugs, scenes: []))
        #expect(plan.intents.isEmpty)
        #expect(plan.suggestions.map(\.title) == [
            "打开书房插座",
            "打开主卧插座",
            "打开客厅插座",
            "全部打开"
        ])
        let candidate = try #require(plan.suggestions.first?.intents.first)
        #expect(ModelActionPolicy.needsConfirmation(candidate, devices: plugs))
    }
}

private extension SmartDevice {
    func off() -> SmartDevice {
        var copy = self
        if case .light(var spec) = copy.kind {
            spec.isOn = false
            copy.kind = .light(spec)
        }
        return copy
    }
}
