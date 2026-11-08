import AppKit
import MijiaCore
import SwiftUI

// PTZ drag driver, split from CameraLivePanelController.swift to keep that file under the
// project's 1500-line guidance.
extension CameraLivePanelController {
    // MARK: - PTZ Drag

    public var isPTZEnabled: Bool {
        ptzClientReady
            && ptzBinding != nil
            && hasLiveVideoFrame
            && !isPictureInPictureActive
    }

    var ptzTime: TimeInterval { ProcessInfo.processInfo.systemUptime }

    public func ptzDragBegan(at point: CameraPTZPoint) {
        guard isPTZEnabled, let client = ptzClient, let deviceId = activeDeviceId else { return }
        finishPTZMove()
        let dragID = UUID()
        ptzDragID = dragID
        ptzDeviceId = deviceId
        ptzPlanner = CameraPTZDragPlanner()
        ptzPlanner.began(at: point, time: ptzTime)
        ptzSeq = 0
        ptzPendingDirection = nil
        let previousStop = ptzStopTask
        ptzBeginTask = Task { @MainActor [weak self] in
            // A new drag must not begin before the previous stop reached the bridge; otherwise
            // begin sees the old session active and fails (or races its stop).
            await previousStop?.value
            do {
                let begin = try await client.begin()
                if Task.isCancelled {
                    try? await client.stop(token: begin.token)
                    return
                }
                guard let self, self.ptzDragID == dragID else {
                    try? await client.stop(token: begin.token)
                    return
                }
                self.ptzToken = begin.token
                self.ptzBeginTask = nil
                if let direction = self.ptzPendingDirection {
                    self.ptzPendingDirection = nil
                    self.enqueueMove(direction)
                }
            } catch {
                guard let self, !Task.isCancelled, self.ptzDragID == dragID else { return }
                self.showPTZNotice(error.localizedDescription)
                self.finishPTZMove(notify: false)
            }
        }
    }

    public func ptzDragMoved(to point: CameraPTZPoint) {
        guard let capability = ptzBinding?.capability, ptzDragID != nil else { return }
        let commands = ptzPlanner.moved(to: point, time: ptzTime, capability: capability)
        for command in commands {
            switch command {
            case .move(let direction):
                if ptzToken == nil {
                    ptzPendingDirection = direction
                } else {
                    ptzPendingDirection = nil
                    enqueueMove(direction)
                }
                startPTZHeartbeat()
            case .heartbeat:
                if ptzToken != nil {
                    enqueueHeartbeat()
                }
            case .stop:
                finishPTZMove()
            }
        }
    }

    public func ptzDragEnded() {
        finishPTZMove()
    }

    public func ptzDragCancelled() {
        finishPTZMove()
    }

    func enqueueMove(_ direction: CameraPTZDirection) {
        ptzSeq &+= 1
        enqueue(.move(direction, ptzSeq))
    }

    func enqueueHeartbeat() {
        ptzSeq &+= 1
        enqueue(.heartbeat(ptzSeq))
    }

    /// One request in flight and only the newest pending intent. A slow network coalesces the
    /// drag instead of replaying old directions after the user already released.
    func enqueue(_ command: CameraPTZQueuedCommand) {
        guard let next = ptzQueue.submit(command) else { return }
        run(next)
    }

    func run(_ command: CameraPTZQueuedCommand) {
        guard let client = ptzClient, let token = ptzToken, let dragID = ptzDragID else { return }
        Task { @MainActor [weak self] in
            do {
                switch command {
                case .move(let direction, let seq):
                    try await client.move(direction: direction, token: token, seq: seq)
                case .heartbeat(let seq):
                    try await client.heartbeat(token: token, seq: seq)
                }
            } catch {
                guard let self, self.ptzDragID == dragID else { return }
                self.showPTZNotice(error.localizedDescription)
                self.finishPTZMove()
            }
            guard let self, self.ptzDragID == dragID else { return }
            if let next = self.ptzQueue.complete() {
                self.run(next)
            }
        }
    }

    func startPTZHeartbeat() {
        guard ptzHeartbeatTask == nil else { return }
        let dragID = ptzDragID
        ptzHeartbeatTask = Task { @MainActor [weak self] in
            defer {
                if let self, self.ptzDragID == dragID {
                    self.ptzHeartbeatTask = nil
                }
            }
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(250))
                guard let self, !Task.isCancelled, self.ptzDragID == dragID else { return }
                let commands = self.ptzPlanner.heartbeat(time: self.ptzTime)
                if commands.contains(.stop) {
                    self.finishPTZMove()
                    return
                }
                if commands.contains(.heartbeat), self.ptzToken != nil {
                    self.enqueueHeartbeat()
                }
            }
        }
    }

    /// Cancels the heartbeat and releases the bridge session first, then stops. The stop is
    /// attempted even when the move task failed. Any result is guarded by the device that
    /// started the drag, so an old stop cannot put a notice over a newly opened camera.
    func finishPTZMove(notify: Bool = true) {
        ptzBeginTask?.cancel()
        ptzBeginTask = nil
        ptzHeartbeatTask?.cancel()
        ptzHeartbeatTask = nil
        ptzQueue.reset()
        ptzPendingDirection = nil
        ptzDragID = nil
        let token = ptzToken
        ptzToken = nil
        let deviceId = ptzDeviceId
        ptzDeviceId = nil
        let lifecycleID = ptzLifecycleID
        _ = ptzPlanner.cancel()
        guard let token, let client = ptzClient else { return }
        let stopTask = Task { @MainActor [weak self] in
            do {
                let result = try await client.stop(token: token)
                guard let self,
                      self.activeDeviceId == deviceId,
                      self.ptzLifecycleID == lifecycleID else { return }
                if result.errorReason != nil {
                    self.showPTZNotice(L10n.tr("停止指令写入失败，云台可能仍在转动", "Stop write failed; the camera may still be moving"))
                    return
                }
                guard notify, self.ptzDragID == nil else { return }
                if result.mode != .step, !result.sent {
                    self.showPTZNotice(L10n.tr("松手后未发出停止指令", "Release stop was not sent"))
                }
            } catch {
                guard let self,
                      self.activeDeviceId == deviceId,
                      self.ptzLifecycleID == lifecycleID else { return }
                self.showPTZNotice(error.localizedDescription)
            }
        }
        ptzStopTask = stopTask
    }

    func showPTZNotice(_ text: String?) {
        guard let text else { return }
        ptzNotice = text
        ptzNoticeTask?.cancel()
        ptzNoticeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            self?.ptzNotice = nil
        }
    }

    /// Losing key focus, entering PiP or hiding the panel must not leave a held motor running.
    public func windowDidResignKey(_ notification: Notification) {
        finishPTZMove()
    }

    /// Snapshot/test seam: puts the panel into a PTZ state without a bridge, a stream or any
    /// network request. Production only reaches these states through `startLiveVideo`.
    func configurePTZPreview(
        store: HomeStore? = nil,
        deviceId: String?,
        binding: CameraPTZBinding?,
        stream: CameraStreamSource?,
        notice: String?,
        ready: Bool,
        client: (any CameraPTZControlling)? = nil,
        hasFrame: Bool = true
    ) {
        ptzBeginTask?.cancel()
        ptzBeginTask = nil
        ptzHeartbeatTask?.cancel()
        ptzHeartbeatTask = nil
        ptzNoticeTask?.cancel()
        ptzNoticeTask = nil
        ptzQueue.reset()
        ptzDragID = nil
        ptzToken = nil
        ptzClient = client
        if let store {
            self.store = store
        }
        activeDeviceId = deviceId
        streamSource = stream
        hasLiveVideoFrame = hasFrame && stream != nil
        ptzBinding = binding
        ptzClientReady = ready
        ptzNotice = notice
        ptzLifecycleID = UUID()
        demoTimestampOverride = deviceId == nil ? nil : Date(timeIntervalSince1970: 1_700_000_000)
    }

    func clearPTZ() {
        finishPTZMove(notify: false)
        ptzLifecycleID = UUID()
        ptzBinding = nil
        ptzClient = nil
        ptzClientReady = false
        ptzNoticeTask?.cancel()
        ptzNoticeTask = nil
        ptzNotice = nil
    }

}
