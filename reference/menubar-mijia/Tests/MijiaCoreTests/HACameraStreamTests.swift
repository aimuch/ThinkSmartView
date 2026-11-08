import Foundation
import Testing
@testable import MijiaCore

struct HACameraStreamTests {
    private func camera(stream: String? = nil, snapshot: String? = nil) -> SmartDevice {
        SmartDevice(
            id: "ha-cam", name: "门口", roomId: "door", roomName: "门口", model: "generic.camera", isOnline: true,
            kind: .camera(CameraSpec(entityId: "switch.door_cam", isOn: true, streamEntityId: stream, snapshotPath: snapshot))
        )
    }

    @Test("The snapshot proxy path maps to HA's MJPEG proxy and keeps the access token")
    func mjpegProxyPathKeepsToken() {
        #expect(HAWebSocketProvider.mjpegProxyPath(fromSnapshotPath: "/api/camera_proxy/camera.door?token=abc123")
            == "/api/camera_proxy_stream/camera.door?token=abc123")
        #expect(HAWebSocketProvider.mjpegProxyPath(fromSnapshotPath: "https://ha.local:8123/api/camera_proxy/camera.door?token=t")
            == "https://ha.local:8123/api/camera_proxy_stream/camera.door?token=t")
        #expect(HAWebSocketProvider.mjpegProxyPath(fromSnapshotPath: "/local/door.jpg") == nil)
    }

    @Test("Without HLS, an HA camera entity plays as MJPEG instead of 1 fps snapshots")
    func cameraEntityFallsBackToMJPEG() async throws {
        let provider = HAWebSocketProvider(baseURLString: "http://192.168.1.10:8123", token: "t", onAddressSuggestion: nil)
        let source = try await provider.resolveCameraStream(
            for: camera(stream: "camera.door", snapshot: "/api/camera_proxy/camera.door?token=abc123")
        )
        #expect(source == .mjpeg(URL(string: "http://192.168.1.10:8123/api/camera_proxy_stream/camera.door?token=abc123")!))
    }

    @Test("A non-proxy snapshot path still plays as snapshots")
    func plainSnapshotStaysSnapshot() async throws {
        let provider = HAWebSocketProvider(baseURLString: "http://192.168.1.10:8123", token: "t", onAddressSuggestion: nil)
        let source = try await provider.resolveCameraStream(for: camera(snapshot: "/local/door.jpg"))
        #expect(source == .snapshot(URL(string: "http://192.168.1.10:8123/local/door.jpg")!))
    }

    @Test("A camera with no HA video still reports the 404 with a Mi sign-in hint")
    func noVideoThrows404() async {
        let provider = HAWebSocketProvider(baseURLString: "http://192.168.1.10:8123", token: "t", onAddressSuggestion: nil)
        do {
            _ = try await provider.resolveCameraStream(for: camera())
            Issue.record("expected a 404")
        } catch HomeProviderError.serviceCallFailed(let code, _) {
            #expect(code == 404)
        } catch {
            Issue.record("unexpected error \(error)")
        }
    }

    @Test("Mi sign-in is offered only for an HA camera with no video and no Mi account")
    func miSignInOffer() {
        let bare = CameraSpec(entityId: "switch.cam", isOn: true)
        #expect(bare.offersMiSignInForVideo(backend: .homeAssistant, hasMiSource: false))
        #expect(!bare.offersMiSignInForVideo(backend: .homeAssistant, hasMiSource: true))
        #expect(!bare.offersMiSignInForVideo(backend: .xiaomiCloud, hasMiSource: false))
        let streaming = CameraSpec(entityId: "switch.cam", isOn: true, streamEntityId: "camera.cam")
        #expect(!streaming.offersMiSignInForVideo(backend: .homeAssistant, hasMiSource: false))
        let snapshot = CameraSpec(entityId: "switch.cam", isOn: true, snapshotPath: "/api/camera_proxy/camera.cam?token=t")
        #expect(!snapshot.offersMiSignInForVideo(backend: .homeAssistant, hasMiSource: false))
    }
}
