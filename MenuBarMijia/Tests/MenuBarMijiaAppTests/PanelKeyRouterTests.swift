import Carbon
import Foundation
import Testing
@testable import MenuBarMijiaApp

/// The panel's key priority, asserted as a table without AppKit events or singletons.
struct PanelKeyRouterTests {
    typealias Key = PanelKeyRouter.Key
    typealias Context = PanelKeyRouter.Context

    private func route(_ key: Key, _ context: Context = Context()) -> PanelKeyRouter.Action {
        PanelKeyRouter.beforeSearch(key, context) ?? PanelKeyRouter.afterSearch(key, context)
    }

    @Test("⌘, opens Settings from anywhere")
    func commandComma() {
        #expect(route(Key(code: kVK_ANSI_Comma, command: true), Context(isTextFieldFocused: true)) == .openSettings)
    }

    @Test("The re-auth modal owns the keyboard: Esc dismisses, everything else passes through")
    func reauthModal() {
        let modal = Context(isShowingMiReauth: true, isGrokBallStageExpanded: true)
        #expect(route(Key(code: kVK_Escape), modal) == .dismissMiReauth)
        #expect(route(Key(code: kVK_Space), modal) == .passThrough)
    }

    @Test("Esc closes the GrokBall stage before anything below it")
    func grokStage() {
        #expect(route(Key(code: kVK_Escape), Context(isGrokBallStageExpanded: true, hasExpandedDevice: true)) == .dismissGrokBallStage)
    }

    @Test("An open prompt takes Esc and lets Return and arrows reach its field")
    func prompt() {
        let prompt = Context(hasActivePrompt: true, isTextFieldFocused: true)
        #expect(route(Key(code: kVK_Escape), prompt) == .dismissPrompt)
        #expect(route(Key(code: kVK_Return), prompt) == .passThrough)
        #expect(route(Key(code: kVK_DownArrow), Context(hasActivePrompt: true)) == .passThrough)
    }

    @Test("A global-shortcut echo is swallowed and hides the panel only on a fresh, unregistered press")
    func globalHotkeyEcho() {
        let enabled = Context(globalHotkeyEnabled: true)
        #expect(route(Key(code: kVK_ANSI_M, option: true, matchesGlobalHotkey: true), enabled) == .globalHotkeyEcho(hidePanel: true))
        #expect(route(Key(code: kVK_ANSI_M, option: true, isRepeat: true, matchesGlobalHotkey: true), enabled) == .globalHotkeyEcho(hidePanel: false))
        var registered = enabled
        registered.isGlobalHotkeyRegistered = true
        #expect(route(Key(code: kVK_ANSI_M, option: true, matchesGlobalHotkey: true), registered) == .globalHotkeyEcho(hidePanel: false))
        var recording = enabled
        recording.isRecordingHotkey = true
        #expect(route(Key(code: kVK_ANSI_M, option: true, characters: "m", matchesGlobalHotkey: true), recording) == .passThrough)
    }

    @Test("A focused text field keeps typing; Esc leaves it and closes the assistant bar")
    func textField() {
        #expect(route(Key(code: kVK_Space, characters: " "), Context(isTextFieldFocused: true)) == .passThrough)
        #expect(route(Key(code: kVK_Escape), Context(isTextFieldFocused: true, isShowingAssistantBar: true)) == .leaveTextField(dismissAssistant: true))
    }

    @Test("Outside the panel window device shortcuts never fire")
    func otherWindow() {
        #expect(route(Key(code: kVK_Space), Context(isPanelWindow: false)) == .passThrough)
    }

    @Test("Room order swallows Return, Space and digits but lets / open the assistant")
    func roomOrder() {
        let ordering = Context(editMode: .roomOrder)
        #expect(route(Key(code: kVK_Return), ordering) == .roomOrder(.commit))
        #expect(route(Key(code: kVK_Space, characters: " "), ordering) == .roomOrder(.consume))
        #expect(route(Key(code: kVK_ANSI_3, characters: "3"), ordering) == .roomOrder(.consume))
        #expect(route(Key(code: kVK_ANSI_Slash, characters: "/"), ordering) == .presentAssistant(voice: false))
    }

    @Test("Device-layout mode leaves Return to the Done button")
    func deviceLayoutReturn() {
        #expect(route(Key(code: kVK_Return), Context(editMode: .deviceLayout)) == .passThrough)
    }

    @Test("Plain / and v open the assistant; with ⌘ they do not")
    func assistantKeys() {
        #expect(route(Key(code: kVK_ANSI_V, characters: "v")) == .presentAssistant(voice: true))
        #expect(route(Key(code: kVK_ANSI_V, command: true, characters: "v")) == .passThrough)
    }

    @Test("Device shortcuts: arrows, Space, Tab and digits")
    func deviceShortcuts() {
        #expect(route(Key(code: kVK_UpArrow)) == .moveFocus(-1))
        #expect(route(Key(code: kVK_RightArrow)) == .adjustFocusedDevice(1))
        #expect(route(Key(code: kVK_Space, characters: " ")) == .toggleFocusedDevice)
        #expect(route(Key(code: kVK_Tab, shift: true)) == .cycleRoom(-1))
        #expect(route(Key(code: kVK_ANSI_4, characters: "4")) == .selectQuickIndex(4))
        #expect(route(Key(code: kVK_ANSI_0, characters: "0")) == .passThrough)
    }

    @Test("Esc walks the ladder: assistant bar, drawer, focus, then hide")
    func escapeLadder() {
        #expect(route(Key(code: kVK_Escape), Context(isShowingAssistantBar: true, hasExpandedDevice: true)) == .dismissAssistantBar)
        #expect(route(Key(code: kVK_Escape), Context(hasExpandedDevice: true, hasFocusedDevice: true)) == .collapseExpandedDevice)
        #expect(route(Key(code: kVK_Escape), Context(hasFocusedDevice: true)) == .clearFocus)
        #expect(route(Key(code: kVK_Escape)) == .hidePanel)
    }
}
