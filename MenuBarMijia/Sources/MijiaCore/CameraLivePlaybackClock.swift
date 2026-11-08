import Foundation

public struct CameraLivePlaybackClock: Equatable, Sendable {
    public var maxSkewSeconds: Double
    private var anchorMediaSeconds: Double?
    private var anchorHostSeconds: Double?

    public init(maxSkewSeconds: Double = 0.5) {
        self.maxSkewSeconds = maxSkewSeconds
    }

    public mutating func reset() {
        anchorMediaSeconds = nil
        anchorHostSeconds = nil
    }

    public func timebaseSeconds(hostNowSeconds: Double) -> Double? {
        guard let anchorMediaSeconds, let anchorHostSeconds, hostNowSeconds.isFinite else { return nil }
        return anchorMediaSeconds + (hostNowSeconds - anchorHostSeconds)
    }

    public enum Move: Equatable, Sendable {
        case start(Double)
        case reanchor(Double)
        case follow
    }

    public mutating func step(mediaSeconds: Double, hostNowSeconds: Double) -> Move {
        guard mediaSeconds.isFinite, hostNowSeconds.isFinite else { return .follow }
        guard let timebase = timebaseSeconds(hostNowSeconds: hostNowSeconds) else {
            anchorMediaSeconds = mediaSeconds
            anchorHostSeconds = hostNowSeconds
            return .start(mediaSeconds)
        }
        if abs(mediaSeconds - timebase) > maxSkewSeconds {
            anchorMediaSeconds = mediaSeconds
            anchorHostSeconds = hostNowSeconds
            return .reanchor(mediaSeconds)
        }
        return .follow
    }
}
