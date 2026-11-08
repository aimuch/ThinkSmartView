import AppKit
import Testing
@testable import MenuBarMijiaApp

// Stand-ins named like AVKit's private PiP views; the layout code matches them by class name.
private final class AVPictureInPictureSampleBufferDisplayLayerView: NSView {}
private final class AVPictureInPictureCALayerHostView: NSView {}

@MainActor
struct CameraPictureInPictureLayoutTests {
    @Test("AVKit's PiP content view follows the window and its parked black host view stays offscreen (#97)")
    func pipContentFollowsWindow() {
        let window = NSView(frame: NSRect(x: 0, y: 0, width: 604, height: 340))
        let content = AVPictureInPictureSampleBufferDisplayLayerView(frame: window.bounds)
        let parked = AVPictureInPictureCALayerHostView(frame: NSRect(x: 0, y: 350, width: 604, height: 340))
        let other = NSView(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        window.addSubview(content)
        window.addSubview(other)
        content.addSubview(parked)

        window.setFrameSize(NSSize(width: 864, height: 486))
        CameraPictureInPicture.trackWindowSize(in: window)

        #expect(content.frame == window.bounds)
        #expect(content.autoresizingMask == [.width, .height])
        #expect(parked.frame.minY == 496, "parked view must stay 10 pt past the grown top edge")
        #expect(other.frame == NSRect(x: 0, y: 0, width: 100, height: 100), "non-AVKit views are left alone")
    }

    @Test("A host view AVKit shows at the origin is not moved")
    func visibleHostViewIsLeftAlone() {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 400, height: 225))
        let shown = AVPictureInPictureCALayerHostView(frame: root.bounds)
        root.addSubview(shown)
        CameraPictureInPicture.keepParkedHostViewsOffscreen(in: root)
        #expect(shown.frame == root.bounds)
    }
}
