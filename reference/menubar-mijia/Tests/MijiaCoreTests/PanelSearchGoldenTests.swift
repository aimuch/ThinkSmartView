import Foundation
import Testing
@testable import MijiaCore

struct PanelSearchGoldenTests {
    private struct Corpus: Decodable {
        struct Device: Decodable {
            let id: String
            let name: String
            let room: String
            let pinyin: String
            let roomPinyin: String
        }
        struct Match: Decodable { let query: String; let ids: [String] }
        struct Query: Decodable { let query: String; let kind: String }
        let devices: [Device]
        let matches: [Match]
        let kinds: [Query]
    }

    @Test("Windows and macOS share panel search matching and Return routing")
    func sharedSearchCorpus() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let data = try Data(contentsOf: root.appendingPathComponent("Tests/Fixtures/panel_search_golden.json"))
        let corpus = try JSONDecoder().decode(Corpus.self, from: data)
        let entries = corpus.devices.map { row in
            DeviceSearchEntry(
                deviceId: row.id, name: PanelSearch.normalize(row.name), room: PanelSearch.normalize(row.room),
                pinyin: PanelSearch.normalize(row.pinyin),
                initials: row.pinyin.split(separator: " ").compactMap(\.first).map(String.init).joined(),
                roomPinyin: PanelSearch.normalize(row.roomPinyin),
                roomInitials: row.roomPinyin.split(separator: " ").compactMap(\.first).map(String.init).joined()
            )
        }
        let devices = corpus.devices.map { row in
            SmartDevice(id: row.id, name: row.name, roomId: row.room, roomName: row.room,
                        model: "test.light", isOnline: true,
                        kind: .light(LightSpec(entityId: row.id, isOn: false)))
        }
        for row in corpus.matches {
            #expect(PanelSearch.matches(row.query, entries: entries).map(\.deviceId) == row.ids, "query=\(row.query)")
        }
        for row in corpus.kinds {
            let expected: PanelQueryKind = row.kind == "command" ? .command : .filter
            #expect(PanelSearch.kind(of: row.query, devices: devices, scenes: []) == expected, "query=\(row.query)")
        }
    }
}
