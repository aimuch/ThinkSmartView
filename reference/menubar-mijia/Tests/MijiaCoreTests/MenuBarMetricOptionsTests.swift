import Foundation
import Testing
@testable import MijiaCore

/// #331: the menu-bar metric picker offers each metered device's live power, and every
/// device option carries its room so same-named devices in different rooms stay apart.
@MainActor
struct MenuBarMetricOptionsTests {
    private static let rooms = [
        RoomArea(id: "living", name: "客厅", sortOrder: 0),
        RoomArea(id: "bed", name: "卧室", sortOrder: 1),
        RoomArea(id: "hall", name: "玄关", sortOrder: 2),
    ]

    private static func plug(
        _ id: String, name: String, room: RoomArea, watts: Double?, online: Bool = true,
        countsInTotal: Bool = true
    ) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: room.id, roomName: room.name, model: "cuco.plug.v3", isOnline: online,
            kind: .plug(PlugSpec(
                switchEntityId: "miot:\(id):2", isOn: true, powerWatts: watts,
                powerWritable: countsInTotal, countsInTotalPower: countsInTotal
            ))
        )
    }

    private static func store(
        devices: [SmartDevice], readings: [EnvironmentReading] = []
    ) async throws -> HomeStore {
        let home = SynthesizedHome(rooms: rooms, devices: devices, environmentReadings: readings)
        let store = HomeStore(
            provider: MockHomeProvider(snapshot: home),
            defaultsSuiteName: "test.issue331.\(UUID().uuidString)"
        )
        for _ in 0..<100 where store.devices.count < devices.count {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(store.devices.count == devices.count)
        return store
    }

    private static let breaker = plug("breaker", name: "总闸", room: rooms[2], watts: 2_400, countsInTotal: false)
    private static let kettle = plug("kettle", name: "插座", room: rooms[0], watts: 1_500)
    private static let bedPlug = plug("bedplug", name: "插座", room: rooms[1], watts: 12)
    private static let offlinePlug = plug("dead", name: "台灯插座", room: rooms[1], watts: 0, online: false)
    private static let unmetered = plug("bare", name: "智能插座", room: rooms[0], watts: nil)

    @Test("Each online metered plug, breaker included, gets a room-prefixed power option sorted by room")
    func powerOptionsPerDevice() async throws {
        let store = try await Self.store(devices: [Self.breaker, Self.bedPlug, Self.kettle, Self.offlinePlug, Self.unmetered])
        try L10n.$overrideLanguage.withValue(.zhHans) {
            let power = store.availableMenuBarOptions.filter { $0.id.hasPrefix("power:") && $0.id != "power:total" }
            #expect(power.map(\.id) == ["power:kettle", "power:bedplug", "power:breaker"])
            #expect(power.map(\.title) == ["客厅 · 插座 · 功率", "卧室 · 插座 · 功率", "玄关 · 总闸 · 功率"])
            let ids = store.availableMenuBarOptions.map(\.id)
            let total = try #require(ids.firstIndex(of: "power:total"))
            let first = try #require(ids.firstIndex(of: "power:kettle"))
            #expect(total < first)
        }
    }

    @Test("Selecting a device shows its own watts; the breaker still stays out of the home total")
    func menuBarShowsDeviceWatts() async throws {
        let store = try await Self.store(devices: [Self.breaker, Self.kettle, Self.bedPlug])
        store.menuBarTargetId = "power:breaker"
        #expect(store.menuBarText == "2400W")
        store.menuBarTargetId = "power:bedplug"
        #expect(store.menuBarText == "12W")
        store.menuBarTargetId = "power:total"
        #expect(store.menuBarText == "1512W")
    }

    @Test("An offline or removed device never shows 0W; it falls back like a missing temperature sensor")
    func offlineOrMissingFallsBack() async throws {
        let reading = EnvironmentReading(id: "s", label: "卧室", roomName: "卧室", temperature: 22.5, humidity: 40)
        let store = try await Self.store(devices: [Self.offlinePlug, Self.kettle], readings: [reading])
        L10n.$overrideTemperatureUnit.withValue(.celsius) {
            store.menuBarTargetId = "temp:gone"
            let temperatureFallback = store.menuBarText
            #expect(temperatureFallback == "22.5°C")
            store.menuBarTargetId = "power:dead"
            #expect(store.menuBarText == temperatureFallback)
            store.menuBarTargetId = "power:gone"
            #expect(store.menuBarText == temperatureFallback)
        }
    }

    @Test("Temperature options carry the room, sort by room, and never repeat a room-named label")
    func temperatureOptionsCarryRoom() async throws {
        let readings = [
            EnvironmentReading(id: "hall_t", label: "温度计", roomName: "玄关", temperature: 20, humidity: nil),
            EnvironmentReading(id: "bed_s", label: "卧室", roomName: "卧室", temperature: 22, humidity: 45),
            EnvironmentReading(id: "living_ac", label: "空调", roomName: "客厅", temperature: 26, humidity: nil),
            EnvironmentReading(id: "bed_ac", label: "空调", roomName: "卧室", temperature: 25, humidity: nil),
        ]
        let store = try await Self.store(devices: [Self.kettle], readings: readings)
        L10n.$overrideLanguage.withValue(.zhHans) {
            let titles = store.availableMenuBarOptions
                .filter { $0.id.hasPrefix("temp") }
                .map(\.title)
            #expect(titles == [
                "客厅 · 空调 · 温度",
                "卧室 · 温度",
                "卧室 · 温湿度",
                "卧室 · 空调 · 温度",
                "玄关 · 温度计 · 温度",
            ])
        }
    }

    @Test("A switched-off plug reads 0W rather than its stale last reading")
    func switchedOffPlugReadsZero() async throws {
        let off = SmartDevice(
            id: "off", name: "插座", roomId: "living", roomName: "客厅", model: "cuco.plug.v3", isOnline: true,
            kind: .plug(PlugSpec(switchEntityId: "miot:off:2", isOn: false, powerWatts: 37))
        )
        let store = try await Self.store(devices: [off])
        store.menuBarTargetId = "power:off"
        #expect(store.menuBarText == "0W")
    }

    @Test("A device without a name of its own is labelled by its room alone")
    func emptyLabelUsesRoom() {
        #expect(HomeStore.roomQualifiedLabel("  ", room: "书房") == "书房")
        #expect(HomeStore.roomQualifiedLabel("台灯", room: "") == "台灯")
    }
}
