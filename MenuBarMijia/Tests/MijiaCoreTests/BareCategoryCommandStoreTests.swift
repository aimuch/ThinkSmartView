import Foundation
import Testing
@testable import MijiaCore

/// GLOSSARY `BareCategoryCommand` through the store: chips, the all-off choice, undo, and the room
/// the panel shows.
@MainActor
struct BareCategoryCommandStoreTests {
    static func light(_ id: String, _ name: String, roomId: String, roomName: String, isOn: Bool) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: roomId, roomName: roomName,
            model: "yeelink.light.lamp4", isOnline: true,
            kind: .light(LightSpec(entityId: "light.\(id)", isOn: isOn))
        )
    }

    static let rooms = [
        RoomArea(id: "study", name: "书房", sortOrder: 0),
        RoomArea(id: "bedroom", name: "主卧", sortOrder: 1),
        RoomArea(id: "kitchen", name: "厨房", sortOrder: 2)
    ]
    static let studyCeiling = light("dev_study_ceiling", "书房吸顶灯", roomId: "study", roomName: "书房", isOn: true)
    static let studyDesk = light("dev_study_desk", "书房台灯", roomId: "study", roomName: "书房", isOn: true)
    static let bedroomLamp = light("dev_bedroom_lamp", "主卧床头灯", roomId: "bedroom", roomName: "主卧", isOn: true)
    static let kitchenLight = light("dev_kitchen_light", "厨房灯", roomId: "kitchen", roomName: "厨房", isOn: false)

    static func makeStore(
        _ devices: [SmartDevice] = [studyCeiling, studyDesk, bedroomLamp, kitchenLight]
    ) async throws -> (HomeStore, MockHomeProvider) {
        try await makeStore(devices, rooms: rooms)
    }

    static func makeStore(
        _ devices: [SmartDevice],
        rooms: [RoomArea]
    ) async throws -> (HomeStore, MockHomeProvider) {
        let mock = MockHomeProvider(snapshot: SynthesizedHome(rooms: rooms, devices: devices))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.bare.\(UUID().uuidString)")
        try await store.waitForTestSnapshot()
        return (store, mock)
    }

    @Test("关灯 with several lights on asks with chips and runs nothing")
    func severalOnAsks() async throws {
        let (store, mock) = try await Self.makeStore()
        let plan = try #require(await store.runAssistantCommand("关灯"))

        #expect(plan.intents.isEmpty)
        #expect(store.assistantState == .awaitingChoice("开着：书房吸顶灯、书房台灯、主卧床头灯。请说出房间或设备名"))
        #expect(store.assistantSuggestions.map(\.title) == [
            "关闭书房吸顶灯",
            "关闭书房台灯",
            "关闭主卧床头灯",
            "全部关掉"
        ])
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.scheduledCommands.isEmpty)
        #expect(store.devices.filter { $0.isActiveOn }.count == 3)
    }

    @Test("全部关掉 runs every lit light, offers undo, and undo restores them")
    func allOffChoiceRunsAndUndoes() async throws {
        let (store, mock) = try await Self.makeStore()
        _ = await store.runAssistantCommand("关灯")
        let allOff = try #require(store.assistantSuggestions.last)
        #expect(allOff.title == "全部关掉")

        await store.applyAssistantSuggestion(allOff)
        let executed = await mock.executedCommands
        #expect(executed.count == 3)
        #expect(store.devices.filter { $0.isActiveOn }.isEmpty)
        #expect(store.undoBanner?.restoreCommands.count == 3)

        let undo = try #require(store.undoTurnAllOff())
        await undo.value
        #expect(store.devices.filter { $0.isActiveOn }.map(\.id) == [
            "dev_study_ceiling",
            "dev_study_desk",
            "dev_bedroom_lamp"
        ])
        let restored = await mock.executedCommands
        #expect(restored.dropFirst(3).allSatisfy { command in
            if case .setLightPower(_, let on) = command { return on }
            return false
        })
    }

    @Test("关灯 with every light off answers instead of touching a dark light")
    func nothingOnAnswers() async throws {
        let allOff = [
            Self.studyCeiling.off(), Self.studyDesk.off(),
            Self.bedroomLamp.off(), Self.kitchenLight
        ]
        let (store, mock) = try await Self.makeStore(allOff)
        let plan = try #require(await store.runAssistantCommand("关灯"))

        #expect(plan.intents.isEmpty)
        #expect(store.assistantState == .done("灯都关着"))
        #expect(store.assistantSuggestions.contains { $0.id.hasPrefix("bare:off") } == false)
        #expect(await mock.executedCommands.isEmpty)
    }

    @Test("A room the panel shows decides which lit lights a bare 关灯 closes")
    func panelRoomDecides() async throws {
        let (store, mock) = try await Self.makeStore()
        #expect(store.selectRoom("study"))
        #expect(store.panelRoomName == "书房")

        let plan = try #require(await store.runAssistantCommand("关灯"))
        #expect(plan.intents.map(\.targetId) == ["dev_study_ceiling", "dev_study_desk"])
        let executed = await mock.executedCommands
        #expect(executed.count == 2)
        #expect(store.devices.first { $0.id == "dev_bedroom_lamp" }?.isActiveOn == true)
        #expect(store.undoBanner?.restoreCommands.count == 2)
    }

    @Test("A delayed bare 关灯 that needs a choice sets no timer, runs nothing, and asks for a room")
    func delayedAmbiguitySchedulesNothing() async throws {
        let (store, mock) = try await Self.makeStore()
        let plan = try #require(await store.runAssistantCommand("10分钟后关灯"))
        #expect(plan.intents.isEmpty)
        #expect(store.scheduledCommands.isEmpty)
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.assistantSuggestions.contains { $0.id == "bare:off:all" } == false)
        guard case .done(let reply) = store.assistantState else {
            Issue.record("expected a done answer, got \(store.assistantState)")
            return
        }
        #expect(reply == "开着：书房吸顶灯、书房台灯、主卧床头灯。请说出房间或设备名")
    }

    @Test("An answer with no lights on replaces the previous bare 关灯 chips")
    func answerClearsStaleChips() async throws {
        let (store, mock) = try await Self.makeStore()
        _ = await store.runAssistantCommand("关灯")
        #expect(store.assistantSuggestions.contains { $0.id == "bare:off:all" })

        let allOff = [
            Self.studyCeiling.off(), Self.studyDesk.off(),
            Self.bedroomLamp.off(), Self.kitchenLight
        ]
        await mock.updateSnapshot(SynthesizedHome(rooms: Self.rooms, devices: allOff))
        for _ in 0..<50 where store.devices.contains(where: \.isActiveOn) {
            try await Task.sleep(for: .milliseconds(10))
        }
        _ = await store.runAssistantCommand("关灯")
        #expect(store.assistantState == .done("灯都关着"))
        #expect(store.assistantSuggestions.contains { $0.id.hasPrefix("bare:off") } == false)
    }

    @Test("关灯 follows the panel room id, not a shared room name across accounts")
    func panelRoomUsesRoomId() async throws {
        let cnCeiling = Self.light(
            "dev_cn_ceiling", "客厅吸顶灯", roomId: "account-cn/ke_ting", roomName: "客厅", isOn: true
        )
        let deCeiling = Self.light(
            "dev_de_ceiling", "客厅吊灯", roomId: "account-de/ke_ting", roomName: "客厅", isOn: true
        )
        let (store, mock) = try await Self.makeStore(
            [cnCeiling, deCeiling],
            rooms: [
                RoomArea(id: "account-cn/ke_ting", name: "客厅", sortOrder: 0),
                RoomArea(id: "account-de/ke_ting", name: "客厅", sortOrder: 1)
            ]
        )
        #expect(store.selectRoom("account-cn/ke_ting"))
        #expect(store.panelRoom == BareCategoryCommand.Room(id: "account-cn/ke_ting", name: "客厅"))

        let plan = try #require(await store.runAssistantCommand("关灯"))
        #expect(plan.intents.map(\.targetId) == ["dev_cn_ceiling"])
        let executed = await mock.executedCommands
        #expect(executed == [.setLightPower(entityId: "light.dev_cn_ceiling", isOn: false)])
        #expect(store.devices.first { $0.id == "dev_de_ceiling" }?.isActiveOn == true)
    }

    @Test("A delayed bare 关灯 resolves against the panel room when the timer is set")
    func delayedCommandResolvesWhenScheduled() async throws {
        let (store, mock) = try await Self.makeStore()
        #expect(store.selectRoom("study"))
        let gate = AsyncStream.makeStream(of: Void.self)
        store.assistantSleep = { _ in
            var iterator = gate.stream.makeAsyncIterator()
            _ = await iterator.next()
        }

        let plan = try #require(await store.runAssistantCommand("10分钟后关灯"))
        #expect(plan.intents.map(\.targetId) == ["dev_study_ceiling", "dev_study_desk"])
        #expect(store.scheduledCommands.count == 1)
        // The timer is parked inside assistantSleep, so nothing can run before the test releases it.
        #expect(await mock.executedCommands.isEmpty)

        store.selectRoom(nil)
        gate.continuation.finish()
        for _ in 0..<50 where !store.scheduledCommands.isEmpty {
            try await Task.sleep(for: .milliseconds(20))
        }
        #expect(store.scheduledCommands.isEmpty)
        let executed = await mock.executedCommands
        #expect(executed.count == 2)
        #expect(store.devices.first { $0.id == "dev_bedroom_lamp" }?.isActiveOn == true)
    }

    // MARK: - Opening (#274)

    static func plug(
        _ id: String,
        _ name: String,
        roomId: String,
        roomName: String,
        isOn: Bool = false
    ) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: roomId, roomName: roomName,
            model: "cuco.plug.v3", isOnline: true,
            kind: .plug(PlugSpec(switchEntityId: "switch.\(id)", isOn: isOn))
        )
    }

    static func heater(_ id: String, _ name: String, roomId: String, roomName: String) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: roomId, roomName: roomName,
            model: "zhimi.heater.za2", isOnline: true,
            kind: .climate(ClimateSpec(
                entityId: "climate.\(id)",
                isOn: false,
                hvacMode: "off",
                availableModes: ["off", "heat"],
                targetTemperature: 20
            ))
        )
    }

    @Test("开灯 with several lights off asks with chips and runs nothing")
    func openSeveralOffAsks() async throws {
        let (store, mock) = try await Self.makeStore([
            Self.studyCeiling.off(), Self.studyDesk.off(), Self.bedroomLamp, Self.kitchenLight
        ])
        let plan = try #require(await store.runAssistantCommand("开灯"))

        #expect(plan.intents.isEmpty)
        #expect(store.assistantState == .awaitingChoice("关着：书房吸顶灯、书房台灯、厨房灯。请说出房间或设备名"))
        #expect(store.assistantSuggestions.map(\.title) == [
            "打开书房吸顶灯",
            "打开书房台灯",
            "打开厨房灯",
            "全部打开"
        ])
        #expect(await mock.executedCommands.isEmpty)
    }

    @Test("全部打开 opens every dark light, offers undo, and undo restores them")
    func allOnChoiceRunsAndUndoes() async throws {
        let (store, mock) = try await Self.makeStore([
            Self.studyCeiling.off(), Self.studyDesk.off(), Self.bedroomLamp, Self.kitchenLight
        ])
        _ = await store.runAssistantCommand("开灯")
        let allOn = try #require(store.assistantSuggestions.last)
        #expect(allOn.title == "全部打开")

        await store.applyAssistantSuggestion(allOn)
        let executed = await mock.executedCommands
        #expect(executed.count == 3)
        #expect(executed.allSatisfy { command in
            if case .setLightPower(_, let on) = command { return on }
            return false
        })
        #expect(Set(executed.compactMap { command -> String? in
            if case .setLightPower(let entityId, _) = command { return entityId }
            return nil
        }) == ["light.dev_study_ceiling", "light.dev_study_desk", "light.dev_kitchen_light"])
        #expect(store.devices.filter { $0.isActiveOn }.count == 4)
        #expect(store.undoBanner?.restoreCommands.count == 3)

        let undo = try #require(store.undoTurnAllOff())
        await undo.value
        #expect(store.devices.filter { $0.isActiveOn }.map(\.id) == ["dev_bedroom_lamp"])
    }

    @Test("A room the panel shows decides which dark lights a bare 开灯 opens")
    func openPanelRoomDecides() async throws {
        let (store, mock) = try await Self.makeStore([
            Self.studyCeiling.off(), Self.studyDesk.off(), Self.bedroomLamp, Self.kitchenLight
        ])
        #expect(store.selectRoom("study"))
        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }

        let plan = try #require(await store.runAssistantCommand("开灯"))
        #expect(plan.intents.map(\.targetId) == ["dev_study_ceiling", "dev_study_desk"])
        #expect(await mock.executedCommands.count == 2)
        #expect(prompts == 0, "低风险灯不应二次确认")
        #expect(store.devices.first { $0.id == "dev_kitchen_light" }?.isActiveOn == false)
        #expect(store.undoBanner?.restoreCommands.count == 2)
    }

    @Test("An auto-resolved off plug asks before it switches on")
    func openSingleOffPlugAsksFirst() async throws {
        let studyPlug = Self.plug("dev_plug_study", "书房插座", roomId: "study", roomName: "书房")
        let livingPlug = Self.plug(
            "dev_plug_living", "客厅插座", roomId: "living", roomName: "客厅", isOn: true
        )
        let (store, mock) = try await Self.makeStore([studyPlug, livingPlug], rooms: [])
        var prompts: [ManualControlConfirmation] = []
        store.manualControlConfirmationHandler = { prompt in
            prompts.append(prompt)
            return false
        }

        let cancelled = try #require(await store.runAssistantCommand("打开插座"))
        #expect(cancelled.intents.isEmpty)
        #expect(prompts.count == 1)
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.devices.first { $0.id == "dev_plug_study" }?.isActiveOn == false)

        store.manualControlConfirmationHandler = { prompt in
            prompts.append(prompt)
            return true
        }
        let approved = try #require(await store.runAssistantCommand("打开插座"))
        #expect(approved.intents == [
            DeviceIntent(targetId: "dev_plug_study", operation: .power(true))
        ])
        #expect(prompts.count == 2)
        #expect(await mock.executedCommands == [
            .setSwitch(entityId: "switch.dev_plug_study", isOn: true)
        ])
        #expect(store.devices.first { $0.id == "dev_plug_study" }?.isActiveOn == true)
    }

    @Test("A panel room auto-resolving several heaters asks for each one first")
    func openPanelRoomHeatersAskFirst() async throws {
        let (store, mock) = try await Self.makeStore([
            Self.heater("dev_heat_study", "书房电暖器", roomId: "study", roomName: "书房"),
            Self.heater("dev_heat_study_desk", "书房暖风机", roomId: "study", roomName: "书房"),
            Self.heater("dev_heat_living", "客厅电暖器", roomId: "living", roomName: "客厅")
        ], rooms: [
            RoomArea(id: "study", name: "书房", sortOrder: 0),
            RoomArea(id: "living", name: "客厅", sortOrder: 1)
        ])
        #expect(store.selectRoom("study"))
        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }

        let plan = try #require(await store.runAssistantCommand("打开电暖器"))
        #expect(plan.intents.isEmpty)
        #expect(prompts == 2)
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.devices.allSatisfy { $0.isActiveOn == false })
    }

    @Test("A delayed auto-resolved high-risk open asks now and sets no timer")
    func delayedOpenPlugAsksNow() async throws {
        let studyPlug = Self.plug("dev_plug_study", "书房插座", roomId: "study", roomName: "书房")
        let livingPlug = Self.plug(
            "dev_plug_living", "客厅插座", roomId: "living", roomName: "客厅", isOn: true
        )
        let (store, mock) = try await Self.makeStore([studyPlug, livingPlug], rooms: [])
        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }

        let plan = try #require(await store.runAssistantCommand("10分钟后打开插座"))
        #expect(plan.intents.isEmpty)
        #expect(prompts == 1)
        #expect(store.scheduledCommands.isEmpty)
        #expect(await mock.executedCommands.isEmpty)
    }

    @Test("A high-risk open candidate still asks before it runs")
    func hazardousOpenCandidateAsks() async throws {
        let studyPlug = Self.plug("dev_plug_study", "书房插座", roomId: "study", roomName: "书房")
        let bedroomPlug = Self.plug("dev_plug_bedroom", "主卧插座", roomId: "bedroom", roomName: "主卧")
        let (store, mock) = try await Self.makeStore([studyPlug, bedroomPlug], rooms: [])
        _ = await store.runAssistantCommand("打开插座")
        let candidate = try #require(store.assistantSuggestions.first { $0.title == "打开书房插座" })
        #expect(await mock.executedCommands.isEmpty)

        var prompts: [ManualControlConfirmation] = []
        store.manualControlConfirmationHandler = { prompt in
            prompts.append(prompt)
            return false
        }
        await store.applyAssistantSuggestion(candidate)
        #expect(prompts.count == 1)
        #expect(await mock.executedCommands.isEmpty)
        #expect(store.devices.allSatisfy { $0.isActiveOn == false })

        store.manualControlConfirmationHandler = { _ in true }
        await store.applyAssistantSuggestion(candidate)
        #expect(await mock.executedCommands == [
            .setSwitch(entityId: "switch.dev_plug_study", isOn: true)
        ])
        #expect(store.devices.first { $0.id == "dev_plug_study" }?.isActiveOn == true)
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
