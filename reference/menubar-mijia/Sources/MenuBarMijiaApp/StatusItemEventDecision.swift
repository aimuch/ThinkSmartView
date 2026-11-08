import AppKit
import Foundation

/// What one status-item click event means, decided from a snapshot of the press state.
///
/// `MenuBarPanelController.handleStatusItemEvent` used to interleave this decision with its
/// side effects (timers, highlight, context menu). The decision is now a pure function of the
/// event and the controller's press state, so each macOS quirk it guards against (the synthetic
/// down/up pair on macOS 26, the post-menu mouse-up, the context menu's own dismissal tail) is
/// one assertable row. The controller performs the result.
struct StatusItemEventDecision: Equatable {
    enum Step: Equatable {
        case none
        case beginPress
        case requestContextMenu
        case finishPress
        /// A mouse-up arriving alone after the context menu closed: toggle once and briefly
        /// suppress the release tail.
        case postMenuToggle
        /// Keyboard or accessibility activation, which never produces a mouse-up.
        case toggle
    }

    struct State: Equatable {
        var isContextMenuDismissalEvent = false
        var pressTriggered = false
        var isPressActive = false
        var acceptsStandalonePostMenuMouseUp = false
        var suppressReleaseUntil: TimeInterval = 0
        var suppressContextMenuUntil: TimeInterval = 0
    }

    var step: Step
    /// The `decision=` or `guard=` suffix for the status-item trace.
    var trace: String
    var restoresHighlight = false
    /// A real click ends the time-based suppression of the menu's synthetic release pair.
    var cancelsReleaseSuppression = false
    /// Drops a pending post-menu mouse-up (an accepted press, or a stale one past the grace window).
    var clearsPostMenuMouseUp = false

    static func decide(
        eventType: NSEvent.EventType?,
        pressedMouseButtons: Int,
        now: TimeInterval,
        state: State
    ) -> StatusItemEventDecision {
        let primaryDown = (pressedMouseButtons & 0x1) != 0
        switch eventType {
        case .leftMouseDown?:
            if state.isContextMenuDismissalEvent {
                return .init(step: .none, trace: "guard=contextMenuDismissalEvent", restoresHighlight: true)
            }
            if state.pressTriggered {
                return .init(step: .none, trace: "guard=pressTriggered")
            }
            var cancels = false
            if now < state.suppressReleaseUntil {
                // The menu-bar host can synthesize a primary pair after dismissing the context
                // menu; it has no physical left-button bit. A real click cancels the tail at once.
                guard primaryDown else { return .init(step: .none, trace: "guard=suppressReleaseUntil") }
                cancels = true
            }
            // Only an accepted down consumes the pending post-menu click.
            return .init(step: .beginPress, trace: "decision=beginPress",
                         cancelsReleaseSuppression: cancels, clearsPostMenuMouseUp: true)

        case .rightMouseDown?:
            return .init(step: .requestContextMenu, trace: "decision=requestStatusContextMenu(fallback)")

        case .rightMouseUp?:
            return .init(step: .none, trace: "guard=rightMouseUp", restoresHighlight: true)

        case .leftMouseUp?:
            if state.isContextMenuDismissalEvent {
                return .init(step: .none, trace: "guard=contextMenuDismissalEvent", restoresHighlight: true)
            }
            // macOS 26 (NSSceneStatusItem) synthesizes down and up together while the button is
            // still held; only a release with the button physically up finishes the press.
            guard !primaryDown else {
                return .init(step: .none, trace: "guard=primaryStillPressed", restoresHighlight: true)
            }
            if state.isPressActive {
                return .init(step: .finishPress, trace: "decision=finishPress", restoresHighlight: true)
            }
            if state.acceptsStandalonePostMenuMouseUp {
                if now >= state.suppressReleaseUntil {
                    return .init(step: .postMenuToggle, trace: "decision=postMenuStandaloneToggle",
                                 restoresHighlight: true, clearsPostMenuMouseUp: true)
                }
                return .init(step: .none, trace: "guard=suppressReleaseUntil", restoresHighlight: true)
            }
            return .init(step: .none, trace: "guard=inactiveRelease", restoresHighlight: true)

        default:
            // Past the dismissal grace, a stale post-menu fallback is dropped so the key still toggles;
            // the grace window keeps Escape and the menu's own tail from opening the panel.
            let clearsStale = state.acceptsStandalonePostMenuMouseUp && now >= state.suppressContextMenuUntil
            let accepts = state.acceptsStandalonePostMenuMouseUp && !clearsStale
            let restores = pressedMouseButtons == 0
            if !accepts, !state.pressTriggered, now >= state.suppressReleaseUntil {
                return .init(step: .toggle, trace: "decision=defaultToggle",
                             restoresHighlight: restores, clearsPostMenuMouseUp: clearsStale)
            }
            let reason: String
            if state.pressTriggered {
                reason = "guard=pressTriggered"
            } else if now < state.suppressReleaseUntil {
                reason = "guard=suppressReleaseUntil"
            } else {
                reason = "guard=postMenuAwaitingMouseUp"
            }
            return .init(step: .none, trace: reason, restoresHighlight: restores, clearsPostMenuMouseUp: clearsStale)
        }
    }
}
