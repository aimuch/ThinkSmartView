import Foundation
import Testing
@testable import MijiaCore
@testable import MenuBarMijiaApp

private actor FakePTZClient: CameraPTZControlling {
    private var beginContinuation: CheckedContinuation<(token: CameraPTZSessionToken, mode: CameraPTZMotionMode?), Error>?
    private var stopTokens: [CameraPTZSessionToken] = []
    var stopResult = CameraPTZStopResult(sent: true, confirmed: false, mode: .continuous)

    func begin() async throws -> (token: CameraPTZSessionToken, mode: CameraPTZMotionMode?) {
        try await withCheckedThrowingContinuation { continuation in
            beginContinuation = continuation
        }
    }

    func releaseBegin(_ token: CameraPTZSessionToken, mode: CameraPTZMotionMode = .unknown) {
        beginContinuation?.resume(returning: (token, mode))
        beginContinuation = nil
    }

    func move(direction: CameraPTZDirection, token: CameraPTZSessionToken, seq: UInt64) async throws {}
    func heartbeat(token: CameraPTZSessionToken, seq: UInt64) async throws {}

    func stop(token: CameraPTZSessionToken) async throws -> CameraPTZStopResult {
        stopTokens.append(token)
        return stopResult
    }

    func recordedStops() -> [CameraPTZSessionToken] { stopTokens }
    func hasPendingBegin() -> Bool { beginContinuation != nil }
}

@MainActor
@Suite("Camera PTZ driver lifecycle (#97)", .serialized)
struct CameraPTZDriverTests {
    private func binding() -> CameraPTZBinding {
        CameraPTZBinding(
            capability: CameraPTZCapability(
                model: "chuangmi.camera.061a01",
                vendor: "cs2",
                mode: .unknown,
                confidence: .pendingRealDevice
            ),
            streamName: "cam_raw_test"
        )
    }

    private func configure(
        _ controller: CameraLivePanelController,
        deviceId: String,
        client: FakePTZClient
    ) {
        controller.configurePTZPreview(
            deviceId: deviceId,
            binding: binding(),
            stream: .demo(deviceId: deviceId),
            notice: nil,
            ready: true,
            client: client,
            hasFrame: true
        )
    }

    private func waitForStops(_ client: FakePTZClient, count: Int = 1) async -> [CameraPTZSessionToken] {
        for _ in 0..<5000 {
            let stops = await client.recordedStops()
            if stops.count >= count { return stops }
            await Task.yield()
        }
        return await client.recordedStops()
    }

    @Test("A cancelled begin releases the bridge token instead of leaking it")
    func cancelledBeginReleasesToken() async {
        let controller = CameraLivePanelController.shared
        let client = FakePTZClient()
        configure(controller, deviceId: "cam-a", client: client)
        defer { controller.configurePTZPreview(deviceId: nil, binding: nil, stream: nil, notice: nil, ready: false) }

        controller.ptzDragBegan(at: .init(x: 0, y: 0))
        for _ in 0..<2000 where await !client.hasPendingBegin() {
            await Task.yield()
        }
        controller.ptzDragCancelled()
        let token = CameraPTZSessionToken(epoch: "e1", session: 9)
        await client.releaseBegin(token)

        let stops = await waitForStops(client)
        #expect(stops == [token])
        #expect(controller.ptzNotice == nil)
    }

    @Test("A begin that resolves after a device switch stops its old token and does not touch the new camera")
    func lateBeginAfterDeviceSwitchIsIsolated() async {
        let controller = CameraLivePanelController.shared
        let client = FakePTZClient()
        configure(controller, deviceId: "cam-a", client: client)

        controller.ptzDragBegan(at: .init(x: 0, y: 0))
        for _ in 0..<2000 where await !client.hasPendingBegin() {
            await Task.yield()
        }
        configure(controller, deviceId: "cam-b", client: client)
        defer { controller.configurePTZPreview(deviceId: nil, binding: nil, stream: nil, notice: nil, ready: false) }

        let token = CameraPTZSessionToken(epoch: "e1", session: 9)
        await client.releaseBegin(token)

        let stops = await waitForStops(client)
        #expect(stops == [token])
        #expect(controller.ptzNotice == nil, "the old stream's error must not paint the new camera")
    }

    @Test("A stop write failure is visible even when the caller asked for a silent close")
    func stopFailureIsVisibleOnTheSameCamera() async {
        let controller = CameraLivePanelController.shared
        let client = FakePTZClient()
        await client.setStopResult(CameraPTZStopResult(
            sent: false,
            confirmed: false,
            mode: .continuous,
            errorReason: "write_failed"
        ))
        configure(controller, deviceId: "cam-a", client: client)
        defer { controller.configurePTZPreview(deviceId: nil, binding: nil, stream: nil, notice: nil, ready: false) }

        controller.ptzToken = CameraPTZSessionToken(epoch: "e1", session: 9)
        controller.ptzDragID = UUID()
        controller.finishPTZMove(notify: false)
        let stops = await waitForStops(client)
        #expect(stops.count == 1)
        for _ in 0..<50 where controller.ptzNotice == nil {
            await Task.yield()
        }
        #expect(controller.ptzNotice?.contains("停止指令写入失败") == true)
    }
}

private extension FakePTZClient {
    func setStopResult(_ result: CameraPTZStopResult) {
        stopResult = result
    }
}
