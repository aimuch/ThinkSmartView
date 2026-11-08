import Foundation
import Testing
@testable import MijiaCore

/// Replays MIoT synthesis over one real public spec per device type (#295) and pins the result
/// to `Tests/Fixtures/miot_coverage_golden.json`, which the Windows suite checks too.
/// After an intended change, re-record with `RECORD_MIOT_COVERAGE=1 swift test --filter MiotCoverageBaselineTests`.
struct MiotCoverageBaselineTests {
    enum Category: String, Codable, CaseIterable {
        case matched, misclaimed, genericControllable, genericReadOnly
        case droppedControllable, droppedReadOnly, noUsableProperties
    }

    struct Entry: Codable, Equatable {
        var deviceType: String
        var category: Category
        var service: String?
        var unexposedWritable: Int?
    }

    struct Summary: Codable, Equatable {
        var specs: Int
        var deviceTypes: Int
        var categories: [String: Int]
        var unexposedWritableTotal: Int
    }

    struct Golden: Codable, Equatable {
        var summary: Summary
        var models: [String: Entry]
        /// Models Windows classifies differently today; the Windows suite expects these instead.
        var windowsDivergences: [String: Entry]
    }

    static let noiseServices: Set<String> = ["device-information", "exception-card", "device-custom"]

    static func usableServices(_ spec: MiotDeviceSpec) -> [MiotService] {
        spec.services.filter { !noiseServices.contains($0.shortName) && !$0.shortName.hasSuffix("-custom") }
    }

    static func isControl(_ p: MiotProperty) -> Bool {
        p.isWritable && (p.format == "bool" || p.valueList?.isEmpty == false || p.valueRange != nil)
    }

    static func classify(_ spec: MiotDeviceSpec) -> Entry {
        let deviceType = spec.shortName
        let usable = usableServices(spec)
        if let (service, _) = MiotSynthesizer.primary(for: spec) {
            // A pair the fallback table allows (#296) is the device's own kind, not a misclaim.
            let ownType = MiotSynthesizer.preferredServices(forDeviceType: deviceType).contains(service.shortName)
                || (deviceType.hasSuffix("sensor") && service.shortName == "environment")
                || MiotSynthesizer.fallbackAllows(deviceType: deviceType, service: service.shortName)
            guard ownType else { return Entry(deviceType: deviceType, category: .misclaimed, service: service.shortName) }
            let record = MiDeviceRecord(did: "coverage", name: "", model: "", isOnline: true)
            let polled = Set(MiotSynthesizer.requiredProps(for: record, spec: spec).map { "\($0.siid).\($0.piid)" })
            let unexposed = usable.reduce(0) { count, s in
                count + s.properties.filter { isControl($0) && !polled.contains("\(s.iid).\($0.iid)") }.count
            }
            return Entry(deviceType: deviceType, category: .matched, service: service.shortName, unexposedWritable: unexposed)
        }
        // The generic facet layer is the last rung: a card exists when it finds any control or
        // reading. Only a spec it cannot describe at all stays unsupported (#6).
        let controls = MiotFacetEngine.controls(
            for: spec, did: "coverage", values: [:], primaryServiceId: nil
        )
        if !controls.isEmpty {
            return Entry(
                deviceType: deviceType,
                category: controls.contains { $0.isWritable } ? .genericControllable : .genericReadOnly
            )
        }
        let controllable = usable.contains { s in !s.actions.isEmpty || s.properties.contains(where: isControl) }
        if controllable { return Entry(deviceType: deviceType, category: .droppedControllable) }
        let readable = usable.contains { s in s.properties.contains { $0.isReadable && $0.format != "string" } }
        return Entry(deviceType: deviceType, category: readable ? .droppedReadOnly : .noUsableProperties)
    }

    static func measure() throws -> Golden {
        let specs = try JSONDecoder().decode(
            [String: MiotDeviceSpec].self,
            from: Data(contentsOf: coverageFixture("Tests/Fixtures/miot_coverage_specs.json"))
        )
        let models = specs.mapValues(classify)
        var categories = Dictionary(uniqueKeysWithValues: Category.allCases.map { ($0.rawValue, 0) })
        for entry in models.values { categories[entry.category.rawValue, default: 0] += 1 }
        return Golden(
            summary: Summary(
                specs: models.count,
                deviceTypes: Set(models.values.map(\.deviceType)).count,
                categories: categories,
                unexposedWritableTotal: models.values.compactMap(\.unexposedWritable).reduce(0, +)
            ),
            models: models,
            windowsDivergences: [:]
        )
    }

    @Test func coverageMatchesBaseline() throws {
        var measured = try Self.measure()
        let goldenURL = try coverageFixture("Tests/Fixtures/miot_coverage_golden.json")
        if ProcessInfo.processInfo.environment["RECORD_MIOT_COVERAGE"] == "1" {
            if let previous = try? JSONDecoder().decode(Golden.self, from: Data(contentsOf: goldenURL)) {
                measured.windowsDivergences = previous.windowsDivergences
            }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            try (encoder.encode(measured) + Data("\n".utf8)).write(to: goldenURL)
        }
        let golden = try JSONDecoder().decode(Golden.self, from: Data(contentsOf: goldenURL))

        for model in Set(golden.models.keys).union(measured.models.keys).sorted()
        where golden.models[model] != measured.models[model] {
            Issue.record("\(model): baseline \(String(describing: golden.models[model])), now \(String(describing: measured.models[model]))")
        }
        #expect(golden.summary == measured.summary)
    }

    /// #296: a device type the fallback table does not list is never drawn as another device, and
    /// the listed pairs keep the card they had.
    @Test func fallbackClaimsFollowTheCompatibilityTable() throws {
        let models = try Self.measure().models
        let dropped = [
            "ctest.inskil.duyr",      // insect killer: was a camera
            "asu.projector.apro",     // projector: was a speaker
            "gimi.projector.fm01",
            "123888.tv.xhzn11",       // television: was a speaker
            "xiaomi.split_tv.b1",
            "chuangmi.radio.v1",
            "miot.handdry.hd3",       // hand dryer: was a vacuum
            "miot.flowerpot.aztest",  // flowerpot: was an air conditioner
            "miot.dimmer.hhh",        // dimmer: was a video doorbell
            "shuse.roller.sr01",      // roller shutter: was a lock
            "adp.motor.adfbt6",       // motor controller: was a switch
        ]
        for model in dropped {
            #expect(models[model]?.service == nil, "\(model) must not borrow another device's service")
        }
        let kept: [String: String] = [
            "cuco.acpartner.ap2a": "air-conditioner",  // AC companion outlet
            "chuangmi.door.515a01": "video-doorbell",
            "bymiot.relay.wlv1": "switch",
            "xiaomi.btspker.oh3s": "speaker",
            "careco.nlight.o654": "light",
            "lumi.plug.aq1": "switch",                 // outlet keeps its switch service
        ]
        for (model, service) in kept {
            #expect(models[model]?.category == .matched, "\(model)")
            #expect(models[model]?.service == service, "\(model)")
        }
        #expect(!models.values.contains { $0.category == .misclaimed })
    }

    /// The fixture set is only a baseline if it spans the whole library; 177 types at capture time.
    @Test func fixturesCoverEveryDeviceType() throws {
        let measured = try Self.measure()
        #expect(measured.summary.deviceTypes == 177)
        #expect(measured.models["czmydz.curtain.myf5"]?.deviceType == "curtain")
        #expect(measured.models["xiaomi.fishbowl.m200"]?.deviceType == "fish-tank")
        let specs = try JSONDecoder().decode(
            [String: MiotDeviceSpec].self,
            from: Data(contentsOf: coverageFixture("Tests/Fixtures/miot_coverage_specs.json"))
        )
        let curtainServices = specs["czmydz.curtain.myf5"]?.services(named: "curtain").count
        #expect(curtainServices == 2)
        let withoutOwnService = specs.values.filter { $0.services(named: $0.shortName).isEmpty }
        #expect(!withoutOwnService.isEmpty)
    }
}

private func coverageFixture(_ relative: String) throws -> URL {
    var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    for _ in 0..<8 {
        let candidate = directory.appendingPathComponent(relative)
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("Tests/Fixtures").path) { return candidate }
        directory.deleteLastPathComponent()
    }
    throw CocoaError(.fileNoSuchFile)
}
