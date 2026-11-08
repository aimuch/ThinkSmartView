import Foundation
import Testing
@testable import MijiaCore

/// #299: a MIoT `airer` is a cover card that rises and lowers. The fixtures are verbatim released
/// instances from miot-spec.org, and all three also publish a `light` service:
/// - `aupu.airer.d350m`: motor control and a status word only, no position;
/// - `hotata.airer.q31zm`: adds a read-only current position;
/// - `xiaomi.airer.maxb`: current and writable target position (raw range 12…100) and limit words.
/// An airer reports how far it has lowered (0 at the top), so the card shows height: 100 is up.
struct AirerDeviceTests {
    static let motorOnly = "aupu.airer.d350m"
    static let currentOnly = "hotata.airer.q31zm"
    static let withTarget = "xiaomi.airer.maxb"

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

    /// `status` and the raw positions are keyed by piid on siid 2.
    private func airer(
        _ model: String,
        status: Int = 0,
        positions: [Int: Int] = [:]
    ) throws -> (SmartDevice, CoverSpec, MiotDeviceSpec) {
        let spec = try spec(model)
        let record = MiDeviceRecord(did: "airer1", name: "阳台晾衣架", model: model, isOnline: true)
        var values: [MiotPropKey: JSONValue] = [.init(did: "airer1", siid: 2, piid: 2): .number(Double(status))]
        for (piid, raw) in positions {
            values[.init(did: "airer1", siid: 2, piid: piid)] = .number(Double(raw))
        }
        let home = MiotSynthesizer.synthesize(devices: [record], rooms: [], specs: [model: spec], values: values)
        let device = try #require(home.devices.first)
        guard case .cover(let cover) = device.kind else {
            Issue.record("\(model) was synthesized as \(device.kind), not a cover")
            throw CocoaError(.featureUnsupported)
        }
        return (device, cover, spec)
    }

    @Test("All three real airers become airer cards with Up / Down / Pause, never a lamp card")
    func airersAreCoverCardsNotLamps() throws {
        for model in [Self.motorOnly, Self.currentOnly, Self.withTarget] {
            let (device, cover, spec) = try airer(model)
            #expect(!spec.services(named: "light").isEmpty, "\(model) fixture should carry a light service")
            #expect(MiotSynthesizer.supportedServiceName(for: spec) == "airer")
            #expect(cover.subtype == .clothesAirer)
            #expect(cover.entityId == "miot:airer1:2")
            #expect(cover.openDescription == "Up")
            #expect(cover.closeDescription == "Down")
            #expect(cover.pauseDescription == "Pause")
            #expect(device.sfSymbolName == "hanger")
        }
        // Only the target-position airer can be dragged.
        #expect(try airer(Self.withTarget).1.positionWritable)
        #expect(try !airer(Self.currentOnly).1.positionWritable)
        #expect(try !airer(Self.motorOnly).1.positionWritable)
    }

    @Test("Position reads flip: raw distance lowered becomes height, with airer wording")
    func positionReadIsInverted() throws {
        try L10n.$overrideLanguage.withValue(.zhHans) {
            let top = try airer(Self.withTarget, positions: [11: 0, 12: 0])
            #expect(top.1.positionPercent == 100)
            #expect(top.0.statusSubtitle == "已升到顶")

            let bottom = try airer(Self.withTarget, positions: [11: 100, 12: 100])
            #expect(bottom.1.positionPercent == 0)
            #expect(bottom.0.statusSubtitle == "已降到底")

            let middle = try airer(Self.withTarget, positions: [11: 30, 12: 30])
            #expect(middle.1.positionPercent == 70)
            #expect(middle.0.statusSubtitle == "高度 70%")

            #expect(try airer(Self.currentOnly, positions: [11: 40]).1.positionPercent == 60)
        }
    }

    @Test("Status words: rising and lowering are travel, the upper limit is the top")
    func statusWordsUseAirerDirection() throws {
        try L10n.$overrideLanguage.withValue(.zhHans) {
            let rising = try airer(Self.motorOnly, status: 1)
            #expect(rising.1.motion == .opening)
            #expect(rising.0.statusSubtitle == "正在上升")

            let lowering = try airer(Self.motorOnly, status: 2)
            #expect(lowering.1.motion == .closing)
            #expect(lowering.0.statusSubtitle == "正在下降")

            #expect(CoverStatusWord("Stop Upper Limit") == .open)
            #expect(CoverStatusWord("Stop Lower Limit") == .closed)
        }
    }

    @Test("Position writes flip and scale back to the raw range; motor words map to their codes")
    func positionWriteIsInvertedAndScaled() throws {
        let spec = try spec(Self.withTarget)
        func write(_ percent: Double) throws -> MiotWrite {
            try MiotCommandMapper.map(.setNumberValue(entityId: "miot:airer1:2", value: percent), spec: spec)
        }
        #expect(try write(70) == .setProps([.init(did: "airer1", siid: 2, piid: 12, value: .number(30))]))
        #expect(try write(0) == .setProps([.init(did: "airer1", siid: 2, piid: 12, value: .number(100))]))
        // Height 100 is raw 0, below the spec's minimum of 12: the write clamps to the top it allows.
        #expect(try write(100) == .setProps([.init(did: "airer1", siid: 2, piid: 12, value: .number(12))]))

        func motor(_ word: String) throws -> MiotWrite {
            try MiotCommandMapper.map(.setSelectOption(entityId: "miot:airer1:2", option: word), spec: spec)
        }
        #expect(try motor("Up") == .setProps([.init(did: "airer1", siid: 2, piid: 5, value: .number(1))]))
        #expect(try motor("Down") == .setProps([.init(did: "airer1", siid: 2, piid: 5, value: .number(2))]))
        #expect(try motor("Pause") == .setProps([.init(did: "airer1", siid: 2, piid: 5, value: .number(0))]))
    }

    // MARK: Card tap

    @MainActor
    private func makeStore(_ devices: [SmartDevice]) async throws -> (HomeStore, MockHomeProvider) {
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: devices))
        let store = HomeStore(provider: mock, defaultsSuiteName: "test.airer.\(UUID().uuidString)")
        store.controlDebounceInterval = .zero
        try await store.waitForTestSnapshot()
        return (store, mock)
    }

    @Test("Tap: a moving airer stops, a lowered one rises after confirmation, a raised one lowers")
    @MainActor
    func tapStopsRaisesAndLowers() async throws {
        let (moving, _, _) = try airer(Self.withTarget, status: 2, positions: [11: 40, 12: 80])
        let (movingStore, movingMock) = try await makeStore([moving])
        #expect(await movingStore.togglePrimary(try #require(movingStore.devices.first))?.value == true)
        #expect(await movingMock.executedCommands == [.setSelectOption(entityId: "miot:airer1:2", option: "Pause")])

        // Raising is the airer's "open", which #167 confirms like a window opener.
        let (lowered, _, _) = try airer(Self.withTarget, positions: [11: 100, 12: 100])
        let (loweredStore, loweredMock) = try await makeStore([lowered])
        var prompts = 0
        loweredStore.manualControlConfirmationHandler = { _ in
            prompts += 1
            return true
        }
        #expect(await loweredStore.togglePrimary(try #require(loweredStore.devices.first))?.value == true)
        #expect(prompts == 1)
        #expect(await loweredMock.executedCommands == [.setSelectOption(entityId: "miot:airer1:2", option: "Up")])
        #expect(loweredStore.devices.first?.airerHeight == 100)

        let (raised, _, _) = try airer(Self.withTarget, positions: [11: 0, 12: 0])
        let (raisedStore, raisedMock) = try await makeStore([raised])
        raisedStore.manualControlConfirmationHandler = { _ in
            Issue.record("lowering must not ask")
            return false
        }
        #expect(await raisedStore.togglePrimary(try #require(raisedStore.devices.first))?.value == true)
        #expect(await raisedMock.executedCommands == [.setSelectOption(entityId: "miot:airer1:2", option: "Down")])
        #expect(raisedStore.devices.first?.airerHeight == 0)
    }
}

private extension SmartDevice {
    var airerHeight: Int? {
        guard case .cover(let spec) = kind else { return nil }
        return spec.positionPercent
    }
}
