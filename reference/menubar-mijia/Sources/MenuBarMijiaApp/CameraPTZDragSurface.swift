import AppKit
import MijiaCore
import SwiftUI

/// Drag surface for a camera with a physical gimbal. It sits above the video and below the HUD
/// buttons, so a press-drag turns the camera while the title bar keeps dragging the window.
struct CameraPTZDragSurface: NSViewRepresentable {
    var onBegan: (CameraPTZPoint) -> Void
    var onMoved: (CameraPTZPoint) -> Void
    var onEnded: () -> Void
    var onDoubleClick: () -> Void

    func makeNSView(context: Context) -> CameraPTZDragView {
        let view = CameraPTZDragView()
        view.onBegan = onBegan
        view.onMoved = onMoved
        view.onEnded = onEnded
        view.onDoubleClick = onDoubleClick
        return view
    }

    func updateNSView(_ nsView: CameraPTZDragView, context: Context) {
        nsView.onBegan = onBegan
        nsView.onMoved = onMoved
        nsView.onEnded = onEnded
        nsView.onDoubleClick = onDoubleClick
    }
}

final class CameraPTZDragView: NSView {
    var onBegan: ((CameraPTZPoint) -> Void)?
    var onMoved: ((CameraPTZPoint) -> Void)?
    var onEnded: (() -> Void)?
    var onDoubleClick: (() -> Void)?
    private var hasPushedCursor = false

    override var acceptsFirstResponder: Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .openHand)
    }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount >= 2 {
            onDoubleClick?()
            return
        }
        if !hasPushedCursor {
            NSCursor.closedHand.push()
            hasPushedCursor = true
        }
        onBegan?(point(for: event))
    }

    override func mouseDragged(with event: NSEvent) {
        onMoved?(point(for: event))
    }

    override func mouseUp(with event: NSEvent) {
        popCursorIfNeeded()
        onEnded?()
    }

    override func viewWillMove(toWindow newWindow: NSWindow?) {
        super.viewWillMove(toWindow: newWindow)
        if newWindow == nil {
            popCursorIfNeeded()
            onEnded?()
        }
    }

    private func popCursorIfNeeded() {
        guard hasPushedCursor else { return }
        hasPushedCursor = false
        NSCursor.pop()
    }

    /// AppKit view coordinates grow up; the planner treats y as growing down.
    private func point(for event: NSEvent) -> CameraPTZPoint {
        let local = convert(event.locationInWindow, from: nil)
        return CameraPTZPoint(x: local.x, y: bounds.height - local.y)
    }
}

/// Shown when the local bridge rejects a command or a command cannot reach it.
struct CameraPTZNoticeBanner: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(Color.orange.opacity(0.9)))
            .allowsHitTesting(false)
    }
}

