import Foundation

// MARK: - Devices the Panel Skipped (#45)

/// A device that exists in the account/registry but never became a panel card.
///
/// `id` is kept only for SwiftUI's `ForEach`; it is the raw did/HA device id and is deliberately
/// excluded from `DiagnosticsReportBuilder` output.
public struct UnsupportedDevice: Identifiable, Equatable, Sendable {
    public enum Reason: String, Equatable, Sendable, CaseIterable {
        /// Mi Cloud: no MIoT spec for the model.
        case unknownModel
        /// Mi Cloud: the spec exists, but neither a dedicated builder nor the generic facet
        /// layer found a control or a reading (#6).
        case noControllableItems
        /// Home Assistant: none of the active entities use a domain the panel renders.
        case unsupportedEntities
        /// Home Assistant: every entity is disabled or hidden in the registry.
        case noActiveEntities
        /// Home Assistant: the device itself is disabled.
        case deviceDisabled
        /// DLNA / Apple TV duplicates and gateway indicator devices, skipped on purpose.
        case filteredDuplicate
    }

    public var id: String
    public var name: String
    public var model: String
    public var reason: Reason

    public init(id: String, name: String, model: String, reason: Reason) {
        self.id = id
        self.name = name
        self.model = model
        self.reason = reason
    }

    public var reasonText: String {
        switch reason {
        case .unknownModel:
            return L10n.tr("没有取到米家设备规格", "No Xiaomi device spec for this model")
        case .noControllableItems:
            return L10n.tr("该设备没有向云端开放可控项", "This device exposes no controllable items to the cloud")
        case .unsupportedEntities:
            return L10n.tr("实体类型暂不支持", "Entity type not supported yet")
        case .noActiveEntities:
            return L10n.tr("实体已在 Home Assistant 中禁用或隐藏", "Entities are disabled or hidden in Home Assistant")
        case .deviceDisabled:
            return L10n.tr("设备已在 Home Assistant 中禁用", "Device is disabled in Home Assistant")
        case .filteredDuplicate:
            return L10n.tr("重复发现或网关指示灯，已按设计跳过", "Duplicate discovery or gateway indicator, skipped by design")
        }
    }
}

/// Pure diff between a backend's raw registry and the panel's synthesized home (#45).
///
/// Both functions mirror the skip rules of `MiotSynthesizer` / `DeviceSynthesizer`; they never
/// mutate the registries, so calling them cannot change what the panel shows.
public enum UnsupportedDeviceCollector {
    // MARK: Mi Cloud

    public static func miCloud(
        devices: [MiDeviceRecord],
        specs: [String: MiotDeviceSpec],
        synthesized: SynthesizedHome
    ) -> [UnsupportedDevice] {
        // A panel whose keys are listed as split devices is shown through those keys only.
        let splitParents = Set(devices.compactMap(\.splitParentDid))
        let synthesizedIds = Set(synthesized.devices.map(\.id))
        let synthesizedDIDs = Set(synthesized.deviceIdentities.values.flatMap(\.dids))

        var out: [UnsupportedDevice] = []
        for record in devices where !splitParents.contains(record.did) {
            // A dedicated card or a generic fallback card both count as shown (#6).
            if synthesizedIds.contains(record.did) || synthesizedDIDs.contains(record.did) { continue }
            out.append(UnsupportedDevice(
                id: record.did,
                name: record.name,
                model: record.model,
                reason: specs[record.model] == nil ? .unknownModel : .noControllableItems
            ))
        }
        return sorted(out)
    }

    // MARK: Home Assistant

    public static func homeAssistant(
        devices: [HADeviceEntry],
        entities: [HAEntityEntry],
        states: [String: HAEntityState] = [:],
        synthesized: SynthesizedHome
    ) -> [UnsupportedDevice] {
        let synthesizedIds = Set(synthesized.devices.map(\.id))
        var activeEntitiesByDevice: [String: Int] = [:]
        // A doorbell `event` entity is handled as a ring source (#308), not an unsupported entity.
        var doorbellDevices: Set<String> = []
        for entity in entities {
            guard entity.disabled_by == nil, entity.hidden_by == nil, let deviceId = entity.device_id else { continue }
            if let state = states[entity.entity_id], DoorbellRing.isDoorbellEvent(state) {
                doorbellDevices.insert(deviceId)
                continue
            }
            activeEntitiesByDevice[deviceId, default: 0] += 1
        }

        var out: [UnsupportedDevice] = []
        for device in devices {
            let name = device.name_by_user ?? device.name ?? L10n.tr("未命名设备", "Unnamed Device")
            let model = device.model ?? ""
            if device.disabled_by != nil {
                out.append(UnsupportedDevice(id: device.id, name: name, model: model, reason: .deviceDisabled))
                continue
            }
            // Same duplicates the synthesizer drops before considering entities.
            if model == "hyperDLNA" || model.hasPrefix("Apple TV") || model.hasPrefix("lumi.gateway") {
                out.append(UnsupportedDevice(id: device.id, name: name, model: model, reason: .filteredDuplicate))
                continue
            }
            let activeCount = activeEntitiesByDevice[device.id] ?? 0
            if activeCount == 0, doorbellDevices.contains(device.id) {
                continue
            } else if activeCount == 0 {
                out.append(UnsupportedDevice(id: device.id, name: name, model: model, reason: .noActiveEntities))
            } else if !synthesizedIds.contains(device.id)
                        && !synthesizedIds.contains(where: { $0.hasPrefix("\(device.id)#") }) {
                out.append(UnsupportedDevice(id: device.id, name: name, model: model, reason: .unsupportedEntities))
            }
        }
        return sorted(out)
    }

    private static func sorted(_ devices: [UnsupportedDevice]) -> [UnsupportedDevice] {
        devices.sorted {
            if $0.name != $1.name { return $0.name < $1.name }
            return $0.model < $1.model
        }
    }
}

// MARK: - Copy Diagnostics (#45)

/// Renders the "复制诊断信息" Markdown.
///
/// Privacy contract: the input carries no did, token, server address, IP or account id, so the
/// report cannot leak one. Do not widen `Input` with raw identifiers — the redaction is structural,
/// not a regex applied afterwards.
public enum DiagnosticsReportBuilder {
    public struct ModelCount: Equatable, Sendable {
        public var model: String
        public var count: Int

        public init(model: String, count: Int) {
            self.model = model
            self.count = count
        }
    }

    /// A crash report as the app may see it: the file name and when the file was last modified.
    ///
    /// Only the name decides whether the report belongs to this app. The name never reaches the
    /// rendered report and the file contents are never read, so a stack trace, a user name or a
    /// device payload cannot leak through this type.
    public struct CrashReportStamp: Equatable, Sendable {
        public var fileName: String
        public var modifiedAt: Date

        public init(fileName: String, modifiedAt: Date) {
            self.fileName = fileName
            self.modifiedAt = modifiedAt
        }
    }

    /// How many local crashes happened inside the reporting window, and when the latest one did.
    public struct CrashSummary: Equatable, Sendable {
        public var count: Int
        public var lastAt: Date?

        public init(count: Int, lastAt: Date?) {
            self.count = count
            self.lastAt = lastAt
        }
    }

    /// The window the local crash summary covers. Shared so the file scan and the report cannot drift.
    public static let crashReportWindow: TimeInterval = 7 * 24 * 60 * 60

    public struct Input: Equatable, Sendable {
        public var appVersion: String
        public var buildNumber: String
        public var macOSVersion: String
        public var backendTitle: String
        public var miCloudRegion: String
        public var deviceCount: Int
        public var onlineDeviceCount: Int
        public var hiddenDeviceCount: Int
        public var models: [ModelCount]
        public var unsupported: [UnsupportedDevice]
        public var recentErrorCount: Int
        public var lastErrorAt: Date?
        public var recentCrashTimestamps: [Date]
        public var extraSettings: [String]

        public init(
            appVersion: String,
            buildNumber: String,
            macOSVersion: String,
            backendTitle: String,
            miCloudRegion: String,
            deviceCount: Int,
            onlineDeviceCount: Int,
            hiddenDeviceCount: Int,
            models: [ModelCount],
            unsupported: [UnsupportedDevice] = [],
            recentErrorCount: Int = 0,
            lastErrorAt: Date? = nil,
            recentCrashTimestamps: [Date] = [],
            extraSettings: [String] = []
        ) {
            self.appVersion = appVersion
            self.buildNumber = buildNumber
            self.macOSVersion = macOSVersion
            self.backendTitle = backendTitle
            self.miCloudRegion = miCloudRegion
            self.deviceCount = deviceCount
            self.onlineDeviceCount = onlineDeviceCount
            self.hiddenDeviceCount = hiddenDeviceCount
            self.models = models
            self.unsupported = unsupported
            self.recentErrorCount = recentErrorCount
            self.lastErrorAt = lastErrorAt
            self.recentCrashTimestamps = recentCrashTimestamps
            self.extraSettings = extraSettings
        }
    }

    /// Timestamps of this app's crash reports, newest last, that fall inside the reporting window.
    ///
    /// A report counts when its file name starts with `MenuBarMijia` and ends in `.ips` or `.crash`,
    /// and its modification time is between `window` before `now` and `now`. Future-dated files are
    /// skipped, because a modification time after "now" comes from a clock change, not a real crash.
    public static func crashTimestamps(
        from stamps: [CrashReportStamp],
        now: Date = Date(),
        window: TimeInterval = crashReportWindow
    ) -> [Date] {
        let earliest = now.addingTimeInterval(-window)
        return stamps
            .filter { stamp in
                guard stamp.fileName.hasPrefix("MenuBarMijia") else { return false }
                let suffix = stamp.fileName.split(separator: ".").last.map(String.init)?.lowercased()
                guard suffix == "ips" || suffix == "crash" else { return false }
                return stamp.modifiedAt >= earliest && stamp.modifiedAt <= now
            }
            .map(\.modifiedAt)
            .sorted()
    }

    /// The count and latest time of the timestamps inside the reporting window.
    public static func crashSummary(
        _ timestamps: [Date],
        now: Date = Date(),
        window: TimeInterval = crashReportWindow
    ) -> CrashSummary {
        let earliest = now.addingTimeInterval(-window)
        let inside = timestamps.filter { $0 >= earliest && $0 <= now }
        return CrashSummary(count: inside.count, lastAt: inside.max())
    }

    public static func markdown(_ input: Input, now: Date = Date()) -> String {
        var lines: [String] = []
        lines.append("## MenuBarMijia \(L10n.tr("诊断信息", "Diagnostics"))")
        lines.append("")
        lines.append("- \(L10n.tr("版本", "Version")): \(input.appVersion) (\(input.buildNumber))")
        lines.append("- macOS: \(input.macOSVersion)")
        lines.append("- \(L10n.tr("当前后端", "Backend")): \(input.backendTitle)")
        lines.append("- \(L10n.tr("米家地区", "Mi Cloud Region")): \(input.miCloudRegion)")
        lines.append("- \(L10n.tr("设备", "Devices")): \(input.deviceCount) (\(L10n.tr("在线", "online")) \(input.onlineDeviceCount), \(L10n.tr("隐藏", "hidden")) \(input.hiddenDeviceCount))")
        if !input.extraSettings.isEmpty {
            lines.append("- \(L10n.tr("设置", "Settings")): \(input.extraSettings.joined(separator: " · "))")
        }

        lines.append("")
        lines.append("### \(L10n.tr("设备型号", "Device Models"))")
        if input.models.isEmpty {
            lines.append("- \(L10n.tr("暂无设备", "No devices"))")
        } else {
            for entry in input.models.sorted(by: { lhs, rhs in
                if lhs.count != rhs.count { return lhs.count > rhs.count }
                return lhs.model < rhs.model
            }) {
                let model = entry.model.isEmpty ? L10n.tr("未知型号", "Unknown model") : entry.model
                lines.append("- `\(model)`: \(entry.count)")
            }
        }

        lines.append("")
        lines.append("### \(L10n.tr("未能显示在面板的设备", "Devices Not Shown in the Panel"))")
        if input.unsupported.isEmpty {
            lines.append("- \(L10n.tr("未发现", "None found"))")
        } else {
            for device in input.unsupported {
                let model = device.model.isEmpty ? L10n.tr("未知型号", "Unknown model") : device.model
                lines.append("- \(device.name) (`\(model)`) — \(device.reasonText)")
            }
        }

        lines.append("")
        lines.append("### \(L10n.tr("近期错误", "Recent Errors"))")
        if input.recentErrorCount == 0 {
            lines.append("- \(L10n.tr("无", "None"))")
        } else if let last = input.lastErrorAt {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd HH:mm"
            let stamp = formatter.string(from: last)
            lines.append("- \(input.recentErrorCount) \(L10n.tr("次（最近一次 \(stamp)）", "time(s), last at \(stamp)"))")
        } else {
            lines.append("- \(input.recentErrorCount) \(L10n.tr("次", "time(s)"))")
        }

        lines.append("")
        lines.append("### \(L10n.tr("本地崩溃", "Local Crashes"))")
        let crashes = crashSummary(input.recentCrashTimestamps, now: now)
        if let last = crashes.lastAt {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = "yyyy-MM-dd"
            lines.append("- " + L10n.format(
                "最近 7 天 %@ 次（最近一次 %@）",
                "%@ in the last 7 days, last at %@",
                String(crashes.count),
                formatter.string(from: last)
            ))
        } else {
            lines.append("- " + L10n.format("最近 7 天 %@ 次", "%@ in the last 7 days", "0"))
        }

        lines.append("")
        lines.append("_\(L10n.tr("此报告不包含设备 ID、令牌、服务器地址、账号信息、崩溃堆栈或文件路径。", "This report contains no device IDs, tokens, server addresses, account information, crash stacks, or file paths."))_")
        return lines.joined(separator: "\n")
    }
}
