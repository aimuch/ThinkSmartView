import Foundation

// MARK: - Away arbitration

/// One system notification that can contribute to "the user stepped away".
///
/// macOS posts these separately and never says which physical action caused them, so the trigger
/// the user configured cannot be matched to a notification one-to-one. `AwayArbitrator` is the only
/// place that decides which of them a burst of notifications actually was.
public enum AwaySignal: Equatable, Sendable {
    /// `com.apple.screenIsLocked` (distributed notification).
    case screenLocked
    /// `com.apple.screenIsUnlocked`.
    case screenUnlocked
    /// `NSWorkspace.willSleepNotification` — the machine is about to suspend.
    case willSleep
    /// `NSWorkspace.screensDidSleepNotification` — only the display turned off.
    case displayDidSleep
}

/// Which precise trigger an away burst resolved to.
public enum AwayOutcome: Equatable, Sendable {
    /// Locked, and no sleep followed within the debounce window.
    case screenLockedOnly
    /// The lid was closed and the machine slept.
    case lidClosedSleep
    /// The machine slept with the lid open.
    case systemSleep

    public var trigger: AutomationTrigger {
        switch self {
        case .screenLockedOnly: return .screenLocked
        case .lidClosedSleep: return .lidClosedSleep
        case .systemSleep: return .systemSleep
        }
    }
}

/// Turns a burst of lock/sleep notifications into exactly one precise outcome.
///
/// ## Why the two signals are handled asymmetrically
///
/// A lid close posts the lock notification first and `willSleep` a moment later, and the machine
/// suspends almost immediately after `willSleep`. So:
///
/// - **Lock is held** for `lockDebounce` seconds before it is reported as `.screenLockedOnly`. That
///   delay is what makes "only locked" distinguishable from "closed the lid" at all.
/// - **Sleep is never held.** It is reported immediately, classified by the clamshell state read at
///   that instant. Delaying a sleep event by the debounce would mean the app tries to talk to the
///   cloud while the machine is already suspending, and the command is lost — the upstream engine
///   already documents that failure mode.
///
/// The result is order-independent: whichever signal arrives first, a lock+sleep burst ends up as a
/// sleep outcome, and a lone lock ends up as `.screenLockedOnly`.
public struct AwayArbitrator: Equatable, Sendable {
    /// How long a lock is held before it is reported as "locked only".
    public static let lockDebounce: TimeInterval = 2
    /// A lock arriving this soon after a sleep belongs to the same away burst, not to a new one.
    public static let postSleepSuppression: TimeInterval = 3

    private var pendingLockAt: Date?
    private var lastSleepAt: Date?

    public init() {}

    /// When non-nil, the caller must call `flush(at:)` at this instant; that call is what reports
    /// `.screenLockedOnly`. Kept as a deadline (not a timer) so the arbitration stays pure.
    public var pendingDeadline: Date? {
        pendingLockAt.map { $0.addingTimeInterval(Self.lockDebounce) }
    }

    public var hasPendingLock: Bool { pendingLockAt != nil }

    /// Feeds one notification.
    /// - Parameter lidClosed: `AppleClamshellState` read at the moment the signal arrived.
    /// - Returns: an outcome when this signal settles the question, otherwise `nil`.
    public mutating func ingest(_ signal: AwaySignal, lidClosed: Bool, at now: Date) -> AwayOutcome? {
        switch signal {
        case .screenLocked:
            if recentlySlept(at: now) {
                // The lock is a consequence of the sleep we already reported.
                return nil
            }
            if pendingLockAt == nil {
                pendingLockAt = now
            }
            return nil

        case .willSleep:
            lastSleepAt = now
            // The pending lock is claimed by this sleep; its flush must not fire afterwards.
            pendingLockAt = nil
            return lidClosed ? .lidClosedSleep : .systemSleep

        case .displayDidSleep:
            // Display-only sleep is not a machine sleep. `away` still covers it.
            return nil

        case .screenUnlocked:
            pendingLockAt = nil
            return nil
        }
    }

    /// Called when `pendingDeadline` is reached. Reports `.screenLockedOnly` if the lock was not
    /// claimed by a sleep in the meantime.
    public mutating func flush(at now: Date) -> AwayOutcome? {
        guard let pending = pendingLockAt else { return nil }
        let elapsed = now.timeIntervalSince(pending)
        // A backwards clock jump must not strand the pending lock forever.
        let isDue = elapsed < 0 || elapsed >= Self.lockDebounce
        guard isDue else { return nil }
        pendingLockAt = nil
        if recentlySlept(at: now) { return nil }
        return .screenLockedOnly
    }

    private func recentlySlept(at now: Date) -> Bool {
        guard let lastSleepAt else { return false }
        let elapsed = now.timeIntervalSince(lastSleepAt)
        return elapsed >= 0 && elapsed < Self.postSleepSuppression
    }
}
