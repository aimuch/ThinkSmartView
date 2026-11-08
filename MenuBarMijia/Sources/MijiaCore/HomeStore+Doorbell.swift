import Foundation

extension HomeStore {
    public static let doorbellPopupSecondsRange = 15...300
    static let recentDoorbellRingLimit = 20

    /// Records every press once. The chime and the camera window are separate choices: the chime
    /// plays whenever it is on; the camera opens only when that is on, the screen-lock rule allows
    /// it, and the ringing device has an online camera card (#308).
    func handleDoorbellRing(_ ring: DoorbellRing) {
        guard !recentDoorbellRings.contains(where: { $0.key == ring.key }) else { return }
        recentDoorbellRings.insert(ring, at: 0)
        if recentDoorbellRings.count > Self.recentDoorbellRingLimit {
            recentDoorbellRings.removeLast(recentDoorbellRings.count - Self.recentDoorbellRingLimit)
        }
        var cameraId: String?
        if doorbellAutoPopup, !isControlBlockedByScreenLock,
           let device = devices.first(where: { $0.id == ring.deviceId }),
           device.isOnline, case .camera = device.kind {
            cameraId = device.id
        }
        guard cameraId != nil || doorbellChime else { return }
        let seconds = doorbellPopupSeconds.clamped(to: Self.doorbellPopupSecondsRange)
        onDoorbellRing?(DoorbellResponse(cameraDeviceId: cameraId, playsChime: doorbellChime, dismissAfter: .seconds(seconds)))
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
