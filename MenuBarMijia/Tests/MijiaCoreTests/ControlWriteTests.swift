import Foundation
import Testing
@testable import MijiaCore

/// The control-write module owns optimistic rollback for every write path: card controls, the
/// assistant, undo and Raycast links.
@MainActor
struct ControlWriteTests {
    private static let lamp = SmartDevice(
        id: "lamp", name: "台灯", roomId: "room", roomName: "书房", model: "yeelink.light.lamp4", isOnline: true,
        kind: .light(LightSpec(entityId: "light.lamp", isOn: true, brightnessPercent: 80))
    )
    private static let curtain = SmartDevice(
        id: "curtain", name: "窗帘", roomId: "room", roomName: "书房", model: "fixture.curtain", isOnline: true,
        kind: .cover(CoverSpec(entityId: "miot:curtain:2", subtype: .curtain, positionPercent: 40, positionWritable: true))
    )
    private static let fan = SmartDevice(
        id: "fan", name: "风扇", roomId: "room", roomName: "书房", model: "dmaker.fan.p5", isOnline: true,
        kind: .fan(FanSpec(entityId: "miot:fan:2", isOn: false, percentage: 20, presetMode: "Auto", availablePresetModes: ["Auto", "Favorite"]))
    )

    private func makeStore() async throws -> HomeStore {
        let provider = RefusingProvider(snapshot: SynthesizedHome(devices: [Self.lamp, Self.curtain, Self.fan]))
        let store = HomeStore(provider: provider, defaultsSuiteName: "test.controlwrite.\(UUID().uuidString)")
        store.toastAutoDismissInterval = nil
        try await store.waitForTestSnapshot()
        return store
    }

    private func brightness(_ store: HomeStore) -> Int? {
        guard case .light(let s) = store.devices.first(where: { $0.id == "lamp" })?.kind else { return nil }
        return s.brightnessPercent
    }

    private func withBrightness(_ percent: Int) -> SmartDevice {
        var device = Self.lamp
        guard case .light(var s) = device.kind else { return device }
        s.brightnessPercent = percent
        device.kind = .light(s)
        return device
    }

    @Test("A failed send restores the previous row and drops the lock")
    func failedSendRollsBack() async throws {
        let store = try await makeStore()
        let write = store.beginOptimisticWrite(from: Self.lamp, to: withBrightness(30), lock: .seconds(5))
        #expect(brightness(store) == 30)
        await #expect(throws: (any Error).self) {
            try await store.send(.setLightBrightness(entityId: "light.lamp", percent: 30), for: write)
        }
        #expect(brightness(store) == 80)
        #expect(store.optimisticLockUntil["lamp"] == nil)
    }

    @Test("An older write's failure leaves a newer write's row and lock alone")
    func olderFailureKeepsNewerWrite() async throws {
        let store = try await makeStore()
        let older = store.beginOptimisticWrite(from: Self.lamp, to: withBrightness(30), lock: .seconds(5))
        _ = store.beginOptimisticWrite(from: withBrightness(30), to: withBrightness(60), lock: .seconds(5))
        store.revert(older)
        #expect(brightness(store) == 60)
        #expect(store.optimisticLockUntil["lamp"] != nil)
    }

    @Test("Nothing shown means nothing to roll back")
    func unshownWriteDoesNotRevert() async throws {
        let store = try await makeStore()
        let write = store.beginOptimisticWrite(from: withBrightness(10), to: nil, lock: .seconds(5))
        store.revert(write)
        #expect(brightness(store) == 80)
        #expect(store.optimisticLockUntil["lamp"] == nil)
    }

    @Test("A failed Raycast cover write also clears the cover travel origin")
    func raycastFailureClearsCoverOrigin() async throws {
        let store = try await makeStore()
        store.coverCommandOrigins["curtain"] = 40
        let result = await store.executeRaycastDirectCommand("把窗帘开到70%")
        #expect(result.handled && result.isError)
        guard case .cover(let s) = store.devices.first(where: { $0.id == "curtain" })?.kind else {
            Issue.record("curtain missing")
            return
        }
        #expect(s.positionPercent == 40)
        #expect(store.coverCommandOrigins["curtain"] == nil)
        #expect(store.optimisticLockUntil["curtain"] == nil)
    }

    @Test("Card and Raycast fan speed share one change: power on and switch to Favorite")
    func fanPercentageChangeIsShared() async throws {
        let change = try #require(HomeStore.fanPercentageChange(Self.fan, percent: 0))
        guard case .fan(let s) = change.updated.kind else { Issue.record("not a fan"); return }
        #expect(s.isOn)
        #expect(s.percentage == 1)
        #expect(s.presetMode == "Favorite")
        #expect(change.command == .setFanPercentage(entityId: "miot:fan:2", percent: 1))
        #expect(change.favoritePreset == "Favorite")
    }
}

/// A provider that accepts the snapshot but refuses every write.
private actor RefusingProvider: HomeProvider {
    private let snapshot: SynthesizedHome

    init(snapshot: SynthesizedHome) { self.snapshot = snapshot }

    nonisolated func events() -> AsyncStream<HomeProviderEvent> {
        AsyncStream { continuation in
            Task { await self.start(continuation) }
        }
    }

    private func start(_ continuation: AsyncStream<HomeProviderEvent>.Continuation) {
        continuation.yield(.statusChanged(.connected))
        continuation.yield(.snapshotUpdated(snapshot))
    }

    func execute(_ command: DeviceCommand) async throws {
        throw HomeProviderError.serviceCallFailed(500, "refused")
    }

    func refreshNow() async {}
}
