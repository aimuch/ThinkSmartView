import Foundation

/// One press of a doorbell, from a Home Assistant `event` entity with `device_class: doorbell` (#308).
/// HA sets the entity's state to the event timestamp on every press.
public struct DoorbellRing: Equatable, Sendable {
    /// The card that owns the doorbell: the HA device id, translated to the merged card id in dual-source mode.
    public var deviceId: String
    public var entityId: String
    public var firedAt: String

    public init(deviceId: String, entityId: String, firedAt: String) {
        self.deviceId = deviceId
        self.entityId = entityId
        self.firedAt = firedAt
    }

    /// Identifies this press; the same press is handled at most once.
    public var key: String { "\(entityId)@\(firedAt)" }

    /// The press timestamp a doorbell event entity reports, or nil for `unknown`/`unavailable`.
    static func timestamp(_ state: HAEntityState) -> String? {
        guard isDoorbellEvent(state), !["unknown", "unavailable", ""].contains(state.state) else { return nil }
        return state.state
    }

    static func isDoorbellEvent(_ state: HAEntityState) -> Bool {
        state.entityId.hasPrefix("event.") && state.attributes["device_class"]?.stringValue == "doorbell"
    }
}

/// What the app does for one press after the user's settings and the lock screen are applied.
public struct DoorbellResponse: Equatable, Sendable {
    /// The camera card to open, or nil when only the chime should play.
    public var cameraDeviceId: String?
    public var playsChime: Bool
    public var dismissAfter: Duration
}
