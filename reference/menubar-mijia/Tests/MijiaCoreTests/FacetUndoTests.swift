import Foundation
import Testing
@testable import MijiaCore

@MainActor
@Suite(.timeLimit(.minutes(1)))
struct FacetUndoTests {
    private func light(_ id: String, entityId: String) -> SmartDevice {
        SmartDevice(
            id: id, name: id, roomId: "room", roomName: "room", model: "test.light",
            isOnline: true, kind: .light(LightSpec(entityId: entityId, isOn: true))
        )
    }

    private func facet(entityId: String, id: String = "3.2", value: Double = 1) -> FacetControl {
        FacetControl(
            id: id, kind: .select, name: "mode", title: "Mode", serviceName: "light",
            value: .number(value), isWritable: true, writesByDefault: true, isHighValue: true,
            target: .property(entityId: entityId, piid: 2)
        )
    }

    private func makeStore(
        devices: [SmartDevice], facets: [String: [FacetControl]]
    ) async throws -> (HomeStore, MockHomeProvider) {
        var snapshot = SynthesizedHome(devices: devices)
        snapshot.deviceFacets = facets
        let provider = MockHomeProvider(snapshot: snapshot)
        let store = HomeStore(provider: provider, defaultsSuiteName: "test.facet.undo.\(UUID().uuidString)")
        try await store.waitForTestSnapshot()
        return (store, provider)
    }

    private func offerUndo(_ store: HomeStore, command: DeviceCommand) {
        store.scheduleUndoBanner(
            title: "Updated mode", restoreCommands: [command], previousDevices: store.devices,
            deviceCount: 1
        )
        store.pauseUndoBannerCountdown()
    }

    @Test("Facet undo finds the Xiaomi target on a paired Home Assistant card")
    func pairedCardUndoFindsFacetOwner() async throws {
        let target = "miot:mi-light:3"
        let device = light("ha-light", entityId: "light.living")
        let (store, provider) = try await makeStore(
            devices: [device], facets: [device.id: [facet(entityId: target)]]
        )
        let command = DeviceCommand.setMiotProperty(entityId: target, piid: 2, value: .number(0))
        offerUndo(store, command: command)
        let task = try #require(store.undoTurnAllOff())
        #expect(store.deviceFacets[device.id]?.first?.value == .number(0))
        #expect(store.optimisticLockUntil[device.id] != nil)
        await task.value
        #expect(await provider.executedCommands == [command])
    }

    @Test("Facet undo locks and restores the addressed split card instead of its sibling")
    func splitCardUndoKeepsSibling() async throws {
        let first = light("100.s2", entityId: "miot:100:2")
        let second = light("100.s3", entityId: "miot:100:3")
        let target = "miot:100:3"
        let (store, provider) = try await makeStore(
            devices: [first, second],
            facets: [first.id: [facet(entityId: "miot:100:2", id: "2.2")],
                     second.id: [facet(entityId: target)]]
        )
        let command = DeviceCommand.setMiotProperty(entityId: target, piid: 2, value: .number(0))
        offerUndo(store, command: command)
        let task = try #require(store.undoTurnAllOff())
        #expect(store.deviceFacets[first.id]?.first?.value == .number(1))
        #expect(store.optimisticLockUntil[first.id] == nil)
        #expect(store.deviceFacets[second.id]?.first?.value == .number(0))
        #expect(store.optimisticLockUntil[second.id] != nil)
        await task.value
        #expect(await provider.executedCommands == [command])
    }

    @Test("A failed facet undo restores the pre-undo value and keeps the command available to retry")
    func failedUndoRestoresFacetValue() async throws {
        let device = light("light", entityId: "miot:light:2")
        let target = "miot:light:3"
        let (store, provider) = try await makeStore(
            devices: [device], facets: [device.id: [facet(entityId: target)]]
        )
        let command = DeviceCommand.setMiotProperty(entityId: target, piid: 2, value: .number(0))
        offerUndo(store, command: command)
        await provider.setShouldFail(true)
        let task = try #require(store.undoTurnAllOff())
        #expect(store.deviceFacets[device.id]?.first?.value == .number(0))
        await task.value
        store.pauseUndoBannerCountdown()
        #expect(store.deviceFacets[device.id]?.first?.value == .number(1))
        #expect(store.undoBanner?.restoreCommands == [command])
        #expect(store.optimisticLockUntil[device.id] == nil)
        await provider.setShouldFail(false)
        let retry = try #require(store.undoTurnAllOff())
        await retry.value
        #expect(store.deviceFacets[device.id]?.first?.value == .number(0))
        #expect(await provider.executedCommands == [command])
    }

    @Test("A failed voice undo restores facet values along with device rows")
    func failedVoiceUndoRestoresFacetValue() async throws {
        let device = light("light", entityId: "miot:light:2")
        let target = "miot:light:3"
        let (store, provider) = try await makeStore(
            devices: [device], facets: [device.id: [facet(entityId: target)]]
        )
        let command = DeviceCommand.setMiotProperty(entityId: target, piid: 2, value: .number(0))
        store.lastAssistantUndo = (
            reply: "Updated mode", restore: [command], previous: store.devices,
            at: Date(), habitReceipt: nil
        )
        await provider.setShouldFail(true)
        _ = await store.runAssistantCommand("撤销")
        #expect(store.deviceFacets[device.id]?.first?.value == .number(1))
        #expect(store.lastAssistantUndo?.restore == [command])
    }
    @Test("An offline facet owner never falls back to its online split sibling")
    func offlineSplitOwnerIsNotRetargeted() async throws {
        let first = light("100.s2", entityId: "miot:100:2")
        var second = light("100.s3", entityId: "miot:100:3")
        second.isOnline = false
        let (store, provider) = try await makeStore(
            devices: [first, second],
            facets: [first.id: [facet(entityId: "miot:100:2", id: "2.2")],
                     second.id: [facet(entityId: "miot:100:3")]]
        )
        offerUndo(store, command: .setMiotProperty(entityId: "miot:100:3", piid: 2, value: .number(0)))
        let task = store.undoTurnAllOff()
        #expect(task == nil)
        #expect(store.optimisticLockUntil[first.id] == nil)
        #expect(store.deviceFacets[first.id]?.first?.value == .number(1))
        #expect(await provider.executedCommands.isEmpty)
    }

}
