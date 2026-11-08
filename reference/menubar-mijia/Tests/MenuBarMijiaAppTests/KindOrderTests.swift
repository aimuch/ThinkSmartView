import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// The device-kind chips (插座、扫地机…) share the room-order session: one 完成 / 取消 settles
/// both lists, and the chip row follows the saved kind order.
@MainActor
@Suite(.serialized)
struct KindOrderTests {
    private func makeSuite() -> (UserDefaults, String) {
        let name = "test.kindorder.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: name) else {
            fatalError("could not create a defaults suite")
        }
        return (defaults, name)
    }

    private let twoRooms = [
        RoomArea(id: "living", name: "客厅"),
        RoomArea(id: "bedroom", name: "卧室")
    ]

    private let tags: [DeviceKindTag] = [.plug, .vacuum, .camera, .sensor]
    private var kinds: [RoomArea] { KindOrderPrefs.items(for: tags) }

    @Test("With no saved order the chips keep the built-in order")
    func emptyOrderKeepsRank() {
        #expect(KindOrderPrefs.apply(order: [], to: tags) == tags)
    }

    @Test("A saved order wins, and kinds it does not know follow in built-in order")
    func savedOrderWinsUnknownKindsFollow() {
        let order = ["sensor", "vacuum", "light"]
        #expect(KindOrderPrefs.apply(order: order, to: tags) == [.sensor, .vacuum, .plug, .camera])
    }

    @Test("Committing writes the kind order alongside the room order")
    func commitWritesKindOrder() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        let ui = LayoutUIState()

        ui.beginRoomOrder(rooms: twoRooms, order: [], aliases: [:], kinds: kinds, kindOrder: [], target: .kinds)
        #expect(ui.orderTarget == .kinds)
        #expect(ui.focusedRoomId == "plug")
        ui.updateActiveOrderDraft(items: kinds) { $0.moveToFront(id: "sensor") }

        ui.exitRoomOrder(.commit, defaults: defaults)

        #expect(ui.editMode == .none)
        #expect(ui.kindOrderDraft == nil)
        #expect(ui.orderTarget == .rooms)
        let written = KindOrderPrefs.storedOrder(from: defaults)
        #expect(KindOrderPrefs.apply(order: written, to: tags) == [.sensor, .plug, .vacuum, .camera])
        // The room list was not touched, so the rooms keep their provider order.
        #expect(RoomPrefs.apply(order: RoomPrefs.storedOrder(from: defaults), to: twoRooms).map(\.id) == ["living", "bedroom"])
    }

    @Test("Cancel drops both drafts and writes neither key")
    func cancelWritesNothing() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        KindOrderPrefs.persist(order: ["camera"], to: defaults)
        let ui = LayoutUIState()

        ui.beginRoomOrder(rooms: twoRooms, order: [], aliases: [:], kinds: kinds, kindOrder: ["camera"])
        ui.updateRoomOrderDraft(rooms: twoRooms) { $0.moveToFront(id: "bedroom") }
        ui.switchOrderTarget(to: .kinds, items: kinds)
        ui.updateActiveOrderDraft(items: kinds) { $0.moveToEnd(id: "camera") }

        ui.exitRoomOrder(.cancel, defaults: defaults)

        #expect(KindOrderPrefs.storedOrder(from: defaults) == ["camera"])
        #expect(defaults.string(forKey: RoomPrefs.orderKey) == nil)
    }

    @Test("Switching lists moves the focus to the first row of the new list")
    func switchingMovesFocus() {
        let ui = LayoutUIState()
        ui.beginRoomOrder(rooms: twoRooms, order: ["bedroom"], aliases: [:], kinds: kinds, kindOrder: ["camera"])
        #expect(ui.focusedRoomId == "bedroom")

        ui.switchOrderTarget(to: .kinds, items: kinds)
        #expect(ui.focusedRoomId == "camera")
        #expect(ui.activeOrderDraft == ui.kindOrderDraft)

        ui.switchOrderTarget(to: .rooms, items: twoRooms)
        #expect(ui.focusedRoomId == "bedroom")
    }

    @Test("Keyboard moves edit whichever list is on screen")
    func keyboardMovesActiveList() {
        let ui = LayoutUIState()
        ui.beginRoomOrder(rooms: twoRooms, order: [], aliases: [:], kinds: kinds, kindOrder: [], target: .kinds)

        RoomOrderKeyboard.moveFocus(1, rooms: kinds, ui: ui)
        #expect(ui.focusedRoomId == "vacuum")
        RoomOrderKeyboard.moveFocused(bySlots: -1, rooms: kinds, ui: ui)

        #expect(ui.kindOrderDraft?.visibleOrder(for: kinds) == ["vacuum", "plug", "camera", "sensor"])
        #expect(ui.roomOrderDraft?.visibleOrder(for: twoRooms) == ["living", "bedroom"])
    }
}
