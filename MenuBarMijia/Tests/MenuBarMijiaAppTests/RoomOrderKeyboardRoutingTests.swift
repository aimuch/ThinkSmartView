import Carbon
import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// #146 §5.5: the room-order list owns the keyboard while it is open. This table is the audit's
/// high-severity fix — Return used to fall through to `toggleFocusedDevice()` — so every key is
/// asserted here instead of only being checked by hand.
/// Serialized: several cases drive a reorder session end to end, and the shared key monitor's state
/// must not interleave with them.
@MainActor
@Suite(.serialized)
struct RoomOrderKeyboardRoutingTests {
    private func action(
        _ keyCode: Int,
        command: Bool = false,
        shift: Bool = false,
        characters: String? = nil
    ) -> HotkeyAndNotificationManager.RoomOrderKeyAction? {
        HotkeyAndNotificationManager.roomOrderKeyAction(
            keyCode: UInt16(keyCode),
            hasCommand: command,
            hasShift: shift,
            charactersIgnoringModifiers: characters
        )
    }

    @Test("Return and keypad Enter commit, Esc cancels")
    func commitAndCancel() {
        #expect(action(kVK_Return) == .commit)
        #expect(action(kVK_ANSI_KeypadEnter) == .commit)
        #expect(action(kVK_Escape) == .cancel)
    }

    @Test("Arrow keys move the list focus, ⌘arrows move the row itself")
    func arrowsAndCommandArrows() {
        #expect(action(kVK_UpArrow) == .moveFocus(-1))
        #expect(action(kVK_DownArrow) == .moveFocus(1))
        #expect(action(kVK_UpArrow, command: true) == .moveRow(-1))
        #expect(action(kVK_DownArrow, command: true) == .moveRow(1))
    }

    @Test("⌘Home and ⌘End send the row to the ends; plain Home/End stay with the dashboard")
    func homeAndEnd() {
        #expect(action(kVK_Home, command: true) == .moveRowToEdge(true))
        #expect(action(kVK_End, command: true) == .moveRowToEdge(false))
        #expect(action(kVK_Home) == nil)
        #expect(action(kVK_End) == nil)
    }

    @Test("Tab walks the list instead of cycling rooms")
    func tabMovesFocus() {
        #expect(action(kVK_Tab) == .moveFocus(1))
        #expect(action(kVK_Tab, shift: true) == .moveFocus(-1))
    }

    @Test("Space never toggles a device while the list is open")
    func spaceIsConsumed() {
        // Without this, Space reached `toggleFocusedDevice()`, which toggles the first visible device
        // even when nothing is focused.
        #expect(action(kVK_Space) == .consume)
    }

    @Test("Left and right arrows are swallowed whatever the modifiers")
    func horizontalArrowsAreConsumed() {
        // `adjustFocusedDevice` does not check modifiers, so ⌘← would still change a stale focused
        // device's value from behind the list.
        #expect(action(kVK_LeftArrow) == .consume)
        #expect(action(kVK_RightArrow) == .consume)
        #expect(action(kVK_LeftArrow, command: true) == .consume)
        #expect(action(kVK_RightArrow, shift: true) == .consume)
    }

    @Test("Quick-index digits never reach the device shortcut")
    func digitsAreConsumed() {
        for character in ["1", "2", "5", "9"] {
            #expect(action(kVK_ANSI_1, characters: character) == .consume)
        }
        // 0 and multi-character input are not quick-index keys.
        #expect(action(kVK_ANSI_0, characters: "0") == nil)
        #expect(action(kVK_ANSI_A, characters: "ab") == nil)
        // ⌘1 also gets swallowed: the dashboard's digit shortcut does not check modifiers, so it
        // would toggle a device from behind the list.
        #expect(action(kVK_ANSI_1, command: true, characters: "1") == .consume)
    }

    @Test("Keys the list does not own fall through to the panel")
    func unownedKeysFallThrough() {
        #expect(action(kVK_ANSI_A, characters: "a") == nil)
        #expect(action(kVK_ANSI_F, command: true, characters: "f") == nil)
        #expect(action(kVK_ANSI_Slash, characters: "/") == nil)
        #expect(action(kVK_ANSI_V, characters: "v") == nil)
    }

    // MARK: - Acting on the action

    /// Each case drives its own `LayoutUIState` and its own defaults suite: the shared singleton
    /// belongs to the running panel, and `UserDefaults.standard` belongs to the developer.
    private func makeSuite() -> (UserDefaults, String) {
        let name = "test.roomorder.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: name) else {
            fatalError("could not create a defaults suite")
        }
        return (defaults, name)
    }

    private let twoRooms = [
        RoomArea(id: "living", name: "客厅"),
        RoomArea(id: "bedroom", name: "卧室")
    ]

    @Test("Return commits the draft and leaves the mode")
    func returnCommitsTheSession() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        let ui = LayoutUIState()

        ui.beginRoomOrder(rooms: twoRooms, order: [], aliases: [:])
        #expect(ui.editMode == .roomOrder)
        ui.updateRoomOrderDraft(rooms: twoRooms) { $0.moveToFront(id: "bedroom") }

        ui.exitRoomOrder(.commit, defaults: defaults)
        #expect(ui.editMode == .none)
        #expect(ui.roomOrderDraft == nil)
        #expect(ui.focusedRoomId == nil)
        #expect(RoomPrefs.storedOrder(from: defaults) == ["bedroom", "living"])
    }

    @Test("Esc drops the draft and writes nothing")
    func escapeRollsBack() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        // The state of the world before the session.
        RoomPrefs.persist(order: ["bedroom", "living"], aliases: ["living": "大厅"], to: defaults)
        let ui = LayoutUIState()

        ui.beginRoomOrder(rooms: twoRooms, order: RoomPrefs.storedOrder(from: defaults), aliases: [:])
        ui.updateRoomOrderDraft(rooms: twoRooms) { draft in
            draft.moveToFront(id: "living")
            draft.rename(id: "living", to: "起居室")
        }
        ui.exitRoomOrder(.cancel, defaults: defaults)

        // A cancel writes nothing: both keys still hold exactly what they held before the session.
        #expect(RoomPrefs.storedOrder(from: defaults) == ["bedroom", "living"])
        #expect(RoomPrefs.decodeAliases(defaults.string(forKey: RoomPrefs.aliasesKey) ?? "{}") == ["living": "大厅"])
        #expect(ui.editMode == .none)
    }

    @Test("The lifecycle hook commits a session that is still open")
    func lifecycleHookCommits() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        let ui = LayoutUIState()
        ui.beginRoomOrder(rooms: twoRooms, order: [], aliases: ["bedroom": "主卧"])

        ui.commitRoomOrderIfNeeded(defaults: defaults)

        #expect(ui.editMode == .none)
        #expect(RoomPrefs.storedOrder(from: defaults) == ["living", "bedroom"])
        #expect(RoomPrefs.decodeAliases(defaults.string(forKey: RoomPrefs.aliasesKey) ?? "{}") == ["bedroom": "主卧"])
    }

    @Test("The lifecycle hook is a no-op outside a session")
    func lifecycleHookOutsideSessionDoesNothing() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        let ui = LayoutUIState()

        ui.commitRoomOrderIfNeeded(defaults: defaults)

        #expect(ui.editMode == .none)
        #expect(defaults.string(forKey: RoomPrefs.orderKey) == nil)
        #expect(defaults.string(forKey: RoomPrefs.aliasesKey) == nil)
    }

    @Test("Committing keeps rooms of another home in the saved order")
    func commitKeepsOtherHomeSlots() {
        let (defaults, name) = makeSuite()
        defer { defaults.removePersistentDomain(forName: name) }
        let ui = LayoutUIState()
        ui.beginRoomOrder(
            rooms: twoRooms,
            order: ["living", "other-home", "bedroom", "other-account"],
            aliases: [:]
        )
        ui.updateRoomOrderDraft(rooms: twoRooms) { $0.move(id: "bedroom", relativeTo: "living", after: false) }
        ui.exitRoomOrder(.commit, defaults: defaults)

        let written = RoomPrefs.storedOrder(from: defaults)
        #expect(written.count == 4)
        #expect(written.contains("other-home"))
        #expect(written.contains("other-account"))
        #expect(RoomPrefs.apply(order: written, to: twoRooms).map(\.id) == ["bedroom", "living"])
    }
}
