import Foundation
import MijiaCore

/// The one-line subtitle under the home title: what is on, plus the single most useful reading.
struct PanelStatusLine: Equatable {
    enum Trailing: Equatable {
        case stale
        /// Widest first; each later candidate sheds something (humidity, then the place) so the
        /// temperature survives a narrow header.
        case sensor(candidates: [String])
    }

    let summary: String
    let trailing: Trailing?

    var candidateCount: Int {
        if case .sensor(let candidates) = trailing { return candidates.count }
        return 1
    }

    static func make(
        activeDeviceCount: Int,
        reading: EnvironmentReading?,
        valuesAreStale: Bool
    ) -> PanelStatusLine {
        // Same wording as the menu-bar tooltip; the header row only leaves about 130 pt for this line.
        let summary = activeDeviceCount > 0
            ? L10n.tr("\(activeDeviceCount) 台开启", "\(activeDeviceCount) on")
            : L10n.tr("全屋待机", "All standby")
        if valuesAreStale {
            return PanelStatusLine(summary: summary, trailing: .stale)
        }
        return PanelStatusLine(summary: summary, trailing: reading.flatMap(sensorTrailing))
    }

    private static func sensorTrailing(_ reading: EnvironmentReading) -> Trailing? {
        // The room, not the device label: "客厅温湿度计" would crowd out the count in a one-line header.
        let place = L10n.displayRoomName(reading.roomName.isEmpty ? reading.label : reading.roomName)
        let temperature = reading.temperature.map { L10n.temperatureValue($0, fractionDigits: 1) + "°" }
        let humidity = reading.humidity.map { String(format: "%.0f%%", $0) }
        let values = [temperature, humidity].compactMap { $0 }
        guard !values.isEmpty else { return nil }
        let candidates = [
            ([place] + values).joined(separator: " "),
            ([place] + [temperature ?? humidity].compactMap { $0 }).joined(separator: " "),
            (temperature ?? humidity) ?? ""
        ]
        return .sensor(candidates: candidates.reduce(into: []) { unique, text in
            if !unique.contains(text) { unique.append(text) }
        })
    }
}
