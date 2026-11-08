import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// The header subtitle is a pure value over store state, so it is tested without rendering the panel.
struct PanelHeaderStateTests {
    private let study = EnvironmentReading(id: "env", label: "书房温湿度计", roomName: "书房", temperature: 25.4, humidity: 56)

    private func candidates(_ line: PanelStatusLine) throws -> [String] {
        guard case .sensor(let candidates) = try #require(line.trailing) else {
            Issue.record("expected a sensor reading")
            return []
        }
        return candidates
    }

    @Test("The subtitle carries the primary sensor reading, shedding detail for narrow headers")
    func subtitleFoldsInTheReading() throws {
        let line = PanelStatusLine.make(activeDeviceCount: 4, reading: study, valuesAreStale: false)
        #expect(line.summary.contains("4"))
        let temperature = L10n.temperatureValue(25.4, fractionDigits: 1) + "°"
        let all = try candidates(line)
        #expect(all.count == 3)
        #expect(all[0].contains(L10n.displayRoomName("书房")))
        #expect(!all[0].contains("温湿度计"))
        #expect(all[0].contains(temperature) && all[0].contains("56%"))
        #expect(all[1].contains(L10n.displayRoomName("书房")) && all[1].contains(temperature) && !all[1].contains("56%"))
        #expect(all[2] == temperature)
        #expect(line.candidateCount == 3)
    }

    @Test("Humidity alone still counts as a reading, a label alone does not")
    func readingNeedsAValue() throws {
        let humidityOnly = EnvironmentReading(id: "h", label: "卧室", roomName: "卧室", temperature: nil, humidity: 61)
        let withHumidity = PanelStatusLine.make(activeDeviceCount: 0, reading: humidityOnly, valuesAreStale: false)
        let all = try candidates(withHumidity)
        #expect(all.allSatisfy { $0.contains("61%") })
        #expect(all.last == "61%")

        let empty = EnvironmentReading(id: "e", label: "卧室", roomName: "卧室", temperature: nil, humidity: nil)
        #expect(PanelStatusLine.make(activeDeviceCount: 0, reading: empty, valuesAreStale: false).trailing == nil)
        #expect(PanelStatusLine.make(activeDeviceCount: 0, reading: nil, valuesAreStale: false).trailing == nil)
    }

    @Test("Stale values replace the reading instead of showing an old number")
    func staleHidesTheReading() {
        let line = PanelStatusLine.make(activeDeviceCount: 2, reading: study, valuesAreStale: true)
        #expect(line.trailing == .stale)
    }

    @Test("Zero active devices reads as standby")
    func standbyWording() {
        let idle = PanelStatusLine.make(activeDeviceCount: 0, reading: nil, valuesAreStale: false)
        let busy = PanelStatusLine.make(activeDeviceCount: 3, reading: nil, valuesAreStale: false)
        #expect(idle.summary != busy.summary)
        #expect(!idle.summary.contains("0"))
    }
}
