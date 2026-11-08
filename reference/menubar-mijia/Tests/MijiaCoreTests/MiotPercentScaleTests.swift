import Foundation
import Testing
@testable import MijiaCore

/// Reading a percent off a card and writing it back must land on the raw value it came from.
struct MiotPercentScaleTests {
    private static func levels(_ values: [Int]) -> MiotProperty {
        MiotProperty(
            iid: 2,
            type: "urn:miot-spec-v2:property:fan-level:00000016:test:1",
            format: "uint8",
            access: ["read", "write"],
            valueList: values.map { MiotValueListItem(value: $0, description: "Level \($0)") }
        )
    }

    private static func range(_ name: String, _ lo: Double, _ hi: Double, _ step: Double = 1) -> MiotProperty {
        MiotProperty(
            iid: 2,
            type: "urn:miot-spec-v2:property:\(name):00000000:test:1",
            format: "uint16",
            access: ["read", "write"],
            valueRange: [lo, hi, step]
        )
    }

    /// The wire value the writer sends: clamped to the range and rounded for integer formats.
    private static func wire(_ raw: Double, _ p: MiotProperty) -> Double {
        var n = raw
        if let lo = p.rangeMin, let hi = p.rangeMax { n = min(hi, max(lo, n)) }
        return p.isIntegerFormat ? n.rounded() : n
    }

    @Test func levelListStartingAtOneRoundTripsEveryLevel() {
        let p = Self.levels([1, 2, 3])
        for level in [1, 2, 3] {
            let percent = MiotPercentScale.percent(fromRaw: Double(level), property: p, curve: .speed)
            let back = MiotPercentScale.raw(fromPercent: Double(percent), property: p, curve: .speed)
            #expect(back == Double(level), "level \(level) read as \(percent)% wrote back \(back)")
        }
    }

    @Test func speedRangeStartingAboveOneRoundTrips() {
        let p = Self.range("fan-level", 2, 5)
        for raw in 2...5 {
            let percent = MiotPercentScale.percent(fromRaw: Double(raw), property: p, curve: .speed)
            let back = Self.wire(MiotPercentScale.raw(fromPercent: Double(percent), property: p, curve: .speed), p)
            #expect(back == Double(raw))
        }
    }

    @Test func volumeWithNonZeroMinimumRoundTrips() {
        let p = Self.range("volume", 1, 100)
        for raw in [1, 30, 50, 99, 100] {
            let percent = MiotPercentScale.percent(fromRaw: Double(raw), property: p, curve: .proportional)
            let back = Self.wire(MiotPercentScale.raw(fromPercent: Double(percent), property: p, curve: .proportional), p)
            #expect(back == Double(raw))
        }
    }

    /// Every percent-scaled property in the 177 real coverage specs: a written-back percent reads
    /// back as the same percent, and a level list lands on the exact level.
    @Test func everyCoverageSpecPropertyIsStableUnderWriteBack() throws {
        let specs = try JSONDecoder().decode(
            [String: MiotDeviceSpec].self,
            from: Data(contentsOf: fixture("Tests/Fixtures/miot_coverage_specs.json"))
        )
        let scaled: [String: MiotPercentScale.Curve] = [
            "brightness": .proportional, "volume": .proportional, "target-position": .proportional,
            "fan-level": .speed, "favorite-fan-level": .speed, "favorite-level": .speed, "favorite-speed": .speed,
        ]
        var checked = 0
        for (model, spec) in specs {
            for service in spec.services {
                for p in service.properties where p.isWritable {
                    guard let curve = scaled[p.shortName] else { continue }
                    let raws: [Double]
                    if let list = p.valueList, !list.isEmpty, curve == .speed, p.rangeMax == nil {
                        raws = list.map { Double($0.value) }
                    } else if let lo = p.rangeMin, let hi = p.rangeMax, hi > lo {
                        let step = max(p.rangeStep ?? 1, (hi - lo) / 50)
                        raws = Array(stride(from: lo, through: hi, by: step))
                    } else {
                        continue
                    }
                    for raw in raws {
                        let percent = MiotPercentScale.percent(fromRaw: raw, property: p, curve: curve)
                        let back = Self.wire(MiotPercentScale.raw(fromPercent: Double(percent), property: p, curve: curve), p)
                        let again = MiotPercentScale.percent(fromRaw: back, property: p, curve: curve)
                        #expect(again == percent, "\(model) \(p.shortName) raw \(raw): \(percent)% → \(back) → \(again)%")
                        if p.valueList?.isEmpty == false, p.rangeMax == nil {
                            #expect(back == raw, "\(model) \(p.shortName) level \(raw) wrote back \(back)")
                        }
                        checked += 1
                    }
                }
            }
        }
        #expect(checked > 100)
    }
}

private func fixture(_ relative: String) throws -> URL {
    var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    for _ in 0..<8 {
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("Tests/Fixtures").path) {
            return directory.appendingPathComponent(relative)
        }
        directory.deleteLastPathComponent()
    }
    throw CocoaError(.fileNoSuchFile)
}
