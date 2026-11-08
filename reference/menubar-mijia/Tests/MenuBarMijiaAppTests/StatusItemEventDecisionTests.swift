import AppKit
import Foundation
import Testing
@testable import MenuBarMijiaApp

/// Each macOS status-item quirk as one row: event + press state in, decision out.
struct StatusItemEventDecisionTests {
    typealias State = StatusItemEventDecision.State

    private func decide(_ type: NSEvent.EventType?, buttons: Int = 0, now: TimeInterval = 100, _ state: State = State()) -> StatusItemEventDecision {
        StatusItemEventDecision.decide(eventType: type, pressedMouseButtons: buttons, now: now, state: state)
    }

    @Test("A real left press begins and consumes a pending post-menu click")
    func leftDownBeginsPress() {
        let d = decide(.leftMouseDown, buttons: 1)
        #expect(d.step == .beginPress)
        #expect(d.clearsPostMenuMouseUp)
        #expect(!d.cancelsReleaseSuppression)
    }

    @Test("The menu's synthetic pair is filtered; a real click cancels the suppression tail")
    func syntheticPairAfterMenu() {
        let suppressed = State(suppressReleaseUntil: 200)
        #expect(decide(.leftMouseDown, buttons: 0, suppressed).step == .none)
        #expect(decide(.leftMouseDown, buttons: 0, suppressed).trace == "guard=suppressReleaseUntil")
        let real = decide(.leftMouseDown, buttons: 1, suppressed)
        #expect(real.step == .beginPress)
        #expect(real.cancelsReleaseSuppression)
    }

    @Test("macOS 26's early mouse-up while the button is held does not finish the press")
    func earlyUpWhileHeld() {
        let d = decide(.leftMouseUp, buttons: 1, State(isPressActive: true))
        #expect(d.step == .none)
        #expect(d.trace == "guard=primaryStillPressed")
        #expect(decide(.leftMouseUp, buttons: 0, State(isPressActive: true)).step == .finishPress)
    }

    @Test("A lone mouse-up after the context menu toggles once, unless still in the release tail")
    func postMenuMouseUp() {
        #expect(decide(.leftMouseUp, State(acceptsStandalonePostMenuMouseUp: true)).step == .postMenuToggle)
        #expect(decide(.leftMouseUp, State(acceptsStandalonePostMenuMouseUp: true, suppressReleaseUntil: 200)).step == .none)
        #expect(decide(.leftMouseUp).trace == "guard=inactiveRelease")
    }

    @Test("Events that belong to the context menu's own dismissal only restore the highlight")
    func dismissalEvents() {
        for type: NSEvent.EventType in [.leftMouseDown, .leftMouseUp] {
            let d = decide(type, buttons: 1, State(isContextMenuDismissalEvent: true, isPressActive: true))
            #expect(d.step == .none)
            #expect(d.restoresHighlight)
        }
        #expect(decide(.rightMouseDown).step == .requestContextMenu)
        #expect(decide(.rightMouseUp).restoresHighlight)
    }

    @Test("Keyboard activation toggles; a stale post-menu fallback past the grace window is dropped first")
    func keyboardActivation() {
        #expect(decide(nil).step == .toggle)
        let stale = decide(nil, now: 100, State(acceptsStandalonePostMenuMouseUp: true, suppressContextMenuUntil: 50))
        #expect(stale.step == .toggle)
        #expect(stale.clearsPostMenuMouseUp)
        let inGrace = decide(nil, now: 100, State(acceptsStandalonePostMenuMouseUp: true, suppressContextMenuUntil: 150))
        #expect(inGrace.step == .none)
        #expect(inGrace.trace == "guard=postMenuAwaitingMouseUp")
        #expect(decide(nil, State(pressTriggered: true)).trace == "guard=pressTriggered")
    }
}
