import Foundation

/// Favorite devices in `favoriteOrder` (then device order), rooms, scenes, then remaining devices;
/// trimmed, deduped, capped at 100.
public enum SpeechRecognitionHints {
    private static let maximumCount = 100

    public static func contextualStrings(
        devices: [SmartDevice],
        rooms: [RoomArea],
        scenes: [HomeScene],
        favoriteDeviceIds: Set<String>,
        favoriteOrder: [String]
    ) -> [String] {
        let devicesById = Dictionary(devices.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var names: [String] = []
        var seen = Set<String>()

        func append(_ name: String) {
            guard names.count < maximumCount else { return }
            let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty, seen.insert(trimmed).inserted else { return }
            names.append(trimmed)
        }

        var orderedFavoriteIds = Set<String>()
        for id in favoriteOrder where favoriteDeviceIds.contains(id) {
            guard orderedFavoriteIds.insert(id).inserted else { continue }
            if let device = devicesById[id] { append(device.name) }
        }
        for device in devices where favoriteDeviceIds.contains(device.id) {
            guard orderedFavoriteIds.insert(device.id).inserted else { continue }
            append(device.name)
        }
        for room in rooms { append(room.name) }
        for scene in scenes { append(scene.name) }
        for device in devices { append(device.name) }
        return names
    }
}
