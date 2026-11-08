import Foundation

/// One pan/tilt step direction understood by the camera's P2P motor command.
public enum CameraPTZDirection: String, CaseIterable, Equatable, Sendable {
    case left
    case right
    case up
    case down
}

/// How one camera model reacts to `cmdMotorReq 0x112`.
public enum CameraPTZMotionMode: String, Equatable, Sendable {
    /// One operation moves a bounded step and the motor stops by itself.
    case step
    /// One operation keeps the motor running until a stop operation.
    case continuous
    /// Hardware is documented but the motion mode has not been verified on a real unit.
    case unknown
}

/// What the app knows about one camera's physical gimbal.
public struct CameraPTZCapability: Equatable, Sendable {
    public enum Confidence: String, Equatable, Sendable {
        case verified
        /// The model is documented as a PTZ product, but no one has run the motor command on it.
        case pendingRealDevice
    }

    public var model: String
    public var vendor: String
    public var mode: CameraPTZMotionMode
    public var confidence: Confidence
    public var supportsPan: Bool
    public var supportsTilt: Bool

    public init(
        model: String,
        vendor: String,
        mode: CameraPTZMotionMode,
        confidence: Confidence,
        supportsPan: Bool = true,
        supportsTilt: Bool = true
    ) {
        self.model = model
        self.vendor = vendor
        self.mode = mode
        self.confidence = confidence
        self.supportsPan = supportsPan
        self.supportsTilt = supportsTilt
    }

    /// An evidence table, not a brand rule: `chuangmi` has both motorised and fixed cameras.
    /// A model outside this table keeps the plain viewer.
    public static func resolve(model: String, vendor: String) -> CameraPTZCapability? {
        let key = model.lowercased()

        // The two Dafang-era cameras use the TUTK transport and a raw 0xff2404 command that runs
        // until an explicit stop. Other TUTK models are not implemented.
        if vendor == "tutk" {
            if key == "isa.camera.df3" || key == "isa.camera.isc5c1" {
                return CameraPTZCapability(model: model, vendor: vendor, mode: .continuous, confidence: .verified)
            }
            return nil
        }
        guard vendor == "cs2" else { return nil }

        let stepModels: Set<String> = [
            "chuangmi.camera.029a02",
            "isa.camera.hlc8",
            "isa.camera.hlc8a",
            "isa.camera.500dh",
            "isa.camera.hlmax",
        ]
        let continuousModels: Set<String> = [
            "xiaomi.camera.c01a01",
            "chuangmi.camera.72ac1",
            "chuangmi.camera.039a04",
            "chuangmi.camera.039c04",
        ]
        let pendingModels: Set<String> = [
            "chuangmi.camera.061a01",
            "chuangmi.camera.061a03",
            "chuangmi.camera.069a01",
            "chuangmi.camera.039c01",
            "chuangmi.camera.021a04",
            "chuangmi.camera.026c02",
            "chuangmi.camera.ipc019",
            "chuangmi.camera.ip029a",
        ]

        if stepModels.contains(key) {
            return CameraPTZCapability(model: model, vendor: vendor, mode: .step, confidence: .verified)
        }
        if continuousModels.contains(key) {
            return CameraPTZCapability(model: model, vendor: vendor, mode: .continuous, confidence: .verified)
        }
        if pendingModels.contains(key) {
            return CameraPTZCapability(model: model, vendor: vendor, mode: .unknown, confidence: .pendingRealDevice)
        }
        return nil
    }
}

/// The capability plus the go2rtc stream name that carries the already-open P2P session.
public struct CameraPTZBinding: Equatable, Sendable {
    public var capability: CameraPTZCapability
    public var streamName: String

    public init(capability: CameraPTZCapability, streamName: String) {
        self.capability = capability
        self.streamName = streamName
    }
}

/// A drag point in view coordinates: x grows right, y grows down.
public struct CameraPTZPoint: Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

/// Turns one held drag into motor intents. Pure and time-injected so the thresholds, direction
/// changes, heartbeat cadence and hold cap are testable without sleeping.
public struct CameraPTZDragPlanner: Sendable {
    public struct Timing: Sendable {
        /// A press must travel this far before any motor command is sent.
        public var activationDistance: Double
        /// Switching axis needs the new axis to dominate by this fraction.
        public var axisHysteresis: Double
        /// The bridge stops a move that is not renewed within its lease.
        public var heartbeatInterval: TimeInterval
        /// One held drag can never run longer than this.
        public var maxHold: TimeInterval

        public init(
            activationDistance: Double = 4,
            axisHysteresis: Double = 0.25,
            heartbeatInterval: TimeInterval = 0.25,
            maxHold: TimeInterval = 8
        ) {
            self.activationDistance = activationDistance
            self.axisHysteresis = axisHysteresis
            self.heartbeatInterval = heartbeatInterval
            self.maxHold = maxHold
        }
    }

    public enum Command: Equatable, Sendable {
        case move(CameraPTZDirection)
        case heartbeat
        case stop
    }

    public private(set) var isActive = false
    public private(set) var activeDirection: CameraPTZDirection?

    private let timing: Timing
    private var start: CameraPTZPoint?
    private var beganAt: TimeInterval = 0
    private var lastCommandAt: TimeInterval = 0

    public init(timing: Timing = Timing()) {
        self.timing = timing
    }

    public mutating func began(at point: CameraPTZPoint, time: TimeInterval) {
        start = point
        beganAt = time
        lastCommandAt = time
        isActive = false
        activeDirection = nil
    }

    public mutating func moved(
        to point: CameraPTZPoint,
        time: TimeInterval,
        capability: CameraPTZCapability
    ) -> [Command] {
        guard let start else { return [] }
        if isActive, time - beganAt >= timing.maxHold {
            reset()
            return [.stop]
        }

        let translation = CameraPTZPoint(x: point.x - start.x, y: point.y - start.y)
        if !isActive, hypot(translation.x, translation.y) < timing.activationDistance {
            return []
        }
        guard let direction = direction(for: translation, capability: capability) else { return [] }

        if !isActive {
            isActive = true
            activeDirection = direction
            lastCommandAt = time
            return [.move(direction)]
        }
        if direction != activeDirection {
            activeDirection = direction
            lastCommandAt = time
            return [.move(direction)]
        }
        return heartbeatIfDue(time: time)
    }

    public mutating func heartbeat(time: TimeInterval) -> [Command] {
        guard isActive else { return [] }
        if time - beganAt >= timing.maxHold {
            reset()
            return [.stop]
        }
        return heartbeatIfDue(time: time)
    }

    public mutating func ended() -> [Command] {
        let wasActive = isActive
        reset()
        return wasActive ? [.stop] : []
    }

    public mutating func cancel() -> [Command] {
        ended()
    }

    private mutating func heartbeatIfDue(time: TimeInterval) -> [Command] {
        guard time - lastCommandAt >= timing.heartbeatInterval else { return [] }
        lastCommandAt = time
        return [.heartbeat]
    }

    private func direction(
        for translation: CameraPTZPoint,
        capability: CameraPTZCapability
    ) -> CameraPTZDirection? {
        let horizontal = capability.supportsPan ? abs(translation.x) : 0
        let vertical = capability.supportsTilt ? abs(translation.y) : 0
        guard horizontal > 0 || vertical > 0 else { return nil }

        let horizontalDominant = horizontal >= vertical
        if let previous = activeDirection {
            switch previous {
            case .left, .right:
                if horizontalDominant { return translation.x < 0 ? .left : .right }
                guard vertical >= horizontal * (1 + timing.axisHysteresis) else { return previous }
            case .up, .down:
                if !horizontalDominant { return translation.y < 0 ? .up : .down }
                guard horizontal >= vertical * (1 + timing.axisHysteresis) else { return previous }
            }
        }

        if horizontalDominant {
            if abs(translation.x) < timing.activationDistance { return activeDirection }
            return translation.x < 0 ? .left : .right
        }
        if abs(translation.y) < timing.activationDistance { return activeDirection }
        return translation.y < 0 ? .up : .down
    }

    private mutating func reset() {
        start = nil
        isActive = false
        activeDirection = nil
        beganAt = 0
        lastCommandAt = 0
    }
}

/// One request in flight, one newest pending intent. A slow bridge coalesces drags instead of
/// replaying historical directions, but a heartbeat can never replace a direction that has not
/// been sent yet: the user's latest direction wins over lease renewal.
public enum CameraPTZQueuedCommand: Equatable, Sendable {
    case move(CameraPTZDirection, UInt64)
    case heartbeat(UInt64)
}

public struct CameraPTZCommandQueue: Sendable {
    public private(set) var inFlight = false
    public private(set) var pending: CameraPTZQueuedCommand?

    public init() {}

    /// Returns the command to send now, or nil when it was coalesced into the pending slot.
    public mutating func submit(_ command: CameraPTZQueuedCommand) -> CameraPTZQueuedCommand? {
        if inFlight {
            if case .heartbeat = command, case .move = pending {
                return nil
            }
            pending = command
            return nil
        }
        inFlight = true
        return command
    }

    /// Called when the in-flight command finished. Returns the newest pending command.
    public mutating func complete() -> CameraPTZQueuedCommand? {
        if let pending {
            self.pending = nil
            inFlight = true
            return pending
        }
        inFlight = false
        return nil
    }

    public mutating func reset() {
        pending = nil
        inFlight = false
    }
}
