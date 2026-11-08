import Foundation
import Testing
@testable import MijiaCore

/// The purifier card's label and colour come from one grade (#arch-review #3).
struct AirQualityLevelTests {
    struct Corpus: Decodable {
        struct Case: Decodable { let raw: String?; let pm25: Int?; let level: String? }
        let cases: [Case]
    }

    @Test("Shared air-quality golden matches AirQualityLevel")
    func goldenParity() throws {
        let url = try GoldenCorpusTests.locateGoldenFixture("Tests/Fixtures/air_quality_golden.json")
        let corpus = try JSONDecoder().decode(Corpus.self, from: Data(contentsOf: url))
        for item in corpus.cases {
            let level = AirQualityLevel(raw: item.raw, pm25: item.pm25)
            #expect(level?.rawValue == item.level, "raw \(item.raw ?? "nil") pm25 \(item.pm25.map(String.init) ?? "nil")")
        }
    }

    @Test("A raw 优 without PM2.5 is labelled and toned excellent")
    func rawChineseGradeHasGoodTone() {
        let level = AirQualityLevel(raw: "优", pm25: nil)
        #expect(level == .excellent)
        #expect(level?.tone == .good)
        #expect(SmartDevice.localizeAirQuality("优", pm25: nil) == level?.label)
    }

    @Test("An unknown raw grade keeps its text and has no tone")
    func unknownRawGrade() {
        #expect(AirQualityLevel(raw: "Smoky", pm25: 12) == nil)
        #expect(SmartDevice.localizeAirQuality("Smoky", pm25: 12) == "Smoky")
    }
}
