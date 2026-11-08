import Foundation
import Testing
@testable import MijiaCore

/// Away arbitration: turning one burst of lock/sleep notifications into exactly one precise trigger.
///
/// The scenarios marked "regression" reproduce the behaviour that motivated the split — a single
/// coarse `away` trigger cannot tell a lid close from a plain lock.
struct AwayArbitratorTests {
    private let start = Date(timeIntervalSince1970: 1_800_000_000)

    // MARK: Lock

    @Test func plainLockWaitsForTheDebounceThenFires() {
        var arbiter = AwayArbitrator()
        #expect(arbiter.ingest(.screenLocked, lidClosed: false, at: start) == nil)
        #expect(arbiter.hasPendingLock)

        // Still inside the window: nothing is reported yet.
        #expect(arbiter.flush(at: start.addingTimeInterval(1.9)) == nil)
        #expect(arbiter.hasPendingLock)

        #expect(arbiter.flush(at: start.addingTimeInterval(2.0)) == .screenLockedOnly)
        // The pending lock is consumed; a second flush must not fire again.
        #expect(arbiter.flush(at: start.addingTimeInterval(2.5)) == nil)
    }

    @Test func unlockCancelsThePendingLock() {
        var arbiter = AwayArbitrator()
        _ = arbiter.ingest(.screenLocked, lidClosed: false, at: start)
        _ = arbiter.ingest(.screenUnlocked, lidClosed: false, at: start.addingTimeInterval(0.5))
        #expect(arbiter.flush(at: start.addingTimeInterval(5)) == nil)
    }

    // MARK: Sleep

    @Test func lidCloseReportsLidSleepImmediately() {
        var arbiter = AwayArbitrator()
        #expect(arbiter.ingest(.screenLocked, lidClosed: true, at: start) == nil)
        #expect(arbiter.ingest(.willSleep, lidClosed: true, at: start.addingTimeInterval(0.4)) == .lidClosedSleep)
        // The held lock was claimed by the sleep and must not fire later.
        #expect(arbiter.flush(at: start.addingTimeInterval(3)) == nil)
    }

    @Test func sleepWithLidOpenIsSystemSleep() {
        var arbiter = AwayArbitrator()
        #expect(arbiter.ingest(.willSleep, lidClosed: false, at: start) == .systemSleep)
    }

    @Test func sleepBeforeLockStillSettlesOnce() {
        var arbiter = AwayArbitrator()
        // Some Macs post the sleep notification first.
        #expect(arbiter.ingest(.willSleep, lidClosed: true, at: start) == .lidClosedSleep)
        // The lock that follows belongs to the same away burst.
        #expect(arbiter.ingest(.screenLocked, lidClosed: true, at: start.addingTimeInterval(1)) == nil)
        #expect(arbiter.flush(at: start.addingTimeInterval(10)) == nil)
    }

    @Test func displaySleepAloneProducesNothing() {
        var arbiter = AwayArbitrator()
        #expect(arbiter.ingest(.displayDidSleep, lidClosed: false, at: start) == nil)
        #expect(arbiter.flush(at: start.addingTimeInterval(10)) == nil)
    }

    @Test func lockLongAfterASleepStartsANewWait() {
        var arbiter = AwayArbitrator()
        _ = arbiter.ingest(.willSleep, lidClosed: false, at: start)
        // Well past the suppression window: this is a separate lock, not part of the sleep.
        #expect(arbiter.ingest(.screenLocked, lidClosed: false, at: start.addingTimeInterval(10)) == nil)
        #expect(arbiter.flush(at: start.addingTimeInterval(12)) == .screenLockedOnly)
    }

    @Test func backwardsClockDoesNotStrandThePendingLock() {
        var arbiter = AwayArbitrator()
        _ = arbiter.ingest(.screenLocked, lidClosed: false, at: start)
        #expect(arbiter.flush(at: start.addingTimeInterval(-30)) == .screenLockedOnly)
    }

    // MARK: Regression — what the single `away` trigger could not do

    /// The old engine mapped `screenIsLocked` and `willSleep` to the same trigger, so one rule fired
    /// for both physical actions. The arbiter now returns two different outcomes for the same two
    /// notification sequences, which is the whole point of the split.
    @Test func lidCloseAndPlainLockAreNoLongerTheSameOutcome() {
        func outcome(lidClosed: Bool, sleepFollows: Bool) -> AwayOutcome? {
            var arbiter = AwayArbitrator()
            _ = arbiter.ingest(.screenLocked, lidClosed: lidClosed, at: start)
            if sleepFollows {
                return arbiter.ingest(.willSleep, lidClosed: lidClosed, at: start.addingTimeInterval(0.4))
            }
            return arbiter.flush(at: start.addingTimeInterval(5))
        }

        #expect(outcome(lidClosed: true, sleepFollows: true) == .lidClosedSleep)
        #expect(outcome(lidClosed: false, sleepFollows: false) == .screenLockedOnly)
        // And the precise outcomes map onto distinct triggers.
        #expect(AwayOutcome.lidClosedSleep.trigger == .lidClosedSleep)
        #expect(AwayOutcome.screenLockedOnly.trigger == .screenLocked)
        #expect(AwayOutcome.systemSleep.trigger == .systemSleep)
    }
}
