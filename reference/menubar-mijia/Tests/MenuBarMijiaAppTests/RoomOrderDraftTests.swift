import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// The reorder session's in-memory model. Everything the room-order list does to the order and the
/// aliases has to be expressible here, because the panel must not touch `UserDefaults` mid-drag.
struct RoomOrderDraftTests {
    private func roomList(_ ids: [String]) -> [RoomArea] {
        ids.enumerated().map { RoomArea(id: $0.element, name: "房间\($0.offset)", sortOrder: $0.offset) }
    }

    private let visible = ["living", "bedroom", "study", "kids"]
    private let ghosts = ["other-home-1", "other-account-2"]

    private func draft(
        order: [String] = ["living", "other-home-1", "bedroom", "study", "other-account-2", "kids"],
        aliases: [String: String] = [:]
    ) -> RoomOrderDraft {
        RoomOrderDraft(order: order, aliases: aliases)
    }

    // MARK: - Seeding and identity

    @Test("A draft dedupes its order and remembers the state it started from")
    func initDedupesAndSnapshots() {
        let subject = RoomOrderDraft(
            order: ["living", "bedroom", "living", "study"],
            aliases: ["living": "大厅", "bedroom": "  "]
        )
        #expect(subject.order == ["living", "bedroom", "study"])
        #expect(subject.initialOrder == subject.order)
        // A blank alias never becomes part of the session, so it cannot be "restored" later.
        #expect(subject.aliases == ["living": "大厅"])
        #expect(subject.alias(for: "bedroom") == nil)
    }

    @Test("The visible projection skips ids whose room is not in the snapshot")
    func visibleOrderSkipsGhosts() {
        let subject = draft()
        let rooms = roomList(visible)
        #expect(subject.visibleOrder(for: rooms) == visible)
        #expect(subject.visibleOrder(for: roomList(visible + ["never-seen"])) == visible + ["never-seen"])
        // A room that vanished keeps its slot in the saved order but drops out of the list; the rest
        // keep the draft's ranking (bedroom is ranked before study), not the provider order.
        #expect(subject.visibleOrder(for: roomList(["study", "living", "bedroom"])) == ["living", "bedroom", "study"])
    }

    // MARK: - Drag and keyboard moves

    @Test("move places an id before or after a target, reusing the saved-order rule")
    func moveRelativeToTarget() {
        var subject = draft()
        subject.move(id: "kids", relativeTo: "living", after: false)
        #expect(subject.order == ["kids", "living", "other-home-1", "bedroom", "study", "other-account-2"])

        subject.move(id: "kids", relativeTo: "study", after: true)
        #expect(subject.order == ["living", "other-home-1", "bedroom", "study", "kids", "other-account-2"])

        // Unknown ids and self-targets change nothing.
        subject.move(id: "ghost-typo", relativeTo: "living", after: true)
        subject.move(id: "living", relativeTo: "nope", after: false)
        subject.move(id: "living", relativeTo: "living", after: true)
        #expect(subject.order == ["living", "other-home-1", "bedroom", "study", "kids", "other-account-2"])
    }

    @Test("A multi-step drag lands where the list shows it, ghost ids included")
    func moveToVisibleIndexWalksPastGhosts() {
        var subject = draft()
        let rooms = roomList(visible)
        var visibleIds = subject.visibleOrder(for: rooms)
        #expect(visibleIds == ["living", "bedroom", "study", "kids"])

        // From the last row to the first in one move.
        let movedToFirst = subject.move(id: "kids", toVisibleIndex: 0, visible: visibleIds)
        #expect(movedToFirst)
        visibleIds = subject.visibleOrder(for: rooms)
        #expect(visibleIds == ["kids", "living", "bedroom", "study"])
        // The other home's ids are still in the saved order, in their old relative positions.
        #expect(subject.order.filter { ghosts.contains($0) } == ghosts)

        // Out-of-range indices clamp to the visible ends instead of dropping the row.
        let movedToLast = subject.move(id: "kids", toVisibleIndex: 99, visible: visibleIds)
        #expect(movedToLast)
        #expect(subject.visibleOrder(for: rooms) == ["living", "bedroom", "study", "kids"])
        let movedToStart = subject.move(id: "kids", toVisibleIndex: -3, visible: subject.visibleOrder(for: rooms))
        #expect(movedToStart)
        #expect(subject.visibleOrder(for: rooms) == ["kids", "living", "bedroom", "study"])

        // Moving a row to the slot it already holds reports "nothing changed".
        let stayed = subject.move(id: "kids", toVisibleIndex: 0, visible: subject.visibleOrder(for: rooms))
        #expect(!stayed)
        // An id that is not in the list is a no-op.
        let ignored = subject.move(id: "not-a-room", toVisibleIndex: 1, visible: subject.visibleOrder(for: rooms))
        #expect(!ignored)
    }

    @Test("Move to front and to back keep every other id in order")
    func moveToFrontAndEnd() {
        var subject = draft()
        subject.moveToEnd(id: "living")
        #expect(subject.order == ["other-home-1", "bedroom", "study", "other-account-2", "kids", "living"])
        subject.moveToFront(id: "living")
        #expect(subject.order == ["living", "other-home-1", "bedroom", "study", "other-account-2", "kids"])
        // Unknown ids are no-ops.
        subject.moveToFront(id: "ghost-typo")
        subject.moveToEnd(id: "ghost-typo")
        #expect(subject.order.first == "living")
        #expect(subject.order.count == 6)
    }

    // MARK: - Reconciling the snapshot

    @Test("A room that appears mid-session joins the end and is flagged once")
    func reconcileAppendsNewRoom() {
        var subject = draft()
        let rooms = roomList(visible + ["bath"])
        subject.reconcile(rooms: rooms)
        #expect(subject.order.last == "bath")
        #expect(subject.isFresh("bath"))
        #expect(!subject.isFresh("living"))
    }

    @Test("A room that disappears mid-session keeps its slot and returns to it")
    func reconcileKeepsMissingRoom() {
        var subject = draft()
        subject.move(id: "study", relativeTo: "living", after: true)
        let before = subject.order

        // The snapshot loses 书房, then gets it back.
        subject.reconcile(rooms: roomList(["living", "bedroom", "kids"]))
        #expect(subject.order == before)
        subject.reconcile(rooms: roomList(visible))
        #expect(subject.order == before)
        #expect(subject.visibleOrder(for: roomList(visible)) == ["living", "study", "bedroom", "kids"])
    }

    // MARK: - Restore default order

    @Test("Restoring the default order re-ranks the visible rooms only")
    func resetOrderKeepsOtherHomes() {
        var subject = draft(order: ["kids", "other-home-1", "study", "bedroom", "other-account-2", "living"])
        subject.rename(id: "living", to: "大厅")
        subject.resetOrder(rooms: roomList(visible))

        #expect(subject.visibleOrder(for: roomList(visible)) == visible)
        // Ghost ids survive, ahead of the re-ranked rooms, in their original relative order.
        #expect(subject.order.filter { ghosts.contains($0) } == ghosts)
        #expect(subject.order.count == 6)
        // Aliases are a separate concern: 恢复默认顺序 never renames anything.
        #expect(subject.alias(for: "living") == "大厅")
    }

    @Test("Restoring the default order of one home leaves another home's rooms untouched")
    func resetOrderIsScopedToTheVisibleHome() {
        var subject = draft(order: ["other-home-1", "other-account-2", "living", "bedroom", "study", "kids"])
        subject.resetOrder(rooms: roomList(visible))
        // The other home's ids were already ahead and stay exactly where they were.
        #expect(Array(subject.order.prefix(2)) == ["other-home-1", "other-account-2"])
        #expect(Array(subject.order.suffix(4)) == ["living", "bedroom", "study", "kids"])
    }

    // MARK: - Aliases

    @Test("Renaming writes the draft, saving the original name clears the alias")
    func renameWritesDraftOnly() {
        var subject = draft(aliases: ["living": "大厅"])
        subject.rename(id: "bedroom", to: "主卧")
        #expect(subject.alias(for: "bedroom") == "主卧")
        #expect(subject.initialAliases == ["living": "大厅"])

        subject.rename(id: "living", to: "  ")
        #expect(subject.alias(for: "living") == nil)

        subject.rename(id: "study", to: nil)
        #expect(subject.alias(for: "study") == nil)
    }

    // MARK: - Dirty tracking and rollback

    @Test("isDirty follows the visible order and the visible aliases only")
    func isDirtyTracksOrderAndAliases() {
        let rooms = roomList(visible)
        var subject = draft(aliases: ["living": "大厅"])
        #expect(!subject.isDirty(currentRooms: rooms))

        subject.rename(id: "study", to: "工作间")
        #expect(subject.isDirty(currentRooms: rooms))

        var orderOnly = draft()
        orderOnly.move(id: "kids", relativeTo: "living", after: true)
        #expect(orderOnly.isDirty(currentRooms: rooms))

        // A room appearing (reconcile appends it) counts as a change to write back.
        var withNewRoom = draft()
        withNewRoom.reconcile(rooms: roomList(visible + ["bath"]))
        #expect(withNewRoom.isDirty(currentRooms: roomList(visible + ["bath"])))

        // Nothing visible changed, so an invisible home's ids on their own never mark the session dirty.
        var ghostOnly = draft(order: ["living", "other-home-1", "bedroom", "study", "other-account-2", "kids"])
        ghostOnly.moveToFront(id: "other-home-1")
        #expect(!ghostOnly.isDirty(currentRooms: rooms))
    }

    @Test("Cancel reads the untouched initial values back out of the draft")
    func cancelCanRollBack() {
        let rooms = roomList(visible)
        var subject = draft(aliases: ["living": "大厅"])
        let initialOrder = subject.initialOrder
        let initialAliases = subject.initialAliases

        subject.move(id: "kids", relativeTo: "living", after: false)
        subject.rename(id: "study", to: "工作间")
        subject.resetOrder(rooms: rooms)

        // A cancel discards the draft; the caller writes nothing, so the saved keys still hold these.
        #expect(subject.initialOrder == initialOrder)
        #expect(subject.initialAliases == initialAliases)
    }

    // MARK: - Writing back

    @Test("persist writes both keys and keeps ids of rooms that are not visible")
    func persistWritesBothKeys() throws {
        let suite = "test.roomorder.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        let subject = draft()
        defaults.set(RoomPrefs.encodeOrder(["stale"]), forKey: RoomPrefs.orderKey)
        defaults.set(RoomPrefs.encodeAliases(["stale": "旧名"]), forKey: RoomPrefs.aliasesKey)

        RoomPrefs.persist(order: subject.order, aliases: ["living": "大厅"], to: defaults)

        let writtenOrder = RoomPrefs.decodeOrder(defaults.string(forKey: RoomPrefs.orderKey) ?? "[]")
        let writtenAliases = RoomPrefs.decodeAliases(defaults.string(forKey: RoomPrefs.aliasesKey) ?? "{}")
        #expect(writtenOrder == subject.order)
        #expect(writtenOrder.contains("other-home-1"))
        #expect(writtenOrder.contains("other-account-2"))
        #expect(writtenAliases == ["living": "大厅"])
    }

    @Test("A persisted draft applied to the snapshot reproduces the list the user saw")
    func committedOrderMatchesThePreviewedStrip() {
        let rooms = roomList(visible)
        var subject = draft()
        let moved = subject.move(id: "kids", toVisibleIndex: 0, visible: subject.visibleOrder(for: rooms))
        #expect(moved)
        let committed = RoomPrefs.apply(order: subject.order, to: rooms)
        #expect(committed.map(\.id) == ["kids", "living", "bedroom", "study"])
    }
}
