import Foundation
import Observation
import SwiftUI

/// The panel's saved card order, card sizes and group membership, decoded once and written through.
///
/// The panel and every card read these three JSON preferences. With `@AppStorage` each view
/// decoded its own copy and the panel kept hand-written `live*` mirrors, because `@AppStorage`
/// republishes a moment after a write and a drag or resize would otherwise flicker back. This
/// module holds the decoded value as the truth, publishes it immediately, and persists it to the
/// same keys, so older builds and tests that read the keys see the same data.
///
/// There is one instance per `UserDefaults` (the store's suite in tests, `.standard` in the app).
@MainActor
@Observable
final class PanelLayoutStore {
    private(set) var order: [String]
    private(set) var sizes: [String: TileSize]
    private(set) var groupMap: [String: String]

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var observer: NSObjectProtocol?

    private static var instances: [ObjectIdentifier: PanelLayoutStore] = [:]

    /// The layout saved in `defaults`, shared by every view that renders from it.
    static func shared(for defaults: UserDefaults) -> PanelLayoutStore {
        let key = ObjectIdentifier(defaults)
        if let existing = instances[key] { return existing }
        let created = PanelLayoutStore(defaults: defaults)
        instances[key] = created
        return created
    }

    init(defaults: UserDefaults) {
        self.defaults = defaults
        order = DeviceLayoutPrefs.decodeOrder(defaults.string(forKey: DeviceLayoutPrefs.orderKey) ?? "[]")
        sizes = DeviceLayoutPrefs.decode(defaults.string(forKey: DeviceLayoutPrefs.storageKey) ?? "{}")
        groupMap = GroupPrefs.map(defaults.string(forKey: GroupPrefs.mapKey) ?? "{}")
        // A write that did not come through here (another process, a migration, a test seeding the
        // keys) still reaches the panel.
        observer = NotificationCenter.default.addObserver(
            forName: UserDefaults.didChangeNotification,
            object: defaults,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.reloadIfChangedElsewhere() }
        }
    }

    // MARK: Reads

    /// The saved size of `deviceId` on the page `scope` (room id or "all"), before `.auto` is resolved.
    func size(of deviceId: String, scope: String) -> TileSize {
        DeviceLayoutPrefs.lookup(sizes, id: deviceId, scope: scope)
    }

    // MARK: Writes

    func setOrder(_ next: [String]) {
        guard next != order else { return }
        order = next
        defaults.set(DeviceLayoutPrefs.encodeOrder(next), forKey: DeviceLayoutPrefs.orderKey)
    }

    func setSize(_ size: TileSize, of deviceId: String, scope: String) {
        var next = sizes
        next[DeviceLayoutPrefs.scopedKey(id: deviceId, scope: scope)] = size
        let raw = DeviceLayoutPrefs.encode(next)
        let normalized = DeviceLayoutPrefs.decode(raw)
        guard normalized != sizes else { return }
        sizes = normalized
        defaults.set(raw, forKey: DeviceLayoutPrefs.storageKey)
    }

    /// Files `deviceId` into `groupId`, or back into the ungrouped section when nil.
    func assign(_ deviceId: String, toGroup groupId: String?) {
        var next = groupMap
        next[deviceId] = groupId
        setGroupMap(next)
    }

    /// Empties a deleted group: its devices go back to the ungrouped section.
    func removeGroup(_ groupId: String) {
        setGroupMap(groupMap.filter { $0.value != groupId })
    }

    private func setGroupMap(_ next: [String: String]) {
        guard next != groupMap else { return }
        groupMap = next
        defaults.set(GroupPrefs.encode(next), forKey: GroupPrefs.mapKey)
    }

    private func reloadIfChangedElsewhere() {
        let order = DeviceLayoutPrefs.decodeOrder(defaults.string(forKey: DeviceLayoutPrefs.orderKey) ?? "[]")
        let sizes = DeviceLayoutPrefs.decode(defaults.string(forKey: DeviceLayoutPrefs.storageKey) ?? "{}")
        let groupMap = GroupPrefs.map(defaults.string(forKey: GroupPrefs.mapKey) ?? "{}")
        guard order != self.order || sizes != self.sizes || groupMap != self.groupMap else { return }
        withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
            if order != self.order { self.order = order }
            if sizes != self.sizes { self.sizes = sizes }
            if groupMap != self.groupMap { self.groupMap = groupMap }
        }
    }
}
