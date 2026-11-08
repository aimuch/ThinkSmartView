import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// The list's drag arithmetic: the row pitch, the mid-line swap rule, the edge auto-scroll ramp,
/// and — the point of the whole redesign — one continuous press reaching any row from any other at
/// 16 rooms (#146 §5.4, §7.2 row "16 房间排序").
struct RoomOrderDragMathTests {
    private let rooms: [RoomArea] = (0..<16).map {
        RoomArea(id: "room_\($0)", name: "房间\($0 + 1)", sortOrder: $0)
    }

    @Test("A point maps to the row it is over; the gap belongs to the row above it")
    func rowIndexFromPointer() {
        let step = RoomOrderMetrics.rowStep
        #expect(RoomOrderMetrics.rowIndex(atContentY: 0, rowCount: 16) == 0)
        #expect(RoomOrderMetrics.rowIndex(atContentY: RoomOrderMetrics.rowHeight - 1, rowCount: 16) == 0)
        // 30…34 is the 4 pt gap after row 0, still row 0.
        #expect(RoomOrderMetrics.rowIndex(atContentY: RoomOrderMetrics.rowHeight + 3, rowCount: 16) == 0)
        #expect(RoomOrderMetrics.rowIndex(atContentY: step, rowCount: 16) == 1)
        #expect(RoomOrderMetrics.rowIndex(atContentY: 15 * step + 12, rowCount: 16) == 15)
        // Past the last row, and above the list, there is no row.
        #expect(RoomOrderMetrics.rowIndex(atContentY: 16 * step, rowCount: 16) == nil)
        #expect(RoomOrderMetrics.rowIndex(atContentY: -1, rowCount: 16) == nil)
        #expect(RoomOrderMetrics.rowIndex(atContentY: 0, rowCount: 0) == nil)
    }

    @Test("A row only swaps once its centre crosses the neighbour's midline")
    func targetIndexFlipsAtHalfARow() {
        let step = RoomOrderMetrics.rowStep
        let top = 5 * step
        // Still under half a row of travel: the row keeps its slot.
        #expect(RoomOrderMetrics.targetIndex(rowTop: top, rowCount: 16) == 5)
        #expect(RoomOrderMetrics.targetIndex(rowTop: top + 16, rowCount: 16) == 5)
        // Half a row (17 pt) down moves it into slot 6; half a row up into slot 4.
        #expect(RoomOrderMetrics.targetIndex(rowTop: top + 17, rowCount: 16) == 6)
        #expect(RoomOrderMetrics.targetIndex(rowTop: top - 18, rowCount: 16) == 4)
        // The ends clamp instead of running off the list.
        #expect(RoomOrderMetrics.targetIndex(rowTop: -4_000, rowCount: 16) == 0)
        #expect(RoomOrderMetrics.targetIndex(rowTop: 4_000, rowCount: 16) == 15)
    }

    @Test("The auto-scroll ramp is 600 pt/s at the edge, 120 pt/s at the inner end, 0 outside")
    func autoScrollRamp() {
        #expect(RoomOrderMetrics.autoScrollSpeed(distanceToEdge: 0) == RoomOrderMetrics.maxAutoScrollSpeed)
        let justInside = RoomOrderMetrics.edgeTrigger - 0.001
        #expect(RoomOrderMetrics.autoScrollSpeed(distanceToEdge: justInside) > RoomOrderMetrics.minAutoScrollSpeed)
        #expect(RoomOrderMetrics.autoScrollSpeed(distanceToEdge: RoomOrderMetrics.edgeTrigger) == 0)
        #expect(RoomOrderMetrics.autoScrollSpeed(distanceToEdge: 400) == 0)
        #expect(RoomOrderMetrics.autoScrollSpeed(distanceToEdge: -1) == 0)
        // Closer to the edge is always faster: the linear ramp the design asks for.
        let samples = stride(from: CGFloat(0), through: RoomOrderMetrics.edgeTrigger, by: 2).map {
            RoomOrderMetrics.autoScrollSpeed(distanceToEdge: $0)
        }
        for pair in zip(samples, samples.dropFirst()) {
            #expect(pair.0 >= pair.1)
        }
    }

    /// The acceptance test for the redesign: the last row travels to the first in one continuous
    /// press, without the pointer ever leaving the button.
    @Test("One press drags row 16 to the top of a 16-room list")
    func singlePressTravelsTheWholeList() {
        let step = RoomOrderMetrics.rowStep
        var draft = RoomOrderDraft(order: rooms.map(\.id), aliases: [:])
        var visible = draft.visibleOrder(for: rooms)
        #expect(visible.count == 16)

        // Grab the middle of the last row.
        let id = visible[15]
        var rowTop = step * 15

        // Travel up 2 pt at a time — no re-pick-up, no scroll reset, no second gesture.
        var presses = 0
        while rowTop > 0 {
            rowTop -= 2
            presses += 1
            guard let target = RoomOrderMetrics.targetIndex(rowTop: rowTop, rowCount: visible.count),
                  let current = visible.firstIndex(of: id), target != current
            else { continue }
            draft.move(id: id, toVisibleIndex: target, visible: visible)
            visible = draft.visibleOrder(for: rooms)
        }

        #expect(visible.first == id)
        #expect(visible.count == 16)
        // Rows never teleport past each other mid-drag: every intermediate order is still a permutation
        // of the same 16 ids.
        #expect(Set(visible) == Set(rooms.map(\.id)))
        #expect(presses > 100, "the drag should have travelled the full height")
    }

    @Test("Keyboard-style single-slot moves walk a row to the front one step at a time")
    func commandArrowWalksToTheFront() {
        var draft = RoomOrderDraft(order: rooms.map(\.id), aliases: [:])
        let id = "room_15"
        for _ in 0..<15 {
            var visible = draft.visibleOrder(for: rooms)
            let from = visible.firstIndex(of: id) ?? 0
            draft.move(id: id, toVisibleIndex: from - 1, visible: visible)
            visible = draft.visibleOrder(for: rooms)
            #expect(visible.firstIndex(of: id) == max(from - 1, 0))
        }
        #expect(draft.visibleOrder(for: rooms).first == id)
        // Everything else kept its relative order.
        #expect(Array(draft.visibleOrder(for: rooms).dropFirst()) == rooms.map(\.id).filter { $0 != id })
    }

    @Test("A room that vanishes mid-drag leaves the draft's slot intact")
    func vanishingRoomKeepsItsSlot() {
        var draft = RoomOrderDraft(order: rooms.map(\.id), aliases: [:])
        draft.move(id: "room_3", toVisibleIndex: 0, visible: rooms.map(\.id))
        let withoutThree = rooms.filter { $0.id != "room_3" }
        draft.reconcile(rooms: withoutThree)
        // Hidden from the list, not deleted from the order: it returns to the same place.
        #expect(draft.visibleOrder(for: withoutThree).first != "room_3")
        let threeIndex = draft.order.firstIndex(of: "room_3") ?? 0
        #expect(draft.order[(threeIndex + 1)...].contains("room_0"))
        draft.reconcile(rooms: rooms)
        #expect(draft.visibleOrder(for: rooms).first == "room_3")
    }
}
