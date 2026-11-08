import Foundation
import Testing
@testable import MijiaCore

@MainActor
struct ControlFailureTests {
    private func lamp(online: Bool = true) -> SmartDevice {
        SmartDevice(
            id: "lamp",
            name: "台灯",
            roomId: "room",
            roomName: "书房",
            model: "yeelink.light.lamp4",
            isOnline: online,
            kind: .light(LightSpec(
                entityId: "light.lamp",
                isOn: false,
                brightnessPercent: 80,
                colorTempKelvin: 4000,
                currentEffect: "Day",
                availableEffects: ["Day", "Night"]
            ))
        )
    }

    private func makeStore(error: any Error & Sendable, online: Bool = true) async throws -> HomeStore {
        let provider = FailingControlProvider(
            snapshot: SynthesizedHome(devices: [lamp(online: online)]),
            error: error
        )
        let store = HomeStore(provider: provider, defaultsSuiteName: "test.controlfailure.\(UUID().uuidString)")
        store.toastAutoDismissInterval = nil
        try await store.waitForTestSnapshot()
        return store
    }

    /// Runs a real control write through the store and returns the toast it showed, plus the store so
    /// the caller can check the card rolled back.
    private func failureToast(
        for error: any Error & Sendable,
        online: Bool = true,
        adjustment: Bool = false
    ) async throws -> (message: String, store: HomeStore) {
        let store = try await makeStore(error: error, online: online)
        if adjustment {
            store.controlDebounceInterval = .zero
            store.setLightBrightness("lamp", percent: 30)
        } else {
            store.setLightEffect("lamp", effect: "Night")
        }
        for _ in 0..<300 where store.toastNotice == nil {
            try await Task.sleep(for: .milliseconds(10))
        }
        return (try #require(store.toastNotice?.message), store)
    }

    private func capturedCloudError(_ body: () throws -> Void) -> MiCloudError? {
        do {
            try body()
            return nil
        } catch let error as MiCloudError {
            return error
        } catch {
            return nil
        }
    }

    private func capturedProviderError(_ operation: () async throws -> Void) async -> HomeProviderError? {
        do {
            try await operation()
            return nil
        } catch let error as HomeProviderError {
            return error
        } catch {
            return nil
        }
    }

    @Test("The documented offline MIoT code is explained as offline")
    func offlineMiotCode() async throws {
        let error = try #require(capturedCloudError {
            try MiCloudClient.checkWriteCodes([.object(["code": .number(-704042011)])])
        })
        let (message, store) = try await failureToast(for: error)
        #expect(message == "台灯 操作未生效：设备离线，等它重新上线后再试")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("A device the snapshot marks offline is explained as offline for any backend")
    func offlineFromSnapshot() async throws {
        let (message, _) = try await failureToast(
            for: HomeProviderError.serviceCallFailed(500, "Entity is not available"),
            online: false
        )
        #expect(message == "台灯 操作未生效：设备离线，等它重新上线后再试")
    }

    @Test("HTTP 429 is explained as too many requests")
    func rateLimited() async throws {
        let (message, store) = try await failureToast(for: MiCloudError.http(429))
        #expect(message == "台灯 操作未生效：请求太频繁，稍后再试")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("An expired Mijia session asks the user to sign in again")
    func sessionExpired() async throws {
        let (message, store) = try await failureToast(for: MiCloudError.sessionExpired)
        #expect(message == "台灯 操作未生效：登录已过期，请在设置里重新登录")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("A real per-item device error still carries its code")
    func deviceRejected() async throws {
        let error = try #require(capturedCloudError {
            try MiCloudClient.checkWriteCodes([.object(["code": .number(-704002000)])])
        })
        guard case .deviceError(let code, _) = error else {
            Issue.record("Expected a per-item device error")
            return
        }
        #expect(code == -704002000)
        let (message, store) = try await failureToast(for: error)
        #expect(message == "台灯 操作未生效：设备拒绝了这次操作（米家错误码 -704002000）")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("A cloud envelope error keeps the generic copy and shows no code")
    func cloudEnvelopeErrorStaysGeneric() async throws {
        let body = Data(#"{"code":-8,"message":"data type not valid"}"#.utf8)
        let error = try #require(capturedCloudError {
            _ = try MiCloudClient.decodeResponse(body, statusCode: 400, signedNonce: "")
        })
        #expect(error == .api(code: -8, message: "data type not valid"))
        let (message, store) = try await failureToast(for: error)
        #expect(message == "台灯 操作未生效")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("A transport failure is explained as a network error")
    func networkFailure() async throws {
        let (message, store) = try await failureToast(for: URLError(.notConnectedToInternet))
        #expect(message == "台灯 操作未生效：网络异常，检查网络后重试")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("Home Assistant failures land in the same categories")
    func homeAssistantFailures() async throws {
        let unauthorized = try await failureToast(for: HomeProviderError.unauthorized)
        #expect(unauthorized.message == "台灯 操作未生效：访问令牌无效，请在设置里更新")

        let forbidden = try await failureToast(for: HomeProviderError.serviceCallFailed(401, "Unauthorized"))
        #expect(forbidden.message == "台灯 操作未生效：访问令牌无效，请在设置里更新")

        let throttled = try await failureToast(for: HomeProviderError.serviceCallFailed(429, "Too Many Requests"))
        #expect(throttled.message == "台灯 操作未生效：请求太频繁，稍后再试")

        let network = try await failureToast(
            for: HomeProviderError.networkError("No route to host")
        )
        #expect(network.message == "台灯 操作未生效：网络异常，检查网络后重试")
    }

    @Test("A gateway or timeout status is explained as a network error")
    func gatewayErrorIsNetwork() async throws {
        let (message, _) = try await failureToast(
            for: HomeProviderError.serviceCallFailed(503, "Service Unavailable")
        )
        #expect(message == "台灯 操作未生效：网络异常，检查网络后重试")
    }

    @Test("A plain 500 means the service answered with its own failure, so it keeps the generic copy")
    func internalServerErrorStaysGeneric() async throws {
        let ha = try await failureToast(for: HomeProviderError.serviceCallFailed(500, "Integration error"))
        #expect(ha.message == "台灯 操作未生效")

        let cloud = try await failureToast(for: MiCloudError.http(500))
        #expect(cloud.message == "台灯 操作未生效")
    }

    @Test("A Mijia account that never signed in is not told its sign-in expired")
    func notLoggedIn() async throws {
        let (message, _) = try await failureToast(for: MiCloudError.notLoggedIn)
        #expect(message == "台灯 操作未生效：未登录米家，请在设置里登录")
    }

    @Test("An error raised before anything reached an offline device keeps its own copy")
    func localErrorOnOfflineDeviceIsNotOffline() async throws {
        let unsupported = try await failureToast(for: MiotCommandError.unsupported("测试"), online: false)
        #expect(unsupported.message == "台灯 操作未生效")

        let unconfigured = try await failureToast(
            for: HomeProviderError.backendNotConfigured(.homeAssistant),
            online: false
        )
        #expect(unconfigured.message == "台灯 操作未生效")
    }

    @Test("A cancelled write shows no failure toast, even for an offline device")
    func cancelledWriteIsSilent() async throws {
        for error: any Error & Sendable in [CancellationError(), URLError(.cancelled)] {
            let store = try await makeStore(error: error, online: false)
            store.setLightEffect("lamp", effect: "Night")
            try await Task.sleep(for: .milliseconds(300))
            #expect(store.toastNotice == nil, "\(error)")
        }
    }

    @Test("An unconfigured backend keeps the generic copy instead of a network error")
    func unconfiguredBackendStaysGeneric() async throws {
        let provider = DualSourceHomeProvider(homeAssistant: nil, xiaomiCloud: nil)
        let miFailure = await capturedProviderError {
            try await provider.execute(.setLightPower(entityId: "miot:did:2", isOn: true))
        }
        let miError = try #require(miFailure)
        #expect(miError == .backendNotConfigured(.xiaomiCloud))
        let miToast = try await failureToast(for: miError)
        #expect(miToast.message == "台灯 操作未生效")

        let haFailure = await capturedProviderError {
            try await provider.execute(.setLightPower(entityId: "light.lamp", isOn: true))
        }
        let haError = try #require(haFailure)
        #expect(haError == .backendNotConfigured(.homeAssistant))
        let haToast = try await failureToast(for: haError)
        #expect(haToast.message == "台灯 操作未生效")
    }

    @Test("An error outside the five categories keeps the generic action copy")
    func unknownKeepsGenericCopy() async throws {
        let (message, store) = try await failureToast(for: MiotCommandError.unsupported("测试"))
        #expect(message == "台灯 操作未生效")
        #expect(store.devices.first?.isActiveOn == false)
    }

    @Test("A failed adjustment rolls the slider back and keeps the adjustment wording")
    func adjustmentRollsBack() async throws {
        let error = try #require(capturedCloudError {
            try MiCloudClient.checkWriteCodes([.object(["code": .number(-704002000)])])
        })
        let (message, store) = try await failureToast(for: error, adjustment: true)
        #expect(message == "台灯 调节未生效：设备拒绝了这次操作（米家错误码 -704002000）")
        let device = try #require(store.devices.first)
        guard case .light(let spec) = device.kind else {
            Issue.record("Expected a light")
            return
        }
        #expect(spec.brightnessPercent == 80)
    }

    @Test("English and Traditional Chinese copy is resolved")
    func otherLanguages() throws {
        let offlineError = try #require(capturedCloudError {
            try MiCloudClient.checkWriteCodes([.object(["code": .number(-704042011)])])
        })
        let rejectedError = try #require(capturedCloudError {
            try MiCloudClient.checkWriteCodes([.object(["code": .number(-704002000)])])
        })
        let tables = LocalizationTests.shippedTablesDirectory
        L10n.$overrideTranslationDirectory.withValue(tables) {
            L10n.$overrideLanguage.withValue(.en) {
                #expect(
                    ControlFailure.userMessage(
                        for: MiCloudError.sessionExpired,
                        deviceName: "Desk Lamp",
                        isDeviceOnline: true,
                        adjustment: false
                    ) == "Desk Lamp action failed: sign-in expired, sign in again in Settings"
                )
                #expect(
                    ControlFailure.userMessage(
                        for: URLError(.timedOut),
                        deviceName: "Desk Lamp",
                        isDeviceOnline: true,
                        adjustment: true
                    ) == "Desk Lamp adjustment failed: network error, check the connection and try again"
                )
            }
            L10n.$overrideLanguage.withValue(.zhHant) {
                #expect(
                    ControlFailure.userMessage(
                        for: offlineError,
                        deviceName: "台灯",
                        isDeviceOnline: true,
                        adjustment: false
                    ) == "台灯 操作未生效：裝置離線，等它重新上線後再試"
                )
                #expect(
                    ControlFailure.userMessage(
                        for: rejectedError,
                        deviceName: "台灯",
                        isDeviceOnline: true,
                        adjustment: false
                    ) == "台灯 操作未生效：裝置拒絕了這次操作（米家錯誤碼 -704002000）"
                )
                #expect(
                    ControlFailure.userMessage(
                        for: HomeProviderError.unauthorized,
                        deviceName: "台灯",
                        isDeviceOnline: true,
                        adjustment: false
                    ) == "台灯 操作未生效：訪問令牌無效，請在設定裡更新"
                )
                #expect(HomeProviderError.backendNotConfigured(.xiaomiCloud).errorDescription == "米家雲端未配置")
                #expect(HomeProviderError.backendNotConfigured(.demo).errorDescription == "當前資料來源未配置")
            }
        }
    }
}

/// A provider whose writes always fail with the error under test.
private actor FailingControlProvider: HomeProvider {
    private let snapshot: SynthesizedHome
    private let error: any Error & Sendable
    private var continuation: AsyncStream<HomeProviderEvent>.Continuation?

    init(snapshot: SynthesizedHome, error: any Error & Sendable) {
        self.snapshot = snapshot
        self.error = error
    }

    nonisolated func events() -> AsyncStream<HomeProviderEvent> {
        AsyncStream { continuation in
            Task { await self.register(continuation) }
        }
    }

    private func register(_ continuation: AsyncStream<HomeProviderEvent>.Continuation) {
        self.continuation = continuation
        continuation.yield(.statusChanged(.connected))
        continuation.yield(.snapshotUpdated(snapshot))
    }

    func execute(_ command: DeviceCommand) async throws {
        throw error
    }

    func refreshNow() async {}
}
