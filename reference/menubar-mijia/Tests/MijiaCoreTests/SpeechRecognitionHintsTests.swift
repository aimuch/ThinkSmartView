import Foundation
import Testing
@testable import MijiaCore

struct SpeechRecognitionHintsTests {
    private func device(_ id: String, _ name: String) -> SmartDevice {
        SmartDevice(
            id: id, name: name, roomId: "room.\(id)", roomName: "房间",
            model: "test.light", isOnline: true, kind: .light(LightSpec(entityId: "light.\(id)", isOn: false))
        )
    }

    private func room(_ id: String, _ name: String) -> RoomArea {
        RoomArea(id: id, name: name)
    }

    private func scene(_ id: String, _ name: String) -> HomeScene {
        HomeScene(id: id, name: name, entityId: "scene.\(id)")
    }

    @Test("Favorites come first in favorite order, then rooms, scenes, and other devices")
    func ordering() {
        let devices = [
            device("light.living", "客厅灯"),
            device("light.study", "书房台灯"),
            device("climate.ac", "空调")
        ]

        let hints = SpeechRecognitionHints.contextualStrings(
            devices: devices,
            rooms: [room("bedroom", "次卧")],
            scenes: [scene("movie", "影院模式")],
            favoriteDeviceIds: ["climate.ac", "light.living"],
            favoriteOrder: ["climate.ac", "light.living"]
        )

        #expect(hints == ["空调", "客厅灯", "次卧", "影院模式", "书房台灯"])
    }

    @Test("More than 100 distinct names yields the first 100 in order")
    func cap() {
        let devices = (0..<100).map { device("light.\($0)", "设备\($0)") }

        let hints = SpeechRecognitionHints.contextualStrings(
            devices: devices,
            rooms: [room("room", "房间0")],
            scenes: [scene("scene", "场景0")],
            favoriteDeviceIds: ["light.0"],
            favoriteOrder: ["light.0"]
        )

        let expected = ["设备0", "房间0", "场景0"] + (1...97).map { "设备\($0)" }
        #expect(hints.count == 100)
        #expect(hints == expected)
    }

    @Test("A repeated name appears once at its first position")
    func dedupe() {
        let devices = [
            device("light.study", "书房台灯"),
            device("light.study.2", "书房台灯"),
            device("climate.ac", "空调")
        ]

        let hints = SpeechRecognitionHints.contextualStrings(
            devices: devices,
            rooms: [room("study", "书房台灯"), room("bedroom", "次卧")],
            scenes: [scene("movie", "书房台灯")],
            favoriteDeviceIds: ["light.study"],
            favoriteOrder: ["light.study"]
        )

        #expect(hints == ["书房台灯", "次卧", "空调"])
    }

    @Test("Empty names are dropped and surrounding whitespace is trimmed")
    func emptyAndWhitespaceNames() {
        let devices = [
            device("light.study", "  书房台灯 \n"),
            device("light.blank", "   "),
            device("light.empty", "")
        ]

        let hints = SpeechRecognitionHints.contextualStrings(
            devices: devices,
            rooms: [room("bedroom", "\t次卧  ")],
            scenes: [scene("blank", " \n ")],
            favoriteDeviceIds: [],
            favoriteOrder: []
        )

        #expect(hints == ["次卧", "书房台灯"])
    }

    @Test("Favorite membership decides priority; missing and stale ids are ignored")
    func favoriteMembershipWins() {
        let devices = [device("light.one", "灯一"), device("light.two", "灯二")]

        let hints = SpeechRecognitionHints.contextualStrings(
            devices: devices,
            rooms: [],
            scenes: [],
            favoriteDeviceIds: ["light.two"],
            favoriteOrder: ["light.one", "missing", "light.two"]
        )

        #expect(hints == ["灯二", "灯一"])
    }

    @Test("A favorite without a saved order still comes before rooms")
    func favoriteWithoutSavedOrderStillFirst() {
        let devices = [device("light.one", "灯一"), device("light.study", "书房台灯")]

        let hints = SpeechRecognitionHints.contextualStrings(
            devices: devices,
            rooms: [room("bedroom", "次卧")],
            scenes: [],
            favoriteDeviceIds: ["light.study"],
            favoriteOrder: []
        )

        #expect(hints == ["书房台灯", "次卧", "灯一"])
    }
}
