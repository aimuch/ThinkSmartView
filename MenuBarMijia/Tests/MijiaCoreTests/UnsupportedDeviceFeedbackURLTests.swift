import Foundation
import Testing
@testable import MijiaCore

/// #284: the "report this device" link prefills a GitLab issue title and a sectioned description.
struct UnsupportedDeviceFeedbackURLTests {
    private struct Form {
        let url: URL
        let items: [String: String]

        init(_ url: URL) {
            self.url = url
            let queryItems = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            items = Dictionary(queryItems.map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { first, _ in first })
        }

        subscript(name: String) -> String? { items[name] }
        var title: String? { items["issue[title]"] }
        var description: String { items["issue[description]"] ?? "" }

        func section(_ heading: String) -> String? {
            let marker = "### \(heading)\n\n"
            guard let start = description.range(of: marker) else { return nil }
            let rest = description[start.upperBound...]
            let end = rest.range(of: "\n\n### ")?.lowerBound ?? rest.endIndex
            return rest[..<end].trimmingCharacters(in: .newlines)
        }
    }

    private func form(language: AppLanguage, device: UnsupportedDevice) throws -> Form {
        let url = try L10n.$overrideLanguage.withValue(language) {
            try #require(UnsupportedDeviceFeedbackURL.make(for: device))
        }
        return Form(url)
    }

    @Test("Chinese UI: the title and symptom are localized and no device id is sent")
    func chinesePrefill() throws {
        let device = UnsupportedDevice(
            id: "did-700000001",
            name: "客厅新风机",
            model: "acme.fan.v1",
            reason: .unknownModel
        )
        let form = try form(language: .zhHans, device: device)

        #expect(form.url.absoluteString.hasPrefix("https://gitlab.com/jryang1997/MenuBarMijia/-/issues/new?"))
        #expect(form.title == "[设备不支持] 客厅新风机 (acme.fan.v1)")
        #expect(form.section("平台 / Platform") == "macOS")
        #expect(form.section("接入方式 / Backend") == "小米云（扫码登录）")
        #expect(form.section("型号 / Model") == "acme.fan.v1")
        #expect(form.section("现象 / Symptom") == "没有取到米家设备规格\n\n期望支持的功能（开关、亮度、温度…）：")
        #expect(Set(form.items.keys) == ["issue[title]", "issue[description]"])
        #expect(!form.items.values.contains { $0.contains(device.id) })
    }

    @Test("English UI: the title and symptom are localized and no device id is sent")
    func englishPrefill() throws {
        let device = UnsupportedDevice(
            id: "dev-secret-42",
            name: "Air Fryer",
            model: "acme.fryer",
            reason: .unsupportedEntities
        )
        let form = try form(language: .en, device: device)

        #expect(form.title == "[Unsupported device] Air Fryer (acme.fryer)")
        #expect(form.section("平台 / Platform") == "macOS")
        #expect(form.section("接入方式 / Backend") == "Home Assistant")
        #expect(form.section("型号 / Model") == "acme.fryer")
        #expect(form.section("现象 / Symptom") == "Entity type not supported yet\n\nWhat should it support (power, brightness, temperature…):")
        #expect(Set(form.items.keys) == ["issue[title]", "issue[description]"])
        #expect(!form.items.values.contains { $0.contains(device.id) })
    }

    @Test("Special characters in the name and model survive URL encoding")
    func specialCharactersRoundTrip() throws {
        let name = "客厅 & 灯 = #1 100%"
        let model = "acme model=v1&x#y%2F"
        let device = UnsupportedDevice(id: "did-secret", name: name, model: model, reason: .unknownModel)
        let form = try form(language: .zhHans, device: device)

        #expect(form.title == "[设备不支持] \(name) (\(model))")
        #expect(form.section("型号 / Model") == model)

        let encoded = URLComponents(url: form.url, resolvingAgainstBaseURL: false)?.percentEncodedQuery ?? ""
        #expect(!encoded.contains(" "))
        for escape in ["%26", "%3D", "%23", "%25"] {
            #expect(encoded.contains(escape))
        }
    }

    @Test("Plus signs survive form-urlencoded decoding")
    func plusSignsSurviveFormDecoding() throws {
        let name = "客厅 & 灯 = #1 100% + plus?"
        let model = "acme model=v1&x#y%2F+z"
        let device = UnsupportedDevice(id: "did-secret", name: name, model: model, reason: .unknownModel)
        let form = try form(language: .zhHans, device: device)

        let encoded = URLComponents(url: form.url, resolvingAgainstBaseURL: false)?.percentEncodedQuery ?? ""
        #expect(encoded.contains("%2B"))
        #expect(!encoded.contains("+"))

        // WHATWG form-urlencoded decoders read a raw "+" as a space.
        let formDecoded = encoded
            .replacingOccurrences(of: "+", with: " ")
            .removingPercentEncoding ?? ""
        #expect(formDecoded.contains("[设备不支持] \(name) (\(model))"))
        #expect(formDecoded.contains("### 型号 / Model\n\n\(model)"))
    }

    @Test("Intentional skips (duplicates, devices disabled or hidden in HA) get no feedback link")
    func intentionalSkipsHaveNoLink() {
        for reason: UnsupportedDevice.Reason in [.filteredDuplicate, .deviceDisabled, .noActiveEntities] {
            let device = UnsupportedDevice(
                id: "did-gateway",
                name: "网关",
                model: "lumi.gateway.mgl03",
                reason: reason
            )
            #expect(UnsupportedDeviceFeedbackURL.make(for: device) == nil, "\(reason)")
        }
    }

    @Test("Traditional Chinese UI localizes the symptom prompt")
    func traditionalChinesePrompt() throws {
        let device = UnsupportedDevice(id: "x", name: "燈", model: "acme.light", reason: .unknownModel)
        let url = try L10n.$overrideTranslationDirectory.withValue(LocalizationTests.shippedTablesDirectory) {
            try L10n.$overrideLanguage.withValue(.zhHant) {
                try #require(UnsupportedDeviceFeedbackURL.make(for: device))
            }
        }
        #expect(Form(url).section("现象 / Symptom")?.hasSuffix("期望支援的功能（開關、亮度、溫度…）：") == true)
    }
}
