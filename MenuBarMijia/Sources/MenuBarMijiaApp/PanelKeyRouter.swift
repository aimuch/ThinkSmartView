import Carbon
import Foundation

/// Who owns a key press in the panel, decided without touching AppKit or any singleton.
///
/// The panel's keyboard is a priority chain: the re-auth modal, the GrokBall stage, search, an
/// open prompt, the global shortcut, a focused text field, the room-order session, device-layout
/// mode, the assistant keys and finally the device shortcuts. `HotkeyAndNotificationManager`
/// snapshots the state into a `Context`, asks the router, and performs the action. The order is
/// therefore a table tests can assert key by key. Search keeps its own handler and runs between
/// the two stages, because its state lives in `PanelSearchKeyboard`.
enum PanelKeyRouter {
    struct Key: Equatable {
        var code: Int
        var command = false
        var shift = false
        var option = false
        var control = false
        var characters: String?
        var isRepeat = false
        /// The press matches the user's global shortcut chord.
        var matchesGlobalHotkey = false
    }

    struct Context: Equatable {
        var isPanelWindow = true
        var isShowingMiReauth = false
        var isGrokBallStageExpanded = false
        var hasActivePrompt = false
        var globalHotkeyEnabled = false
        var isRecordingHotkey = false
        var isGlobalHotkeyRegistered = false
        var isHotkeyPhysicallyDown = false
        var isTextFieldFocused = false
        var isShowingAssistantBar = false
        var editMode: PanelEditMode = .none
        var hasExpandedDevice = false
        var hasFocusedDevice = false
    }

    enum Action: Equatable {
        /// Not the panel's key: let AppKit deliver it.
        case passThrough
        /// The panel swallows the key and does nothing.
        case consume
        case openSettings
        case dismissMiReauth
        case dismissGrokBallStage
        case dismissPrompt
        /// A repeat of the global shortcut leaked into the panel; hide it only on a fresh press
        /// when the shortcut is not registered system-wide.
        case globalHotkeyEcho(hidePanel: Bool)
        /// Esc in a text field: leave the field, and close the assistant bar when it is open.
        case leaveTextField(dismissAssistant: Bool)
        case roomOrder(HotkeyAndNotificationManager.RoomOrderKeyAction)
        case presentAssistant(voice: Bool)
        case moveFocus(Int)
        case adjustFocusedDevice(Int)
        case toggleFocusedDevice
        case cycleRoom(Int)
        case dismissAssistantBar
        case collapseExpandedDevice
        case clearFocus
        case hidePanel
        case selectQuickIndex(Int)
    }

    /// Keys decided before search sees them. nil means search goes next.
    static func beforeSearch(_ key: Key, _ context: Context) -> Action? {
        if key.code == kVK_ANSI_Comma, key.command { return .openSettings }
        if let consumes = HotkeyAndNotificationManager.miReauthKeyDownResult(
            keyCode: key.code,
            isShowingMiReauth: context.isShowingMiReauth,
            isPanelWindow: context.isPanelWindow
        ) {
            return consumes ? .dismissMiReauth : .passThrough
        }
        if key.code == kVK_Escape, context.isGrokBallStageExpanded, context.isPanelWindow {
            return .dismissGrokBallStage
        }
        return nil
    }

    /// Keys search did not take.
    static func afterSearch(_ key: Key, _ context: Context) -> Action {
        if context.hasActivePrompt {
            // Esc belongs to the prompt; the text-field guard below would only resign focus.
            if key.code == kVK_Escape { return .dismissPrompt }
            // Return and ↑↓ go to the prompt's field, never to the card behind it.
            if key.code == kVK_Return || key.code == kVK_UpArrow || key.code == kVK_DownArrow { return .passThrough }
        }

        if context.globalHotkeyEnabled, !context.isRecordingHotkey, key.matchesGlobalHotkey {
            let hide = !context.isGlobalHotkeyRegistered && !key.isRepeat && !context.isHotkeyPhysicallyDown
            return .globalHotkeyEcho(hidePanel: hide)
        }

        if context.isTextFieldFocused {
            return key.code == kVK_Escape ? .leaveTextField(dismissAssistant: context.isShowingAssistantBar) : .passThrough
        }

        // Device shortcuts belong to the menu-bar panel; Settings keeps normal AppKit behaviour (#42).
        guard context.isPanelWindow else { return .passThrough }

        if context.editMode == .roomOrder,
           let action = HotkeyAndNotificationManager.roomOrderKeyAction(
               keyCode: UInt16(key.code),
               hasCommand: key.command,
               hasShift: key.shift,
               charactersIgnoringModifiers: key.characters
           ) {
            return .roomOrder(action)
        }

        // Return triggers the Done button's default action in device-layout mode.
        if context.editMode == .deviceLayout, key.code == kVK_Return { return .passThrough }

        let plain = !key.command && !key.option && !key.control
        if plain, key.characters == "/" { return .presentAssistant(voice: false) }
        if plain, key.characters?.lowercased() == "v" { return .presentAssistant(voice: true) }

        switch key.code {
        case kVK_UpArrow: return .moveFocus(-1)
        case kVK_DownArrow: return .moveFocus(1)
        case kVK_LeftArrow: return .adjustFocusedDevice(-1)
        case kVK_RightArrow: return .adjustFocusedDevice(1)
        case kVK_Space, kVK_Return: return .toggleFocusedDevice
        case kVK_Tab: return .cycleRoom(key.shift ? -1 : 1)
        case kVK_Escape:
            if context.isShowingAssistantBar { return .dismissAssistantBar }
            if context.hasExpandedDevice { return .collapseExpandedDevice }
            if context.hasFocusedDevice { return .clearFocus }
            return .hidePanel
        default:
            break
        }

        if let chars = key.characters, chars.count == 1, let digit = Int(chars), (1...9).contains(digit) {
            return .selectQuickIndex(digit)
        }
        return .passThrough
    }
}
