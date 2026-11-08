import Foundation
import MijiaCore

// MARK: - Panel Edit Mode (#146 room order redesign)

/// The one edit mode the panel is in. Two independent booleans (`isEditingLayout` /
/// `isEditingRooms`) used to be cleared by hand from every entry point, which allowed the illegal
/// "both at once" state; a single enum makes it unrepresentable.
enum PanelEditMode: Equatable {
    case none
    case deviceLayout
    case roomOrder
}

/// How a room-order session ends. Only an explicit cancel rolls the draft back; every service
/// exit (Done, panel hide, search, home switch, entering device layout) commits, so a half-finished
/// reorder is never silently dropped.
enum RoomOrderExit: Equatable {
    case commit
    case cancel
}

/// Which list the order session is showing: the room strip, or the device-kind chips under it.
enum OrderListTarget: Equatable {
    case rooms
    case kinds
}

// MARK: - Draft

/// Room order and aliases for one reorder session, held in memory until the session ends.
///
/// `order` is the *complete* saved order, including ids of rooms that belong to another home or
/// account and are not visible right now. Every mutation keeps those ids in their relative place,
/// which is what stops editing one home from wiping another home's slots (#146 audit item 6).
struct RoomOrderDraft: Equatable {
    private(set) var order: [String]
    private(set) var aliases: [String: String]
    let initialOrder: [String]
    let initialAliases: [String: String]

    init(order: [String], aliases: [String: String]) {
        self.order = RoomPrefs.deduped(order)
        self.aliases = aliases.filter { RoomPrefs.alias(for: $0.key, in: [$0.key: $0.value]) != nil }
        self.initialOrder = self.order
        self.initialAliases = aliases
    }

    /// The saved `alias` for that room, or `nil` when unset or blank.
    func alias(for roomId: String) -> String? { RoomPrefs.alias(for: roomId, in: aliases) }

    /// Visible rooms in list order: the draft's ranking restricted to what the snapshot has, plus
    /// any room the draft has never heard of (appended in provider order).
    func visibleOrder(for rooms: [RoomArea]) -> [String] {
        let ids = Set(rooms.map(\.id))
        let known = order.filter { ids.contains($0) }
        let knownSet = Set(known)
        return known + rooms.map(\.id).filter { !knownSet.contains($0) }
    }

    /// A room that appeared after the session opened; the list marks it once so an incoming device
    /// snapshot does not look like a lost row.
    func isFresh(_ roomId: String) -> Bool { !initialOrder.contains(roomId) }

    /// Folds the current snapshot into the draft: new rooms go to the end, rooms that are
    /// temporarily missing keep their slot. Never deletes an id.
    mutating func reconcile(rooms: [RoomArea]) {
        order = RoomPrefs.seededOrder(order, rooms: rooms)
    }

    /// Places `id` next to `target` (drag reorder). Unknown ids are a no-op.
    mutating func move(id: String, relativeTo target: String, after: Bool) {
        order = RoomPrefs.move(order, id: id, relativeTo: target, after: after)
    }

    /// Places `id` at `index` of the *visible* list, leaving ids of invisible rooms where they are.
    /// Returns whether the order actually changed.
    @discardableResult
    mutating func move(id: String, toVisibleIndex index: Int, visible: [String]) -> Bool {
        let before = order
        order = RoomPrefs.reordered(order, id: id, toVisibleIndex: index, visible: visible)
        return order != before
    }

    mutating func moveToFront(id: String) {
        order = RoomPrefs.moveToFront(order, id: id)
    }

    mutating func moveToEnd(id: String) {
        order = RoomPrefs.moveToEnd(order, id: id)
    }

    /// Sets the alias, or clears it when the text is blank or `nil`.
    mutating func rename(id: String, to alias: String?) {
        if let alias, RoomPrefs.alias(for: id, in: [id: alias]) != nil {
            aliases[id] = alias
        } else {
            aliases.removeValue(forKey: id)
        }
    }

    /// Restores the provider order for the visible rooms only; aliases are untouched.
    mutating func resetOrder(rooms: [RoomArea]) {
        order = RoomPrefs.defaultOrder(order, rooms: rooms)
    }

    /// Whether the session holds anything worth writing back. Compared against the *visible*
    /// projection, so an unrelated home's ids never make this true on their own.
    func isDirty(currentRooms: [RoomArea]) -> Bool {
        let ids = Set(currentRooms.map(\.id))
        if visibleOrder(for: currentRooms) != initialOrder.filter({ ids.contains($0) }) { return true }
        let mine = aliases.filter { ids.contains($0.key) }
        let theirs = initialAliases.filter { ids.contains($0.key) && RoomPrefs.alias(for: $0.key, in: initialAliases) != nil }
        return mine != theirs
    }
}

// MARK: - RoomPrefs helpers for the room-order session

extension RoomPrefs {
    /// Order-preserving de-duplication; the same rule `encodeOrder` applies before writing.
    static func deduped(_ ids: [String]) -> [String] {
        var seen = Set<String>()
        return ids.filter { seen.insert($0).inserted }
    }

    /// `id` first, every other id — including ids of rooms that are not visible — in its old order.
    static func moveToFront(_ order: [String], id: String) -> [String] {
        guard order.contains(id) else { return order }
        return [id] + order.filter { $0 != id }
    }

    /// `id` last; unknown ids are a no-op.
    static func moveToEnd(_ order: [String], id: String) -> [String] {
        guard order.contains(id) else { return order }
        return order.filter { $0 != id } + [id]
    }

    /// `id` moved to `index` of the visible list; ids of rooms that are not visible keep their
    /// slots because the move is expressed relative to a neighbouring visible room. Out-of-range
    /// indices clamp to the visible ends, and an id that is not in the list is a no-op.
    static func reordered(_ order: [String], id: String, toVisibleIndex index: Int, visible: [String]) -> [String] {
        guard visible.contains(id), order.contains(id) else { return order }
        let others = visible.filter { $0 != id }
        let clamped = min(max(index, 0), others.count)
        if clamped < others.count {
            return move(order, id: id, relativeTo: others[clamped], after: false)
        }
        guard let last = others.last else { return order }
        return move(order, id: id, relativeTo: last, after: true)
    }

    /// The "restore default order" result: visible rooms go back to the provider order, and ids of
    /// other homes or accounts keep their relative order ahead of them. Overwriting the whole array
    /// with `rooms.map(\.id)` would drop those ids, which is exactly what #146 audit item 6 flagged.
    static func defaultOrder(_ order: [String], rooms: [RoomArea]) -> [String] {
        let visible = Set(rooms.map(\.id))
        return order.filter { !visible.contains($0) } + rooms.map(\.id)
    }

    /// Writes a finished session back to `UserDefaults`. `LayoutUIState` is not an `@AppStorage`
    /// holder, so the commit path has to write the keys themselves; the panel's `@AppStorage`
    /// observes the same `UserDefaults.standard` and republishes.
    static func persist(order: [String], aliases: [String: String], to defaults: UserDefaults = .standard) {
        defaults.set(encodeOrder(order), forKey: orderKey)
        defaults.set(encodeAliases(aliases), forKey: aliasesKey)
    }
}

// MARK: - Kind chip order

/// The user's order for the device-kind chips (插座、扫地机…) under the room strip.
///
/// The order list edits ids, so each kind travels as a `RoomArea` whose id is the tag's raw value
/// and whose name is its chip title; the draft only reads those two fields. Kinds the saved order
/// does not know yet follow in their built-in `rank` order. Only the chip row reads this: device
/// sections keep their own ordering.
enum KindOrderPrefs {
    static let orderKey = "kind_custom_order"

    static func items(for tags: [DeviceKindTag]) -> [RoomArea] {
        tags.enumerated().map { RoomArea(id: $1.rawValue, name: $1.chipTitle, sortOrder: $0) }
    }

    static func apply(order: [String], to tags: [DeviceKindTag]) -> [DeviceKindTag] {
        RoomPrefs.apply(order: order, to: items(for: tags)).compactMap { DeviceKindTag(rawValue: $0.id) }
    }

    static func persist(order: [String], to defaults: UserDefaults = .standard) {
        defaults.set(RoomPrefs.encodeOrder(order), forKey: orderKey)
    }

    static func storedOrder(from defaults: UserDefaults = .standard) -> [String] {
        RoomPrefs.decodeOrder(defaults.string(forKey: orderKey) ?? "[]")
    }
}

// MARK: - LayoutUIState session

extension LayoutUIState {
    /// The panel is showing the room-order list.
    var isRoomOrdering: Bool { editMode == .roomOrder }

    /// Opens a reorder session. `order` is the saved order; the draft seeds it with every visible
    /// room so a room missing from the saved list still gets a row. The same session also holds the
    /// kind-chip order, so 完成 / 取消 / Esc settle both lists at once.
    func beginRoomOrder(
        rooms: [RoomArea],
        order: [String],
        aliases: [String: String],
        kinds: [RoomArea] = [],
        kindOrder: [String] = [],
        target: OrderListTarget = .rooms
    ) {
        let draft = RoomOrderDraft(order: RoomPrefs.seededOrder(order, rooms: rooms), aliases: aliases)
        renamingRoomId = nil
        editMode = .roomOrder
        roomOrderDraft = draft
        kindOrderDraft = RoomOrderDraft(order: RoomPrefs.seededOrder(kindOrder, rooms: kinds), aliases: [:])
        switchOrderTarget(to: target, items: target == .rooms ? rooms : kinds)
    }

    /// Shows the other list of the open session. Focus moves to the first row of the new list,
    /// because a focused id from the other list means nothing here.
    func switchOrderTarget(to target: OrderListTarget, items: [RoomArea]) {
        orderTarget = target
        focusedRoomId = activeOrderDraft?.visibleOrder(for: items).first
    }

    /// The draft the list is editing right now.
    var activeOrderDraft: RoomOrderDraft? {
        orderTarget == .rooms ? roomOrderDraft : kindOrderDraft
    }

    /// Leaves the room-order session, writing the draft back on every exit except an explicit cancel.
    /// `defaults` is a seam for tests: production always writes the `UserDefaults.standard` the panel's
    /// `@AppStorage` observes.
    func exitRoomOrder(_ exit: RoomOrderExit, defaults: UserDefaults = .standard) {
        guard editMode == .roomOrder else { return }
        if exit == .commit {
            if let draft = roomOrderDraft {
                RoomPrefs.persist(order: draft.order, aliases: draft.aliases, to: defaults)
            }
            if let draft = kindOrderDraft {
                KindOrderPrefs.persist(order: draft.order, to: defaults)
            }
            if defaults === UserDefaults.standard {
                HomeStore.shared.scheduleRaycastSnapshotExport()
            }
        }
        roomOrderDraft = nil
        kindOrderDraft = nil
        orderTarget = .rooms
        focusedRoomId = nil
        renamingRoomId = nil
        editMode = .none
    }

    /// Cancel button / Esc: drop the draft, the strip goes back to the order from before the session.
    func cancelRoomOrder() {
        exitRoomOrder(.cancel)
    }

    /// Called from `MenuBarPanelController.hidePanel()` and `applicationWillTerminate`: a session
    /// must never outlive the screen that shows it, and `DragGesture.onEnded` is not guaranteed to
    /// run when the window is ordered out mid-drag.
    func commitRoomOrderIfNeeded(defaults: UserDefaults = .standard) {
        guard editMode == .roomOrder else { return }
        exitRoomOrder(.commit, defaults: defaults)
    }

    /// Enters device-layout editing, committing any open room-order draft first so no draft
    /// crosses contexts.
    func beginDeviceLayoutEditing() {
        commitRoomOrderIfNeeded()
        editMode = .deviceLayout
    }

    func endDeviceLayoutEditing() {
        guard editMode == .deviceLayout else { return }
        editMode = .none
    }

    /// Runs one mutation on the open draft and folds the snapshot back in afterwards, so a room
    /// that appears or disappears during the session never loses its slot.
    func updateRoomOrderDraft(rooms: [RoomArea], _ mutate: (inout RoomOrderDraft) -> Void) {
        guard var draft = roomOrderDraft else { return }
        mutate(&draft)
        draft.reconcile(rooms: rooms)
        roomOrderDraft = draft
    }

    /// The kind-chip counterpart of `updateRoomOrderDraft`.
    func updateKindOrderDraft(kinds: [RoomArea], _ mutate: (inout RoomOrderDraft) -> Void) {
        guard var draft = kindOrderDraft else { return }
        mutate(&draft)
        draft.reconcile(rooms: kinds)
        kindOrderDraft = draft
    }

    /// Mutates whichever list is on screen; `items` must be that list's items.
    func updateActiveOrderDraft(items: [RoomArea], _ mutate: (inout RoomOrderDraft) -> Void) {
        switch orderTarget {
        case .rooms: updateRoomOrderDraft(rooms: items, mutate)
        case .kinds: updateKindOrderDraft(kinds: items, mutate)
        }
    }

    /// Closes whichever name prompt is open. Returns `true` when a prompt owned the key press, so
    /// Esc never falls through to "clear first responder" / "hide panel" behind a typing field.
    @discardableResult
    func dismissActivePrompt() -> Bool {
        guard newGroupForDevice != nil || renamingGroupId != nil || renamingDeviceId != nil || renamingRoomId != nil
        else { return false }
        newGroupForDevice = nil
        renamingGroupId = nil
        renamingDeviceId = nil
        renamingRoomId = nil
        return true
    }

    var hasActivePrompt: Bool {
        newGroupForDevice != nil || renamingGroupId != nil || renamingDeviceId != nil || renamingRoomId != nil
    }

    #if DEBUG
    /// Snapshot runs and UI tests build on the shared observable, so a previous session's draft must
    /// not leak into the next render.
    func resetForTesting() {
        editMode = .none
        roomOrderDraft = nil
        kindOrderDraft = nil
        orderTarget = .rooms
        focusedRoomId = nil
        newGroupForDevice = nil
        renamingGroupId = nil
        renamingDeviceId = nil
        renamingRoomId = nil
        isEditingLayoutRequested = false
    }
    #endif
}
