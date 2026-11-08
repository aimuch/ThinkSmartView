import Foundation

// MARK: - External URL Scheme (#57)

/// An action requested by a `menumijia://` link from Raycast, Alfred, Stream Deck, a Shortcut, or a
/// web page.
///
/// Links are easy to trigger from a browser, so handlers must not run them silently:
/// `URLDirectControl` decides whether the user has to confirm the action first (default: confirm).
///
/// `toggle` flips whatever the device is doing now; the parameterized cases set an exact value, so a
/// companion extension can render a slider without guessing the state the app is about to change.
public enum AppURLCommand: Equatable, Sendable {
    case toggle(deviceId: String)
    case scene(name: String)
    case ask(text: String)
    /// `menumijia://power?device=<id>&on=true|false`
    case power(deviceId: String, isOn: Bool)
    /// `menumijia://brightness?device=<id>&percent=0…100`
    case brightness(deviceId: String, percent: Int)
    /// `menumijia://color-temp?device=<id>&kelvin=<k>`
    case colorTemp(deviceId: String, kelvin: Int)
    /// `menumijia://temperature?device=<id>&celsius=<°C>`
    case temperature(deviceId: String, celsius: Double)
    /// `menumijia://fan?device=<id>&percent=<1…100>` or `&preset=<mode>`
    case fan(deviceId: String, percent: Int?, preset: String?)
    /// `menumijia://focus?device=<id>` opens the panel on that device's drawer.
    case focus(deviceId: String)

    /// True when the link changes a device, so the confirmation gate must run first.
    public var controlsDevice: Bool {
        switch self {
        case .focus: return false
        case .toggle, .scene, .ask, .power, .brightness, .colorTemp, .temperature, .fan: return true
        }
    }
}

/// Parsed URL command together with optional companion-bridge authentication and request tracking metadata.
public struct AppURLCommandEnvelope: Equatable, Sendable {
    public let command: AppURLCommand
    public let token: String?
    public let requestId: String?

    public init(command: AppURLCommand, token: String? = nil, requestId: String? = nil) {
        self.command = command
        self.token = token
        self.requestId = requestId
    }
}

/// Built-in quick actions and undo entry points addressable via `menumijia://scene?name=<id>`.
public enum BuiltInURLScene: String, CaseIterable, Equatable, Sendable {
    case lightsOff
    case softNightLight
    case climateSleepOff
    case allOff
    case undo

    public static func resolve(_ raw: String) -> BuiltInURLScene? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let normalized: String
        if trimmed.lowercased().hasPrefix("quick:") {
            normalized = String(trimmed.dropFirst("quick:".count)).trimmingCharacters(in: .whitespacesAndNewlines)
        } else {
            normalized = trimmed
        }
        switch normalized.lowercased() {
        case "lightsoff", "lights_off", "全屋关灯":
            return .lightsOff
        case "softnightlight", "soft_night_light", "柔和夜灯":
            return .softNightLight
        case "climatesleepoff", "climate_sleep_off", "关温控风扇", "睡眠关空调":
            return .climateSleepOff
        case "alloff", "all_off", "一键全关", "全关":
            return .allOff
        case "undo", "撤销上一步", "撤销":
            return .undo
        default:
            return nil
        }
    }

    public var quickAction: QuickAction? {
        switch self {
        case .lightsOff: return .lightsOff
        case .softNightLight: return .softNightLight
        case .climateSleepOff: return .climateSleepOff
        case .allOff, .undo: return nil
        }
    }

    public var displayTitle: String {
        switch self {
        case .lightsOff: return QuickAction.lightsOff.title
        case .softNightLight: return QuickAction.softNightLight.title
        case .climateSleepOff: return QuickAction.climateSleepOff.title
        case .allOff: return L10n.tr("一键全关", "Turn All Off")
        case .undo: return L10n.tr("撤销上一步", "Undo Last Action")
        }
    }
}

/// Parses `menumijia://toggle?device=<id>`, `menumijia://scene?name=<scene>`,
/// `menumijia://ask?text=<sentence>`, and the parameterized companion controls
/// (`power`, `brightness`, `color-temp`, `temperature`, `fan`, `focus`).
///
/// All parameters are required and trimmed. Long values are rejected instead of being forwarded to
/// the cloud assistant, so a stray/malicious link cannot turn into an unbounded request. Out-of-range
/// or non-numeric values are rejected too: a slider that sends 250% must not reach the device.
public enum AppURLParser {
    public static let scheme = "menumijia"
    public static let maximumAskLength = 500
    public static let maximumNameLength = 200
    public static let maximumTokenLength = 200
    public static let maximumRequestIdLength = 120
    public static let maximumURLLength = 4096
    public static let brightnessRange = 0...100
    public static let colorTempRange = 1000...20000
    public static let celsiusRange = -30.0...50.0

    public static func parse(_ url: URL) -> AppURLCommand? {
        parseEnvelope(url)?.command
    }

    public static func parseEnvelope(_ url: URL) -> AppURLCommandEnvelope? {
        guard url.scheme?.lowercased() == scheme,
              url.absoluteString.count <= maximumURLLength,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return nil
        }

        // `menumijia://toggle?...` puts the route in the host; also accept `menumijia:/toggle?...`.
        // `color-temp` and `color_temp` reach the same route as `colortemp`.
        let route = (components.host ?? components.path)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            .lowercased()
            .replacingOccurrences(of: "-", with: "")
            .replacingOccurrences(of: "_", with: "")

        var query: [String: String] = [:]
        for item in components.queryItems ?? [] {
            let key = item.name.lowercased()
            guard query[key] == nil, let raw = item.value else { continue }
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                query[key] = trimmed
            }
        }

        let token: String?
        if let rawToken = query["token"] {
            guard rawToken.count <= maximumTokenLength else { return nil }
            token = rawToken
        } else {
            token = nil
        }

        let requestId: String?
        if let rawId = query["id"] ?? query["request_id"] ?? query["requestid"] ?? query["req"] {
            guard rawId.count <= maximumRequestIdLength else { return nil }
            requestId = rawId
        } else {
            requestId = nil
        }

        let command: AppURLCommand
        switch route {
        case "toggle":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength else { return nil }
            command = .toggle(deviceId: deviceId)
        case "scene":
            guard let name = query["name"], name.count <= maximumNameLength else { return nil }
            command = .scene(name: name)
        case "ask":
            guard let text = query["text"], text.count <= maximumAskLength else { return nil }
            command = .ask(text: text)
        case "power":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength,
                  let raw = query["on"] ?? query["state"],
                  let isOn = boolean(raw) else { return nil }
            command = .power(deviceId: deviceId, isOn: isOn)
        case "brightness":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength,
                  let percent = integer(query["percent"], in: brightnessRange) else { return nil }
            command = .brightness(deviceId: deviceId, percent: percent)
        case "colortemp":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength,
                  let kelvin = integer(query["kelvin"] ?? query["value"], in: colorTempRange) else { return nil }
            command = .colorTemp(deviceId: deviceId, kelvin: kelvin)
        case "temperature":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength,
                  let celsius = decimal(query["celsius"] ?? query["value"]),
                  celsiusRange.contains(celsius) else { return nil }
            command = .temperature(deviceId: deviceId, celsius: celsius)
        case "fan":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength else { return nil }
            // One link names one speed. A percent outside 1…100 is rejected, not dropped, and a
            // link that carries both a percent and a preset is ambiguous: which one wins would
            // depend on this parser's order rather than on the caller's intent (ADR-0013).
            let rawPercent = query["percent"]
            let rawPreset = query["preset"]
            let percent = integer(rawPercent, in: 1...100)
            let preset = rawPreset.flatMap { $0.count <= maximumNameLength ? $0 : nil }
            if rawPercent != nil, percent == nil { return nil }
            if rawPreset != nil, preset == nil { return nil }
            guard (percent != nil) != (preset != nil) else { return nil }
            command = .fan(deviceId: deviceId, percent: percent, preset: preset)
        case "focus":
            guard let deviceId = query["device"], deviceId.count <= maximumNameLength else { return nil }
            command = .focus(deviceId: deviceId)
        default:
            return nil
        }

        return AppURLCommandEnvelope(command: command, token: token, requestId: requestId)
    }

    private static let trueWords: Set<String> = ["1", "true", "on", "yes"]
    private static let falseWords: Set<String> = ["0", "false", "off", "no"]

    static func boolean(_ raw: String) -> Bool? {
        let word = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if trueWords.contains(word) { return true }
        if falseWords.contains(word) { return false }
        return nil
    }

    static func integer(_ raw: String?, in range: ClosedRange<Int>) -> Int? {
        guard let raw,
              let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              range.contains(value) else { return nil }
        return value
    }

    static func decimal(_ raw: String?) -> Double? {
        guard let raw,
              let value = Double(raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              value.isFinite else { return nil }
        return value
    }
}

/// Confirmation gate for external links (#57).
///
/// A web page can navigate to a custom URL scheme without any user gesture the app can verify, so
/// every control action asks inside the app first. Only when the user explicitly turns on
/// “允许 URL 直控” in Settings or presents the local `0600` companion `bridgeToken` does a link run
/// without the confirmation sheet.
public enum URLDirectControl {
    public static let allowDirectControlKey = "allow_url_direct_control"
    public static let minimumBridgeTokenLength = 16

    public static func allowsDirectControl(in defaults: UserDefaults = .standard) -> Bool {
        defaults.bool(forKey: allowDirectControlKey)
    }

    public static func isValidBridgeToken(_ providedToken: String?, expectedToken: String?) -> Bool {
        guard let provided = providedToken?.trimmingCharacters(in: .whitespacesAndNewlines),
              let expected = expectedToken?.trimmingCharacters(in: .whitespacesAndNewlines),
              provided.count >= minimumBridgeTokenLength,
              provided.utf8.count == expected.utf8.count else {
            return false
        }
        var diff: UInt8 = 0
        for (lhs, rhs) in zip(provided.utf8, expected.utf8) {
            diff |= lhs ^ rhs
        }
        return diff == 0
    }

    public static func requiresConfirmation(allowDirectControl: Bool) -> Bool {
        !allowDirectControl
    }

    public static func requiresConfirmation(
        allowDirectControl: Bool,
        providedToken: String?,
        expectedToken: String?
    ) -> Bool {
        if allowDirectControl { return false }
        if isValidBridgeToken(providedToken, expectedToken: expectedToken) { return false }
        return true
    }

    public static func requiresConfirmation(
        in defaults: UserDefaults = .standard,
        providedToken: String? = nil,
        expectedToken: String? = nil
    ) -> Bool {
        requiresConfirmation(
            allowDirectControl: allowsDirectControl(in: defaults),
            providedToken: providedToken,
            expectedToken: expectedToken
        )
    }
}
