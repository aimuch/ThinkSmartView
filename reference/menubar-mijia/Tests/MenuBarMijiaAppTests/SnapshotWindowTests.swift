import AppKit
import Testing

/// #323: a control left holding keyboard focus draws its focus ring into the snapshot, and whether
/// AppKit hands focus out before the capture depends on timing, so `panel-dark` failed on CI now
/// and then. The window the harness captures must never have a focused control.
@MainActor
struct SnapshotWindowTests {
    @Test("Preparing the snapshot window clears a focused control")
    func prepareClearsFocus() {
        _ = NSApplication.shared
        let button = NSButton(title: "Mic", target: nil, action: nil)
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 120, height: 40))
        content.addSubview(button)
        let window = NSWindow(
            contentRect: content.frame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.isReleasedWhenClosed = false
        window.contentView = content
        // What CI hit: a control already owns focus when the capture starts.
        window.makeFirstResponder(button)
        #expect(window.firstResponder === button)

        SnapshotWindow.prepareForCapture(window)

        #expect(window.firstResponder === window)
        window.orderOut(nil)
    }

    @Test("The capture window never lets a control take keyboard focus (#351)")
    func captureWindowRefusesFocus() {
        _ = NSApplication.shared
        let button = NSButton(title: "Mic", target: nil, action: nil)
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 120, height: 40))
        content.addSubview(button)
        let window = SnapshotWindow.make(size: content.frame.size)
        window.contentView = content

        // What SwiftUI does on a later pass: hand focus to the first focusable control.
        window.makeFirstResponder(button)

        #expect(window.firstResponder === window)
        #expect(!window.canBecomeKey)
        window.orderOut(nil)
    }
}
