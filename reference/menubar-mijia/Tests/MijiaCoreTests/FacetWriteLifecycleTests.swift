import Foundation
import Testing
@testable import MijiaCore

@MainActor
struct FacetWriteLifecycleTests {
    private func snapshot(backend: HomeBackend = .xiaomiCloud) -> SynthesizedHome {
        let device = SmartDevice(id: "light", name: "Light", roomId: "room", roomName: "Room",
                                 model: "test.light", isOnline: true,
                                 kind: .light(LightSpec(entityId: "miot:light:2", isOn: true)))
        var snapshot = SynthesizedHome(devices: [device])
        snapshot.deviceBackends = [device.id: backend]
        snapshot.deviceFacets = [device.id: ["3.1", "3.2"].enumerated().map { index, id in
            FacetControl(id: id, kind: .slider, name: "brightness", title: "Brightness",
                         serviceName: "light", value: .number(0), isWritable: true,
                         writesByDefault: true, isHighValue: true,
                         target: .property(entityId: "miot:light:3", piid: index + 1))
        }]
        return snapshot
    }

    private func makeStore() async throws -> (HomeStore, MockHomeProvider) {
        let provider = MockHomeProvider(snapshot: snapshot())
        let store = HomeStore(provider: provider, defaultsSuiteName: "test.facet.lifecycle.\(UUID())")
        try await store.waitForTestSnapshot()
        store.controlDebounceInterval = .zero
        return (store, provider)
    }

    @Test("Independent facet writes on one card both reach the provider")
    func independentControlsBothSend() async throws {
        let (store, provider) = try await makeStore()
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        let first = store.debounceTasks["light.facet.3.1"]
        store.setFacetValue("light", controlId: "3.2", value: .number(20))
        let second = store.debounceTasks["light.facet.3.2"]
        await first?.value
        await second?.value
        #expect(await provider.executedCommands.contains(.setMiotProperty(entityId: "miot:light:3", piid: 1, value: .number(10))))
        #expect(await provider.executedCommands.contains(.setMiotProperty(entityId: "miot:light:3", piid: 2, value: .number(20))))
        #expect(await provider.executedCommands.count == 2)
        #expect(store.facetWrites.isEmpty)
    }

    @Test("Failed continuous dragging restores its starting value and removes its undo banner")
    func failedDragRestoresOrigin() async throws {
        let (store, provider) = try await makeStore()
        await provider.setShouldFail(true)
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        store.setFacetValue("light", controlId: "3.1", value: .number(20))
        store.pauseUndoBannerCountdown()
        await store.debounceTasks["light.facet.3.1"]?.value
        #expect(store.deviceFacets["light"]?.first?.value == .number(0))
        #expect(store.facetWrites.isEmpty)
        #expect(store.undoBanner == nil)
    }

    @Test("A missing initial value never turns a drag's intermediate value into an undo origin")
    func unknownOriginStaysUnknown() async throws {
        let (store, provider) = try await makeStore()
        store.deviceFacets["light"]?[0].value = nil
        await provider.setShouldFail(true)
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        store.setFacetValue("light", controlId: "3.1", value: .number(20))
        #expect(store.undoBanner == nil)
        await store.debounceTasks["light.facet.3.1"]?.value
        #expect(store.deviceFacets["light"]?.first?.value == nil)
        #expect(store.facetWrites.isEmpty)
    }

    @Test("Undo cancels the pending facet debounce and the next gesture can offer undo again")
    func undoCancelsPendingGesture() async throws {
        let (store, provider) = try await makeStore()
        store.controlDebounceInterval = .seconds(60)
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        let pending = store.debounceTasks["light.facet.3.1"]
        await store.undoTurnAllOff()?.value
        await pending?.value
        #expect(await provider.executedCommands == [.setMiotProperty(entityId: "miot:light:3", piid: 1, value: .number(0))])
        #expect(store.facetWrites.isEmpty)
        store.setFacetValue("light", controlId: "3.1", value: .number(20))
        store.pauseUndoBannerCountdown()
        #expect(store.undoBanner != nil)
        store.cancelFacetWrites(for: nil)
    }

    @Test("A backend change clears pending facet writes without sending them to the new source")
    func backendChangeCancelsPendingGesture() async throws {
        let (store, provider) = try await makeStore()
        store.controlDebounceInterval = .seconds(60)
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        let pending = store.debounceTasks["light.facet.3.1"]
        let generation = store.snapshotGeneration
        await provider.updateSnapshot(snapshot(backend: .homeAssistant))
        for _ in 0..<200 {
            if store.snapshotGeneration > generation { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(store.snapshotGeneration > generation)
        await pending?.value
        #expect(store.facetWrites.isEmpty)
        #expect(store.debounceTasks["light.facet.3.1"] == nil)
        #expect(await provider.executedCommands.isEmpty)
    }
    @Test("Auxiliary writes and actions do not supersede a pending primary brightness write", arguments: [false, true])
    func facetDoesNotCancelPrimaryDebounce(action: Bool) async throws {
        let (store, provider) = try await makeStore()
        store.manualControlConfirmationHandler = { _ in true }
        if action {
            store.deviceFacets["light"]?[0].kind = .button
            store.deviceFacets["light"]?[0].target = .action(entityId: "miot-action:light:3:1")
        }
        store.setLightBrightness("light", percent: 80)
        let primary = try #require(store.debounceTasks["light.brightness"])
        let revision = store.optimisticLockRevision["light"]
        let primaryDeadline = try #require(store.optimisticLockUntil["light"])
        if action {
            store.runFacetAction("light", controlId: "3.1")
        } else {
            store.setFacetValue("light", controlId: "3.1", value: .number(10))
        }
        let auxiliary = store.debounceTasks["light.facet.3.1"]
        #expect(store.optimisticLockRevision["light"] == revision)
        #expect(try #require(store.optimisticLockUntil["light"]) >= primaryDeadline)
        await auxiliary?.value
        await primary.value
        #expect(await provider.executedCommands.contains(.setLightBrightness(entityId: "miot:light:2", percent: 80)))
        #expect(await provider.executedCommands.count == 2)
    }

    @Test("A failed facet leaves another successful facet protected against stale snapshots")
    func failedFacetKeepsOtherFacetLock() async throws {
        let (store, provider) = try await makeStore()
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        await store.debounceTasks["light.facet.3.1"]?.value
        await provider.setShouldFail(true)
        store.setFacetValue("light", controlId: "3.2", value: .number(20))
        await store.debounceTasks["light.facet.3.2"]?.value
        #expect(store.deviceFacets["light"]?[0].value == .number(10))
        #expect(store.deviceFacets["light"]?[1].value == .number(0))
        #expect(store.optimisticLockUntil["light"] != nil)
        let generation = store.snapshotGeneration
        await provider.updateSnapshot(snapshot())
        for _ in 0..<200 {
            if store.snapshotGeneration > generation { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(store.snapshotGeneration > generation)
        #expect(store.deviceFacets["light"]?[0].value == .number(10))
        #expect(store.deviceFacets["light"]?[1].value == .number(0))
    }

    @Test("A hazardous slider asks once across the pending gesture's ticks")
    func sliderGestureConfirmsOnce() async throws {
        let (store, provider) = try await makeStore()
        store.devices[0].model = "xiaomi.heater.v1"
        store.devices[0].kind = .climate(ClimateSpec(entityId: "miot:light:2", isOn: true,
            hvacMode: "heat", availableModes: ["off", "heat"], targetTemperature: 24))
        store.controlDebounceInterval = .seconds(60)
        var prompts = 0
        store.manualControlConfirmationHandler = { _ in prompts += 1; return true }
        store.setFacetValue("light", controlId: "3.1", value: .number(10))
        store.setFacetValue("light", controlId: "3.1", value: .number(20))
        store.setFacetValue("light", controlId: "3.1", value: .number(30))
        #expect(prompts == 1)
        #expect(await provider.executedCommands.isEmpty)
        store.cancelFacetWrites(for: nil)
    }

}
