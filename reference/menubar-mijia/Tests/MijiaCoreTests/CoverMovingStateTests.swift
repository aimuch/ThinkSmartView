import Foundation
import Testing
@testable import MijiaCore

/// #298: a cover card shows the motor's real travel, snaps parked positions to the ends, and keeps
/// the commanded target while the motor reports intermediate positions.
///
/// The three shapes below are verbatim released miot-spec.org instances: `lumi.curtain.hagl05`
/// publishes `status`, current and target; `090615.curtain.1mcu01` publishes current and target
/// without `status`; `kongke.curtain.dooya` publishes only target.
struct CoverMovingStateTests {
    static let statusModel = "lumi.curtain.hagl05"
    static let positionModel = "090615.curtain.1mcu01"
    static let targetOnlyModel = "kongke.curtain.dooya"
    static let terminalStatusModel = "bymiot.curtain.v2"

    // MARK: Fixtures

    private func spec(_ model: String) throws -> MiotDeviceSpec {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let fixture = directory.appendingPathComponent("Tests/Fixtures/miot/\(model).json")
            if FileManager.default.fileExists(atPath: fixture.path) {
                return try JSONDecoder().decode(MiotDeviceSpec.self, from: Data(contentsOf: fixture))
            }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func coverDevice(
        model: String = CoverMovingStateTests.statusModel,
        did: String = "cover1",
        name: String = "客厅窗帘",
        current: Int? = nil,
        target: Int? = nil,
        status: Int? = nil
    ) throws -> SmartDevice {
        let spec = try spec(model)
        let record = MiDeviceRecord(did: did, name: name, model: model, isOnline: true)
        var values: [MiotPropKey: JSONValue] = [:]
        switch model {
        case Self.statusModel:
            if let current { values[.init(did: did, siid: 2, piid: 3)] = .number(Double(current)) }
            if let target { values[.init(did: did, siid: 2, piid: 7)] = .number(Double(target)) }
            if let status { values[.init(did: did, siid: 2, piid: 6)] = .number(Double(status)) }
        case Self.positionModel:
            if let current { values[.init(did: did, siid: 2, piid: 6)] = .number(Double(current)) }
            if let target { values[.init(did: did, siid: 2, piid: 7)] = .number(Double(target)) }
        case Self.targetOnlyModel:
            if let target { values[.init(did: did, siid: 1, piid: 7)] = .number(Double(target)) }
        case Self.terminalStatusModel:
            if let current { values[.init(did: did, siid: 2, piid: 4)] = .number(Double(current)) }
            if let target { values[.init(did: did, siid: 2, piid: 5)] = .number(Double(target)) }
            if let status { values[.init(did: did, siid: 2, piid: 3)] = .number(Double(status)) }
        default:
            Issue.record("no property map for \(model)")
        }
        let home = MiotSynthesizer.synthesize(
            devices: [record], rooms: [], specs: [model: spec], values: values
        )
        return try #require(home.devices.first)
    }

    // MARK: Status word table

    @Test("Travel words classify after punctuation is removed, including the typos real specs ship")
    func statusWordsClassifyTravel() {
        for word in ["Opening", "Up", "uping", "Rise", "Rising", " opening! ", "打开中"] {
            #expect(CoverStatusWord(word) == .opening, "\(word) should be opening")
        }
        for word in [
            "Closing", "Down", "dowm", "Falling", "fallin",
            "Dropping", "downing", "lower", "关闭中",
        ] {
            #expect(CoverStatusWord(word) == .closing, "\(word) should be closing")
        }
        for word in ["Open", "open", "Opened", "stop at highest", "Ceiling", "完全打开"] {
            #expect(CoverStatusWord(word) == .open, "\(word) should be the open end")
        }
        for word in [
            "Close", "close", "Closed", "close over", "Stop at Lowest", "stop-lower-limit",
            "lower limit stop", "Floor", "lower limit",
        ] {
            #expect(CoverStatusWord(word) == .closed, "\(word) should be the closed end")
        }
        for word in [
            "Stop", "Stopped", "Pause", "paused", "Idle", "Calibration",
            "Partial-open", "Partial closed", "暂停", "停止",
        ] {
            #expect(CoverStatusWord(word) == .stopped, "\(word) should be an explicit rest")
        }
        #expect(CoverStatusWord("Remote") == nil)
        #expect(CoverStatusWord(nil) == nil)
    }

    @Test("Status words compare with their letters only, so case and separators do not matter")
    func statusWordsNormalizeSeparators() {
        #expect(CoverStatusWord(" s t o p   a t   l o w e s t ") == .closed)
        #expect(CoverStatusWord("D-O-W-M") == .closing)
        #expect(CoverStatusWord("R_I_S_I_N_G") == .opening)
        #expect(CoverStatusWord("O-P-E-N") == .open)
        #expect(CoverStatusWord(" P A U S E ") == .stopped)
    }

    // MARK: Three spec shapes

    @Test("A spec with status reports opening and closing from that property")
    func statusDrivesMotion() throws {
        let opening = try coverProperty(statusModel: Self.statusModel, current: 40, target: 40, status: 1)
        let closing = try coverProperty(statusModel: Self.statusModel, current: 60, target: 60, status: 2)
        let stopped = try coverProperty(statusModel: Self.statusModel, current: 40, target: 40, status: 0)
        #expect(opening.motion == .opening)
        #expect(closing.motion == .closing)
        #expect(stopped.motion == .stopped)
        #expect(!opening.motionIsInferred, "a status word is authoritative")
    }

    @Test("A spec without status infers travel from the target and current position")
    func positionDifferenceInfersMotion() throws {
        let opening = try coverProperty(statusModel: Self.positionModel, current: 30, target: 80)
        let closing = try coverProperty(statusModel: Self.positionModel, current: 80, target: 30)
        #expect(opening.motion == .opening)
        #expect(closing.motion == .closing)
        #expect(opening.motionIsInferred)
        #expect(closing.motionIsInferred)

        let atTarget = try coverProperty(statusModel: Self.positionModel, current: 55, target: 55)
        #expect(atTarget.motion == .stopped)
        #expect(atTarget.positionPercent == 55)
        #expect(!atTarget.motionIsInferred)

        // The dead zone is applied before the comparison, so a parked 99 against 100 is at rest.
        let nearOpen = try coverProperty(statusModel: Self.positionModel, current: 99, target: 100)
        #expect(nearOpen.motion == .stopped)
        #expect(nearOpen.positionPercent == 100)
        let nearClosed = try coverProperty(statusModel: Self.positionModel, current: 1, target: 0)
        #expect(nearClosed.motion == .stopped)
        #expect(nearClosed.positionPercent == 0)
    }

    @Test("A spec with only a target shows that target as the current position")
    func targetOnlySpecUsesTargetAsPosition() throws {
        let device = try coverDevice(model: Self.targetOnlyModel, target: 37)
        let cover = try #require(device.coverSpec)
        #expect(cover.entityId == "miot:cover1:1")
        #expect(cover.positionPercent == 37)
        #expect(cover.positionWritable)
        #expect(cover.motion == .stopped)
        #expect(device.statusSubtitle == "开 37%")
    }

    @Test("A missing current position falls back to the target position")
    func missingCurrentUsesTarget() throws {
        let cover = try coverProperty(statusModel: Self.statusModel, target: 63, status: 0)
        #expect(cover.positionPercent == 63)
    }

    @Test("Terminal Open/Close statuses stay stopped unless the position says the motor travels")
    func terminalOpenCloseStatusesDoNotLookLikeTravel() throws {
        let open = try coverProperty(
            statusModel: Self.terminalStatusModel, current: 100, target: 100, status: 1
        )
        #expect(open.motion == CoverMotion.stopped, "Open is a terminal position here, not travel")
        #expect(open.positionPercent == 100)

        let closed = try coverProperty(
            statusModel: Self.terminalStatusModel, current: 0, target: 0, status: 2
        )
        #expect(closed.motion == CoverMotion.stopped, "Close is a terminal position here, not travel")
        #expect(closed.positionPercent == 0)

        // The same bare words still infer travel when the current position is away from the end.
        let opening = try coverProperty(
            statusModel: Self.terminalStatusModel, current: 50, target: 100, status: 1
        )
        #expect(opening.motion == CoverMotion.opening)
        #expect(opening.motionIsInferred)
        let closing = try coverProperty(
            statusModel: Self.terminalStatusModel, current: 50, target: 0, status: 2
        )
        #expect(closing.motion == CoverMotion.closing)
        #expect(closing.motionIsInferred)
    }

    // MARK: Dead zone

    @Test("Positions within two points of either end snap to the end")
    func deadZoneSnapsEnds() {
        #expect(CoverSpec.snappedPosition(0) == 0)
        #expect(CoverSpec.snappedPosition(1) == 0)
        #expect(CoverSpec.snappedPosition(2) == 0)
        #expect(CoverSpec.snappedPosition(3) == 3)
        #expect(CoverSpec.snappedPosition(97) == 97)
        #expect(CoverSpec.snappedPosition(98) == 100)
        #expect(CoverSpec.snappedPosition(99) == 100)
        #expect(CoverSpec.snappedPosition(100) == 100)
    }

    @Test("A parked curtain never displays 1% or 99%")
    func deadZoneReachesTheCard() throws {
        let low = try coverProperty(statusModel: Self.statusModel, current: 1, target: 1, status: 0)
        let high = try coverProperty(statusModel: Self.statusModel, current: 99, target: 99, status: 0)
        #expect(low.positionPercent == 0)
        #expect(high.positionPercent == 100)

        let lowTargetOnly = try coverProperty(statusModel: Self.targetOnlyModel, target: 1)
        #expect(lowTargetOnly.positionPercent == 0)
        let highTargetOnly = try coverProperty(statusModel: Self.targetOnlyModel, target: 99)
        #expect(highTargetOnly.positionPercent == 100)
    }

    @Test("A moving cover displays its target, so an intermediate position cannot pull the card back")
    func movingCoverKeepsTargetPosition() throws {
        let cover = try coverProperty(statusModel: Self.statusModel, current: 55, target: 100, status: 1)
        #expect(cover.motion == .opening)
        #expect(cover.positionPercent == 100)

        let inferred = try coverProperty(statusModel: Self.positionModel, current: 55, target: 100)
        #expect(inferred.motion == .opening)
        #expect(inferred.positionPercent == 100)

        // The status word can arrive before the target read-back. Neither a lagging target while
        // opening nor a stale one while closing may reverse the displayed position.
        let laggingOpen = try coverProperty(statusModel: Self.statusModel, current: 55, target: 0, status: 1)
        #expect(laggingOpen.positionPercent == 55)
        let laggingClose = try coverProperty(statusModel: Self.statusModel, current: 55, target: 100, status: 2)
        #expect(laggingClose.positionPercent == 55)
    }

    // MARK: Store behavior

    @MainActor
    private func makeStore(_ devices: [SmartDevice]) async throws -> (HomeStore, MockHomeProvider) {
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: devices))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.cover.moving.\(UUID().uuidString)")
        store.controlDebounceInterval = .zero
        for _ in 0..<50 where store.devices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(store.devices.count == devices.count)
        return (store, mock)
    }

    @MainActor
    private func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<100 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    @Test("A travel report at the commanded position takes over the card")
    @MainActor
    func movingReadBackTakesOverTheCard() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)

        #expect(await store.togglePrimary(card)?.value == true)
        let optimistic = try #require(store.devices.first?.coverSpec)
        #expect(optimistic.positionPercent == 100)
        #expect(optimistic.motion == .opening)

        let arrived = try coverDevice(current: 100, target: 100, status: 1)
        await mock.updateSnapshot(SynthesizedHome(devices: [arrived]))
        await waitUntil { store.optimisticLockUntil[arrived.id] == nil }

        #expect(store.optimisticLockUntil[arrived.id] == nil, "the reported position reached the command")
        let shown = try #require(store.devices.first?.coverSpec)
        #expect(shown.positionPercent == 100)
        #expect(shown.motion == .opening)
        #expect(store.devices.first?.statusSubtitle == "正在打开")
    }

    @Test("An expired cover lock drops its pre-command origin before a later pause")
    @MainActor
    func expiredCoverLockDropsOrigin() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)
        #expect(await store.togglePrimary(card)?.value == true)
        let id = parked.id
        // The mock echoes a snapshot while the command is in flight. Pin the state the command
        // owns so this test exercises lock expiry rather than echo timing.
        store.coverCommandOrigins[id] = 0
        store.optimisticLockUntil[id] = ContinuousClock.now.advanced(by: .seconds(60))
        #expect(store.coverCommandOrigins[id] == 0)
        let optimistic = try #require(store.devices.first?.coverSpec)
        #expect(optimistic.positionPercent == 100)
        #expect(optimistic.motion == .opening)

        // The command never gets a confirming snapshot, so its lock expires. A later external
        // movement still carries the old origin, which equals the position the motor stops at.
        store.optimisticLockUntil[id] = ContinuousClock.now.advanced(by: .seconds(-1))
        let moving = try coverDevice(current: 0, target: 100, status: 1)
        let beforeMoving = store.snapshotGeneration
        await mock.updateSnapshot(SynthesizedHome(devices: [moving]))
        await waitUntil { store.snapshotGeneration > beforeMoving }
        #expect(store.coverCommandOrigins[id] == nil, "an expired lock must drop its origin")

        #expect(await store.togglePrimary(try #require(store.devices.first))?.value == true)
        let stopped = try coverDevice(current: 0, target: 0, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [stopped]))
        await waitUntil { store.devices.first?.coverSpec?.positionPercent == 0 }
        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.positionPercent == 0, "the real stop at the old origin must not be hidden")
        #expect(settled.motion == .stopped)
    }

    @Test("An expired cover lock drops its origin before a pause, even with no snapshot in between")
    @MainActor
    func expiredCoverLockDropsOriginBeforePause() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let id = parked.id
        #expect(await store.togglePrimary(try #require(store.devices.first))?.value == true)
        // The command is still opening from 0 and its lock runs out without a confirming
        // snapshot. Pin that state so no merge path clears the origin first.
        store.coverCommandOrigins[id] = 0
        store.optimisticLockUntil[id] = ContinuousClock.now.advanced(by: .seconds(-1))
        #expect(store.devices.first?.coverSpec?.motion == .opening)

        // The pause runs synchronously, so the origin it records is observable before the
        // command echo can reach the merge path.
        let pause = store.togglePrimary(try #require(store.devices.first))
        #expect(pause != nil, "the moving cover accepts a pause")
        #expect(store.coverCommandOrigins[id] != 0, "a pause must not inherit the expired origin")
        #expect(await pause?.value == true)
        #expect(store.devices.first?.coverSpec?.motion == .stopped)

        // The motor really stopped at 0, the expired origin. Reading that stop as the stale
        // pre-command snapshot would hide it behind the optimistic 100 until the pause lock runs
        // out, so the card must settle on 0.
        let stopped = try coverDevice(current: 0, target: 0, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [stopped]))
        await waitUntil { store.devices.first?.coverSpec?.positionPercent == 0 }
        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.positionPercent == 0, "the real stop at the expired origin must reach the card")
        #expect(settled.motion == .stopped)
    }

    @Test("A travel status cannot unlock before the reported position leaves the origin")
    @MainActor
    func travelStatusWithoutReportedMovementKeepsLock() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)
        #expect(await store.togglePrimary(card)?.value == true)

        // The target moved to 100 and the status says Opening, but the reported position is still 0.
        // The display position is the target, so comparing it would unlock before the motor moves.
        let started = try coverDevice(current: 0, target: 100, status: 1)
        let beforeStart = store.snapshotGeneration
        await mock.updateSnapshot(SynthesizedHome(devices: [started]))
        await waitUntil { store.snapshotGeneration > beforeStart }
        #expect(store.optimisticLockUntil[started.id] != nil)
        let shown = try #require(store.devices.first?.coverSpec)
        #expect(shown.positionPercent == 100)
        #expect(shown.motion == .opening)

        // A stale Stopped report at the origin must not replace the optimistic travel.
        let staleStop = try coverDevice(current: 0, target: 100, status: 0)
        let beforeStop = store.snapshotGeneration
        await mock.updateSnapshot(SynthesizedHome(devices: [staleStop]))
        await waitUntil { store.snapshotGeneration > beforeStop }
        #expect(store.optimisticLockUntil[staleStop.id] != nil)
        let held = try #require(store.devices.first?.coverSpec)
        #expect(held.positionPercent == 100)
        #expect(held.motion == .opening)

        let arrived = try coverDevice(current: 100, target: 100, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [arrived]))
        await waitUntil { store.devices.first?.coverSpec?.motion == CoverMotion.stopped }
        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.positionPercent == 100)
        #expect(settled.motion == .stopped)
    }

    @Test("A status word that arrives before the target read-back cannot pull the card back")
    @MainActor
    func laggingTargetReportDoesNotPullBack() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)
        #expect(await store.togglePrimary(card)?.value == true)

        // The motor says it is opening while its target still reads 0. Accepting this sample would
        // replace the commanded 100 with 1, so the lock must hold until the position catches up.
        let lagging = try coverDevice(current: 1, target: 0, status: 1)
        let before = store.snapshotGeneration
        await mock.updateSnapshot(SynthesizedHome(devices: [lagging]))
        await waitUntil { store.snapshotGeneration > before }
        let shown = try #require(store.devices.first?.coverSpec)
        #expect(shown.positionPercent == 100)
        #expect(shown.motion == .opening)
        #expect(store.optimisticLockUntil[lagging.id] != nil)

        let caughtUp = try coverDevice(current: 100, target: 100, status: 1)
        await mock.updateSnapshot(SynthesizedHome(devices: [caughtUp]))
        await waitUntil { store.optimisticLockUntil[lagging.id] == nil }
        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.positionPercent == 100)
        #expect(settled.motion == .opening)
    }

    @Test("The card settles on the device's stopped position after the travel report")
    @MainActor
    func stoppedReadBackSettlesTheCard() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)

        #expect(await store.togglePrimary(card)?.value == true)

        let moving = try coverDevice(current: 100, target: 100, status: 1)
        await mock.updateSnapshot(SynthesizedHome(devices: [moving]))
        await waitUntil { store.optimisticLockUntil[moving.id] == nil }

        let stopped = try coverDevice(current: 100, target: 100, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [stopped]))
        await waitUntil { store.devices.first?.coverSpec?.motion == CoverMotion.stopped }

        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.motion == .stopped)
        #expect(settled.positionPercent == 100)
        #expect(store.devices.first?.statusSubtitle == "已打开")
    }

    @Test("A dragged end position displays as 0 or 100 while the requested value is written")
    @MainActor
    func sliderEndSnapsTheDisplay() async throws {
        let parked = try coverDevice(current: 50, target: 50, status: 0)
        let (store, mock) = try await makeStore([parked])
        let id = parked.id

        store.setCoverPosition(id, percent: 99)
        await waitUntil { await mock.executedCommands.count == 1 }
        let high = try #require(store.devices.first?.coverSpec)
        #expect(high.positionPercent == 100)
        #expect(await mock.executedCommands.last == .setNumberValue(entityId: "miot:cover1:2", value: 99))

        store.setCoverPosition(id, percent: 1)
        await waitUntil { await mock.executedCommands.count == 2 }
        let low = try #require(store.devices.first?.coverSpec)
        #expect(low.positionPercent == 0)
        #expect(await mock.executedCommands.last == .setNumberValue(entityId: "miot:cover1:2", value: 1))
    }

    @Test("Pausing a moving cover settles on the device's stop position at once")
    @MainActor
    func pauseSettlesOnTheStopPosition() async throws {
        let moving = try coverDevice(current: 40, target: 40, status: 1)
        let (store, mock) = try await makeStore([moving])
        let card = try #require(store.devices.first)

        #expect(await store.togglePrimary(card)?.value == true)
        let paused = try #require(store.devices.first?.coverSpec)
        #expect(paused.motion == .stopped)
        #expect(store.optimisticLockUntil[moving.id] != nil, "a stale travel report must not restart the label")

        let stopped = try coverDevice(current: 55, target: 55, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [stopped]))
        await waitUntil { store.devices.first?.coverSpec?.positionPercent == 55 }
        #expect(store.devices.first?.coverSpec?.positionPercent == 55)
        #expect(store.optimisticLockUntil[moving.id] == nil)
        #expect(store.devices.first?.statusSubtitle == "开 55%")
    }

    @Test("An obstruction stop off the commanded target ends the travel at once")
    @MainActor
    func offTargetStopSettles() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)

        #expect(await store.togglePrimary(card)?.value == true)

        // The motor reported stopped at 40 while the target still says 100. The pre-command
        // position was 0, so this is a real stop, not the stale snapshot before travel.
        let stopped = try coverDevice(current: 40, target: 100, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [stopped]))
        await waitUntil { store.devices.first?.coverSpec?.motion == CoverMotion.stopped }

        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.motion == .stopped)
        #expect(settled.positionPercent == 40)
        #expect(store.optimisticLockUntil[stopped.id] == nil)
    }

    @Test("A short travel that stops at the commanded position settles without waiting for a moving report")
    @MainActor
    func shortTravelSettlesAtCommandedPosition() async throws {
        let parked = try coverDevice(current: 0, target: 0, status: 0)
        let (store, mock) = try await makeStore([parked])
        let card = try #require(store.devices.first)

        #expect(await store.togglePrimary(card)?.value == true)

        let stopped = try coverDevice(current: 100, target: 100, status: 0)
        await mock.updateSnapshot(SynthesizedHome(devices: [stopped]))
        await waitUntil { store.devices.first?.coverSpec?.motion == CoverMotion.stopped }

        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.motion == .stopped)
        #expect(settled.positionPercent == 100)
        #expect(store.devices.first?.statusSubtitle == "已打开")
    }

    @Test("An inferred travel label clears when the reported position stops changing")
    @MainActor
    func staticInferredMotionStops() async throws {
        let parked = try coverDevice(model: Self.positionModel, current: 30, target: 30)
        let (store, mock) = try await makeStore([parked])
        #expect(store.devices.first?.coverSpec?.motion == CoverMotion.stopped)

        let moving = try coverDevice(model: Self.positionModel, current: 40, target: 80)
        await mock.updateSnapshot(SynthesizedHome(devices: [moving]))
        await waitUntil { store.devices.first?.coverSpec?.motion == CoverMotion.opening }
        let first = try #require(store.devices.first?.coverSpec)
        #expect(first.motionIsInferred)
        #expect(first.positionPercent == 80)

        // The motor reported no new position. The target gap is no longer evidence of travel.
        let stalled = try coverDevice(model: Self.positionModel, current: 40, target: 80)
        await mock.updateSnapshot(SynthesizedHome(devices: [stalled]))
        await waitUntil { store.devices.first?.coverSpec?.motion == CoverMotion.stopped }
        let settled = try #require(store.devices.first?.coverSpec)
        #expect(settled.motion == .stopped)
        #expect(settled.positionPercent == 40, "the card shows where the motor actually stopped")
    }

    @Test("Tapping a stationary cover whose status says Open closes it instead of pausing")
    @MainActor
    func terminalOpenTapClosesInsteadOfPausing() async throws {
        let open = try coverDevice(
            model: Self.terminalStatusModel, current: 100, target: 100, status: 1
        )
        let (store, mock) = try await makeStore([open])

        #expect(await store.togglePrimary(try #require(store.devices.first))?.value == true)
        #expect(await mock.executedCommands == [.setSelectOption(entityId: "miot:cover1:2", option: "Close")])
    }

    @Test("Tapping a moving window opener stops it instead of reversing it")
    @MainActor
    func movingWindowOpenerTapStops() async throws {
        let (store, mock) = try await makeStore([windowOpener(position: 40, motion: .opening)])

        #expect(await store.togglePrimary(try #require(store.devices.first))?.value == true)
        #expect(await mock.executedCommands == [.setSelectOption(entityId: "miot:wd1:2", option: "Pause")])
        let stopped = try #require(store.devices.first?.coverSpec)
        #expect(stopped.motion == .stopped)
    }

    @Test("Tapping a closing window opener stops without the open confirmation prompt")
    @MainActor
    func closingWindowOpenerTapStopsWithoutPrompt() async throws {
        let (store, mock) = try await makeStore([windowOpener(position: 0, motion: .closing)])
        var prompts = 0
        store.manualControlConfirmationHandler = { _ in
            prompts += 1
            return false
        }

        #expect(await store.togglePrimary(try #require(store.devices.first))?.value == true)
        #expect(prompts == 0, "stopping travel is not a hazardous open")
        #expect(await mock.executedCommands == [.setSelectOption(entityId: "miot:wd1:2", option: "Pause")])
    }

    private func windowOpener(position: Int?, motion: CoverMotion) -> SmartDevice {
        SmartDevice(
            id: "wd1", name: "卧室窗户", roomId: "bed", roomName: "卧室",
            model: "linp.wopener.wd1lb", isOnline: true,
            kind: .cover(CoverSpec(
                entityId: "miot:wd1:2",
                subtype: .windowOpener,
                positionPercent: position,
                positionWritable: true,
                openDescription: "Open",
                closeDescription: "Close",
                pauseDescription: "Pause",
                motion: motion
            ))
        )
    }

    private func coverProperty(
        statusModel model: String,
        current: Int? = nil,
        target: Int? = nil,
        status: Int? = nil
    ) throws -> CoverSpec {
        try #require(try coverDevice(model: model, current: current, target: target, status: status).coverSpec)
    }
}

private extension SmartDevice {
    var coverSpec: CoverSpec? {
        guard case .cover(let spec) = kind else { return nil }
        return spec
    }
}
