import Foundation

/// GitLab new-issue link for a device the panel skipped (#284).
///
/// GitLab prefills only `issue[title]` and `issue[description]`, so the fields the old GitHub issue
/// form asked for (platform, backend, model, symptom) go into the description as Markdown sections.
/// The backend values stay Chinese in every UI language so reports group the same way. The device
/// `id` is a did or an HA device id and never enters the URL.
public enum UnsupportedDeviceFeedbackURL {
    private static let issueURL = "https://gitlab.com/jryang1997/MenuBarMijia/-/issues/new"

    public static func make(for device: UnsupportedDevice) -> URL? {
        guard let backend = backend(for: device.reason) else { return nil }

        var components = URLComponents(string: issueURL)
        components?.queryItems = [
            URLQueryItem(name: "issue[title]", value: title(for: device)),
            URLQueryItem(name: "issue[description]", value: description(for: device, backend: backend)),
        ]
        // URLComponents keeps a literal "+" in a value, but form-urlencoded decoders read it as a
        // space, so escape it explicitly.
        if let encodedQuery = components?.percentEncodedQuery {
            components?.percentEncodedQuery = encodedQuery.replacingOccurrences(of: "+", with: "%2B")
        }
        return components?.url
    }

    private static func description(for device: UnsupportedDevice, backend: String) -> String {
        [
            "### 平台 / Platform\n\nmacOS",
            "### 接入方式 / Backend\n\n\(backend)",
            "### 型号 / Model\n\n\(device.model)",
            "### 现象 / Symptom\n\n\(symptom(for: device))",
        ].joined(separator: "\n\n") + "\n"
    }

    /// The skip reason alone would satisfy the required field with nothing the maintainer can act on,
    /// so it ends with an open prompt for what the user expected.
    private static func symptom(for device: UnsupportedDevice) -> String {
        device.reasonText + "\n\n" + L10n.tr(
            "期望支持的功能（开关、亮度、温度…）：",
            "What should it support (power, brightness, temperature…):"
        )
    }

    private static func title(for device: UnsupportedDevice) -> String {
        L10n.tr(
            "[设备不支持] \(device.name) (\(device.model))",
            "[Unsupported device] \(device.name) (\(device.model))"
        )
    }

    /// The reason names the source, which stays correct when a dual-source scan mixes both backends
    /// in one list. `nil` means the skip was intentional and needs no issue: a filtered duplicate, or a
    /// device the user disabled or hid in Home Assistant.
    private static func backend(for reason: UnsupportedDevice.Reason) -> String? {
        switch reason {
        case .unknownModel, .noControllableItems:
            return "小米云（扫码登录）"
        case .unsupportedEntities:
            return "Home Assistant"
        case .noActiveEntities, .deviceDisabled, .filteredDuplicate:
            return nil
        }
    }
}
