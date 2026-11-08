import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// #20: the Raycast/CLI links land on the same handler as hand-written ones, so the bridge token, the
/// confirmation sheet, the high-risk gate and the command feedback have to hold for them too.
@MainActor
struct URLCommandHandlerTests {
    private static let lamp = SmartDevice(
        id: "dev_lamp",
        name: "Study Lamp",
        roomId: "study",
        roomName: "Study",
        model: "test.light",
        isOnline: true,
        kind: .light(LightSpec(entityId: "light.lamp", isOn: true, brightnessPercent: 80))
    )
    private static let airConditioner = SmartDevice(
        id: "dev_ac",
        name: "Study AC",
        roomId: "study",
        roomName: "Study",
        model: "test.aircondition",
        isOnline: true,
        kind: .climate(ClimateSpec(
            entityId: "climate.ac",
            isOn: true,
            hvacMode: "cool",
            availableModes: ["off", "cool"],
            targetTemperature: 25
        ))
    )

    private static func makeStore() async throws -> (HomeStore, MockHomeProvider) {
        let suite = "test.urlhandler.\(UUID().uuidString)"
        let provider = MockHomeProvider(snapshot: SynthesizedHome(
            rooms: [RoomArea(id: "study", name: "Study", sortOrder: 0)],
            devices: [lamp, airConditioner]
        ))
        let store = HomeStore(provider: provider, defaultsSuiteName: suite)
        try await store.waitForTestSnapshot()
        return (store, provider)
    }

    private static func url(_ raw: String) throws -> URL {
        try #require(URL(string: raw))
    }

    @Test("A power link carrying the bridge token skips the sheet and reports its result")
    func trustedPowerLinkSkipsTheSheet() async throws {
        try await L10n.$overrideLanguage.withValue(.en) {
            let (store, provider) = try await Self.makeStore()
            store.setRaycastIntegration(enabled: true)
            let token = try #require(store.raycastBridgeToken)

            let handler = URLCommandHandler()
            var asked = false
            handler.confirmationHandler = { _ in
                asked = true
                return false
            }

            let link = try Self.url("menumijia://power?device=dev_lamp&on=false&id=cli-1&token=\(token)")
            await handler.handle(link, in: store)

            #expect(!asked, "a trusted companion link must not show the confirmation sheet")
            #expect(store.devices.first { $0.id == "dev_lamp" }?.isActiveOn == false)
            #expect(await provider.executedCommands.count == 1)
            #expect(store.lastRaycastCommandFeedback?.requestId == "cli-1")
            #expect(store.lastRaycastCommandFeedback?.isError == false)
            #expect(store.lastRaycastCommandFeedback?.message.contains("Turned off") == true)
        }
    }

    @Test("An untrusted value link names the device and the value, and a refusal sends nothing")
    func untrustedLinkAsksWithTheExactValue() async throws {
        try await L10n.$overrideLanguage.withValue(.en) {
            let (store, provider) = try await Self.makeStore()
            // Integration off: there is no bridge token, so the link must be confirmed.
            #expect(store.raycastBridgeToken == nil)

            let handler = URLCommandHandler()
            var prompt: String?
            handler.confirmationHandler = { description in
                prompt = description
                return false
            }

            let link = try Self.url("menumijia://brightness?device=dev_lamp&percent=40&id=cli-2&token=stale")
            await handler.handle(link, in: store)

            let sheet = try #require(prompt)
            #expect(sheet.contains("Study Lamp"))
            #expect(sheet.contains("40%"))
            #expect(await provider.executedCommands.isEmpty)
            #expect(store.lastRaycastCommandFeedback?.isError == true)
            #expect(store.lastRaycastCommandFeedback?.message.contains("Cancelled") == true)
        }
    }

    @Test("A focus link opens the panel on the device without asking and without sending a command")
    func focusLinkOpensThePanel() async throws {
        try await L10n.$overrideLanguage.withValue(.en) {
            let (store, provider) = try await Self.makeStore()

            let handler = URLCommandHandler()
            var asked = false
            handler.confirmationHandler = { _ in
                asked = true
                return false
            }
            var windowShown = 0
            handler.windowVisibilityHandler = { windowShown += 1 }

            let link = try Self.url("menumijia://focus?device=dev_ac&id=cli-3")
            await handler.handle(link, in: store)

            #expect(!asked, "showing a drawer is navigation, not a device control")
            #expect(store.expandedDeviceId == "dev_ac")
            #expect(windowShown == 1)
            #expect(await provider.executedCommands.isEmpty)
            #expect(store.lastRaycastCommandFeedback?.isError == false)
        }
    }
}
