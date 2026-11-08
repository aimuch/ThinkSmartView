import Foundation

/// Data-driven rules for generic MIoT facets (#303).
/// The document is `Resources/deviceProfiles.json`, byte-identical to
/// `windows/src/core/miCloud/deviceProfiles.json`.
public struct DeviceProfiles: Codable, Equatable, Sendable {
    public struct Noise: Codable, Equatable, Sendable {
        public var services: [String]
        public var properties: [String]
        /// Properties kept even when their service is noise. Vendors hide the water-temperature
        /// alarm inside `temp-custom` / `fish-tank-custom`, and the feeder's daily portion count
        /// inside `feeder-custom`; those named properties stay visible (#7).
        public var keepInNoisyServices: [String]
    }

    /// Option labels that describe something wrong, matched as whole words so `Flow` is not
    /// read as `Low` and `No Faults` is not read as a fault (#7).
    public struct Alerts: Codable, Equatable, Sendable {
        public var words: [String]
    }

    /// The portion action of a fish tank or a pet feeder (#7). `action` names the MIoT action and
    /// `fedToday` names the readings that carry the portions already dispensed today.
    public struct FeedProfile: Codable, Equatable, Sendable {
        public var action: String
        public var fedToday: [String]
    }

    /// Rules for one device family, matched by the record's model (`*.fishbowl.*`) or by the MIoT
    /// device type (`fish-tank`). `highlight` replaces the global high-value patterns for that
    /// family, so a fish tank shows the pump, light, feeding and filter before vendor extras.
    public struct Override: Codable, Equatable, Sendable {
        public var models: [String]?
        public var deviceTypes: [String]?
        public var highlight: [String]?
        public var readouts: [String]?
        public var feeding: FeedProfile?

        public func matches(model: String?, deviceType: String) -> Bool {
            if let deviceTypes, MiotFacetEngine.matches(deviceType, anyOf: deviceTypes) { return true }
            if let models, let model, MiotFacetEngine.matches(model, anyOf: models) { return true }
            return false
        }
    }

    public struct Facets: Codable, Equatable, Sendable {
        /// Upper bound of facets a device marks high value; the rest are secondary.
        public var maxVisible: Int
        /// Device types whose writes stay high risk, mirroring `ModelActionPolicy`'s hazard table.
        public var highRiskTypes: [String]
        /// Services whose writes stay high risk: heating, locks, and network configuration.
        public var highRiskServices: [String]
        public var highValue: [String]
        public var lowRiskWrites: [String]
    }

    /// The confirmation table behind `ManualControlPolicy` (#26). Patterns use the same glob
    /// syntax as `facets`: `strictlyHighRisk` matches device types and services that ask even for
    /// an off direction, `heatingService` names services whose power-on and hotter targets can
    /// hurt, and `lowRiskServices`/`lowRiskTokens` never ask.
    public struct ManualControlRules: Codable, Equatable, Sendable {
        public var strictlyHighRisk: [String]
        public var heatingService: [String]
        public var lowRiskServices: [String]
        public var lowRiskTokens: [String]

        public init(
            strictlyHighRisk: [String],
            heatingService: [String],
            lowRiskServices: [String],
            lowRiskTokens: [String]
        ) {
            self.strictlyHighRisk = strictlyHighRisk
            self.heatingService = heatingService
            self.lowRiskServices = lowRiskServices
            self.lowRiskTokens = lowRiskTokens
        }

        /// Mirrors the bundled document, so a document that predates the field keeps the #143
        /// confirmation behaviour instead of silently letting every write through.
        public static let defaults = ManualControlRules(
            strictlyHighRisk: [
                "lock", "*-lock", "germicidal-lamp", "alertor", "garbage-disposal",
                "router", "gateway", "repeater", "lock-*", "*-valve", "*disposal*",
                "*alarm*", "*steriliz*", "*router*", "*wifi*", "*wlan*", "*gateway*",
                "*repeater*",
            ],
            heatingService: [
                "heater", "ptc", "floor-heat", "heating", "water-heater",
                "electric-water-heater", "gas-water-heater",
            ],
            lowRiskServices: [
                "indicator-light", "night-light", "buzzer", "speaker",
                "physical-controls-locked",
            ],
            lowRiskTokens: ["led", "indicator", "buzzer", "mute", "display", "screen"]
        )
    }

    /// Device type → the services one appliance card merges. `primary` is the one carrying the
    /// main function (heat for a bath heater, exhaust for a hood); `companions` fill the other
    /// controls and readings. Types absent here keep the water-heater card shape.
    public struct ApplianceProfile: Codable, Equatable, Sendable {
        public var primary: [String]
        public var companions: [String]
        public var symbol: String?
        public var roomReading: Bool?
        /// The card can start a heater. Non-heating appliances (a range hood) skip the high-risk
        /// power-on confirmation.
        public var heats: Bool?

        public init(
            primary: [String],
            companions: [String] = [],
            symbol: String? = nil,
            roomReading: Bool? = nil,
            heats: Bool? = nil
        ) {
            self.primary = primary
            self.companions = companions
            self.symbol = symbol
            self.roomReading = roomReading
            self.heats = heats
        }
    }

    public var version: Int
    public var noise: Noise
    public var alerts: Alerts
    public var facets: Facets
    /// The manual-control confirmation table (#26). Optional so documents without it still
    /// decode and fall back to `ManualControlRules.defaults`.
    public var manualControlPolicy: ManualControlRules?
    /// Device family → its own highlight, readout and feeding rules (#7).
    public var overrides: [Override]
    /// Device type → services that may claim it when the type names none of its own services
    /// (#296). Anything not listed stays unsupported instead of being drawn as another device.
    public var fallbackClaims: [String: [String]]
    /// Device type → the services its appliance card aggregates (#4).
    public var appliances: [String: ApplianceProfile]

    public static func decode(_ data: Data) throws -> DeviceProfiles {
        try JSONDecoder().decode(DeviceProfiles.self, from: data)
    }

    public static let current: DeviceProfiles = {
        guard let data = bundledData(), let profiles = try? decode(data) else {
            preconditionFailure("deviceProfiles.json is missing from the MijiaCore bundle")
        }
        return profiles
    }()

    /// Bytes of the bundled document, so a test can prove it matches the Windows file.
    static func bundledData() -> Data? {
        var urls: [URL] = []
        if let url = Bundle.module.url(forResource: "deviceProfiles", withExtension: "json", subdirectory: "Resources") {
            urls.append(url)
        }
        if let url = Bundle.module.url(forResource: "deviceProfiles", withExtension: "json") {
            urls.append(url)
        }
        if let root = Bundle.module.resourceURL {
            urls.append(root.appendingPathComponent("Resources/deviceProfiles.json"))
            urls.append(root.appendingPathComponent("deviceProfiles.json"))
        }
        for base in L10n.translationSearchBases {
            urls.append(base.appendingPathComponent("Resources/deviceProfiles.json"))
            urls.append(base.appendingPathComponent("deviceProfiles.json"))
            for name in L10n.resourceDirectoryNames(in: base) {
                let bundle = base.appendingPathComponent(name)
                urls.append(bundle.appendingPathComponent("Resources/deviceProfiles.json"))
                urls.append(bundle.appendingPathComponent("Contents/Resources/Resources/deviceProfiles.json"))
                urls.append(bundle.appendingPathComponent("Contents/Resources/deviceProfiles.json"))
            }
        }
        for url in urls {
            if let data = try? Data(contentsOf: url), !data.isEmpty { return data }
        }
        return nil
    }
}

public enum MiotFacetKind: String, Codable, Equatable, Sendable {
    case toggle
    case select
    case slider
    case reading
    case button
}

/// Hazard of changing the facet. Raw values are shared with the Windows interpreter.
public enum MiotFacetRisk: String, Codable, Equatable, Sendable {
    case low
    case high
}

public struct MiotFacetOption: Codable, Equatable, Sendable {
    public var value: Int
    public var label: String
}

public struct MiotFacetRange: Codable, Equatable, Sendable {
    public var min: Double
    public var max: Double
    public var step: Double
}

/// Portion handling for feeding inputs (#7). The spec's range is an untrusted cloud number, so
/// every conversion here is bounds-checked: `Int(1e20)` traps and `Array(1...1e20)` allocates
/// forever (§11).
public extension MiotFacetRange {
    /// The most entries a portion picker expands. Feeding specs top out at 30 portions
    /// (`binuo.feeder.y1`); 64 leaves room for a wider vendor while still bounding a typo'd
    /// range. Anything beyond the cap is truncated, never allocated.
    static let maximumPortionOptions = 64

    /// Whole portions inside `min...max`, at most `maxCount` entries, or empty when the range is
    /// unusable: non-finite bounds, a non-positive step, or a max below min.
    func validPortionOptions(maxCount: Int = MiotFacetRange.maximumPortionOptions) -> [Int] {
        guard maxCount > 0, let bounds = integralPortionBounds() else { return [] }
        var options: [Int] = []
        options.reserveCapacity(maxCount)
        for portion in bounds {
            guard options.count < maxCount else { break }
            options.append(portion)
        }
        return options
    }

    /// `requested` clamped into the range, or the first portion when it is nil. Nil when the range
    /// is unusable, so callers keep the requested count instead of inventing a bound.
    func clampedPortion(_ requested: Int?) -> Int? {
        guard let bounds = integralPortionBounds() else { return nil }
        return Swift.min(bounds.upperBound, Swift.max(bounds.lowerBound, requested ?? bounds.lowerBound))
    }

    /// The whole-number bounds of the range, or nil when either bound falls outside `Int`
    /// (`Int(exactly:)` refuses overflow instead of trapping) or the range is empty.
    private func integralPortionBounds() -> ClosedRange<Int>? {
        guard min.isFinite, max.isFinite, step.isFinite, step > 0,
              let lower = Int(exactly: min.rounded(.up)),
              let upper = Int(exactly: max.rounded(.down)),
              lower <= upper else { return nil }
        return lower...upper
    }
}

public struct MiotFacetArgument: Codable, Equatable, Sendable {
    public var propertyId: Int
    public var name: String
    public var format: String
    public var range: MiotFacetRange?
    public var options: [MiotFacetOption]?
}

/// One generic control synthesized from a writable property, a readable property, or an action.
/// `id` is the stable `siid.piid` key for properties and `siid.aAiid` for actions.
public struct MiotFacet: Codable, Equatable, Sendable {
    public var id: String
    public var kind: MiotFacetKind
    public var serviceId: Int
    public var serviceName: String
    public var propertyId: Int?
    public var actionId: Int?
    public var name: String
    public var title: String?
    public var unit: String?
    public var scale: Double?
    public var range: MiotFacetRange?
    public var options: [MiotFacetOption]?
    public var arguments: [MiotFacetArgument]?
    /// True when the facet can change the device: a writable property or an action.
    public var isWritable: Bool
    public var risk: MiotFacetRisk
    /// Writes stay closed unless the data whitelist marks the facet low risk.
    public var writesByDefault: Bool
    /// High-value facets are the few shown first; the rest are secondary.
    public var highValue: Bool
}

public enum MiotFacetEngine {
    /// Generic facets for one MIoT spec, in spec service/property/action order. `model` is the
    /// device record's model (`xiaomi.fishbowl.m300`), used by model-pattern overrides.
    public static func facets(
        for spec: MiotDeviceSpec,
        profiles: DeviceProfiles = .current,
        model: String? = nil
    ) -> [MiotFacet] {
        var facets: [MiotFacet] = []
        let deviceType = spec.shortName
        for service in spec.services {
            let serviceName = service.shortName
            let isNoiseService = matches(serviceName, anyOf: profiles.noise.services)
            for property in service.properties {
                let name = property.shortName
                if isNoiseService {
                    // A noisy vendor service keeps only the named properties the family needs.
                    guard matches(name, anyOf: profiles.noise.keepInNoisyServices),
                          !matches(name, anyOf: profiles.noise.properties) else { continue }
                } else if matches(name, anyOf: profiles.noise.properties) {
                    continue
                }
                if let facet = propertyFacet(
                    property,
                    service: service,
                    serviceName: serviceName,
                    deviceType: deviceType,
                    profiles: profiles
                ) {
                    facets.append(facet)
                }
            }
            guard !isNoiseService else { continue }
            for action in service.actions {
                if let facet = actionFacet(
                    action,
                    service: service,
                    serviceName: serviceName,
                    deviceType: deviceType,
                    profiles: profiles
                ) {
                    facets.append(facet)
                }
            }
        }
        markHighValue(&facets, override: profileOverride(for: spec, model: model, profiles: profiles), profiles: profiles)
        return facets
    }

    /// The family rules that apply to this spec: one entry may name the device type, the model
    /// pattern, or both.
    public static func profileOverride(
        for spec: MiotDeviceSpec,
        model: String? = nil,
        profiles: DeviceProfiles = .current
    ) -> DeviceProfiles.Override? {
        profiles.overrides.first { $0.matches(model: model, deviceType: spec.shortName) }
    }

    private static func propertyFacet(
        _ property: MiotProperty,
        service: MiotService,
        serviceName: String,
        deviceType: String,
        profiles: DeviceProfiles
    ) -> MiotFacet? {
        let name = property.shortName
        guard property.format != "string" else { return nil }
        let id = "\(service.iid).\(property.iid)"
        if property.isWritable {
            let kind: MiotFacetKind
            var range: MiotFacetRange?
            var options: [MiotFacetOption]?
            if property.format == "bool" {
                kind = .toggle
            } else if let list = property.valueList, !list.isEmpty {
                kind = .select
                options = facetOptions(list)
            } else if let parsed = facetRange(property.valueRange) {
                kind = .slider
                range = parsed
            } else {
                return nil
            }
            let risk = risk(for: name, deviceType: deviceType, serviceName: serviceName, profiles: profiles)
            return MiotFacet(
                id: id,
                kind: kind,
                serviceId: service.iid,
                serviceName: serviceName,
                propertyId: property.iid,
                actionId: nil,
                name: name,
                title: nonEmpty(property.description),
                unit: normalizedUnit(property.unit),
                scale: nil,
                range: range,
                options: options,
                arguments: nil,
                isWritable: true,
                risk: risk,
                writesByDefault: risk == .low,
                highValue: false
            )
        }
        guard property.isReadable else { return nil }
        return MiotFacet(
            id: id,
            kind: .reading,
            serviceId: service.iid,
            serviceName: serviceName,
            propertyId: property.iid,
            actionId: nil,
            name: name,
            title: nonEmpty(property.description),
            unit: normalizedUnit(property.unit),
            scale: explicitScale(property),
            range: nil,
            options: facetOptions(property.valueList ?? []),
            arguments: nil,
            isWritable: false,
            risk: .low,
            writesByDefault: false,
            highValue: false
        )
    }

    private static func actionFacet(
        _ action: MiotAction,
        service: MiotService,
        serviceName: String,
        deviceType: String,
        profiles: DeviceProfiles
    ) -> MiotFacet? {
        var arguments: [MiotFacetArgument] = []
        for inputId in action.inputProperties {
            guard let input = service.properties.first(where: { $0.iid == inputId }),
                  !matches(input.shortName, anyOf: profiles.noise.properties) else { return nil }
            let options = facetOptions(input.valueList ?? [])
            let range = facetRange(input.valueRange)
            guard options != nil || range != nil else { return nil }
            arguments.append(MiotFacetArgument(
                propertyId: input.iid,
                name: input.shortName,
                format: input.format,
                range: range,
                options: options
            ))
        }
        let name = action.shortName
        let risk = risk(for: name, deviceType: deviceType, serviceName: serviceName, profiles: profiles)
        return MiotFacet(
            id: "\(service.iid).a\(action.iid)",
            kind: .button,
            serviceId: service.iid,
            serviceName: serviceName,
            propertyId: nil,
            actionId: action.iid,
            name: name,
            title: nonEmpty(action.description),
            unit: nil,
            scale: nil,
            range: nil,
            options: nil,
            arguments: arguments.isEmpty ? nil : arguments,
            isWritable: true,
            risk: risk,
            writesByDefault: risk == .low,
            highValue: false
        )
    }

    /// The global list marks every matching facet up to `maxVisible` (an AC may show several
    /// modes); a family override is a priority list instead, so it marks one facet per entry and
    /// a second filter service cannot spend two of the eight slots (#7).
    private static func markHighValue(
        _ facets: inout [MiotFacet],
        override: DeviceProfiles.Override?,
        profiles: DeviceProfiles
    ) {
        var remaining = max(0, profiles.facets.maxVisible)
        guard let patterns = override?.highlight else {
            for index in facets.indices where matches(facets[index].name, anyOf: profiles.facets.highValue) {
                guard remaining > 0 else { break }
                facets[index].highValue = true
                remaining -= 1
            }
            return
        }
        for pattern in patterns {
            guard remaining > 0 else { break }
            guard let index = facets.firstIndex(where: {
                !$0.highValue && highlightMatches($0, pattern: pattern)
            }) else { continue }
            facets[index].highValue = true
            remaining -= 1
        }
    }

    /// A highlight entry is either `service.name` (`light.on`, so a companion light does not
    /// stand in for the family's own switch) or a bare name pattern (`*filter*`).
    private static func highlightMatches(_ facet: MiotFacet, pattern: String) -> Bool {
        if pattern.contains(".") {
            return matches("\(facet.serviceName).\(facet.name)", pattern: pattern)
        }
        return matches(facet.name, pattern: pattern)
    }

    private static func risk(
        for name: String,
        deviceType: String,
        serviceName: String,
        profiles: DeviceProfiles
    ) -> MiotFacetRisk {
        if matches(deviceType, anyOf: profiles.facets.highRiskTypes) { return .high }
        if matches(serviceName, anyOf: profiles.facets.highRiskServices) { return .high }
        return matches(name, anyOf: profiles.facets.lowRiskWrites) ? .low : .high
    }

    private static func facetOptions(_ list: [MiotValueListItem]) -> [MiotFacetOption]? {
        guard !list.isEmpty else { return nil }
        return list.map { MiotFacetOption(value: $0.value, label: $0.description) }
    }

    private static func facetRange(_ raw: [Double]?) -> MiotFacetRange? {
        guard let raw, raw.count >= 2 else { return nil }
        let step = raw.count >= 3 && raw[2] > 0 ? raw[2] : 1
        return MiotFacetRange(min: raw[0], max: raw[1], step: step)
    }

    private static func normalizedUnit(_ raw: String?) -> String? {
        guard let unit = nonEmpty(raw), unit.lowercased() != "none" else { return nil }
        return unit
    }

    private static func nonEmpty(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        return trimmed
    }

    /// `0.01平方米` or `单位0.1升` in a spec comment is an explicit display scale, so the
    /// reading is converted before it is shown. Ambiguous magnitudes never guess a factor.
    private static func explicitScale(_ property: MiotProperty) -> Double? {
        for raw in [property.unit, property.comment] {
            guard let raw else { continue }
            let text = raw
                .lowercased()
                .replacingOccurrences(of: " ", with: "")
                .replacingOccurrences(of: "：", with: ":")
            if let value = scaleNumber(atStartOf: text) { return value }
            if let marker = text.range(of: "单位") {
                let rest = text[marker.upperBound...]
                let afterColon = rest.hasPrefix(":") ? rest.dropFirst() : rest
                if let value = scaleNumber(atStartOf: String(afterColon)) { return value }
            }
        }
        return nil
    }

    private static func scaleNumber(atStartOf text: String) -> Double? {
        guard text.hasPrefix("0.") else { return nil }
        let digits = text.dropFirst(2).prefix { $0.isNumber }
        guard !digits.isEmpty, let value = Double("0." + String(digits)), value > 0 else { return nil }
        return value
    }

    /// Drawer rows for 更多设置 (#5). A property the primary card already reads is left out, and so
    /// is any action of the card's own service: both are already reachable from the main controls.
    /// An action with inputs has no generic editor here unless the family marks it as feeding, in
    /// which case the drawer picks the portion and the policy confirms it (#7).
    public static func controls(
        for spec: MiotDeviceSpec,
        did: String,
        values: [MiotPropKey: JSONValue],
        primaryServiceId: Int?,
        excluding touched: Set<MiotPropKey> = [],
        model: String? = nil,
        profiles: DeviceProfiles = .current
    ) -> [FacetControl] {
        let override = profileOverride(for: spec, model: model, profiles: profiles)
        let feedingAction = override?.feeding?.action
        // A spec can publish the same action twice (m300 has it under `fish-tank` and `feeder`).
        // The first match carries the feeding editor; the duplicate keeps its card behaviour.
        var feedingAssigned = false
        var controls: [FacetControl] = facets(for: spec, profiles: profiles, model: model).compactMap { facet in
            if let propertyId = facet.propertyId,
               touched.contains(MiotPropKey(did: did, siid: facet.serviceId, piid: propertyId)) {
                return nil
            }
            if facet.actionId != nil, facet.serviceId == primaryServiceId { return nil }
            let isFeeding = !feedingAssigned && feedingAction != nil
                && facet.actionId != nil
                && facet.name == feedingAction
            if facet.actionId != nil {
                // A parameterised action needs the feeding editor; anything else with inputs has
                // no generic editor here (#7, #303).
                let isPlainAction = (facet.arguments ?? []).isEmpty
                if !isFeeding && !isPlainAction { return nil }
                if isFeeding { feedingAssigned = true }
            }
            let value = facet.propertyId.flatMap { piid in
                values[MiotPropKey(did: did, siid: facet.serviceId, piid: piid)]
            }
            var target: FacetTarget?
            if facet.isWritable {
                if let piid = facet.propertyId {
                    target = .property(entityId: MiotAddress.make(did: did, siid: facet.serviceId), piid: piid)
                } else if let aiid = facet.actionId {
                    target = .action(entityId: MiotAddress.makeAction(did: did, siid: facet.serviceId, aiid: aiid))
                }
            }
            var control = FacetControl(
                id: facet.id,
                kind: facet.kind,
                name: facet.name,
                title: facet.title,
                serviceName: facet.serviceName,
                unit: facet.unit,
                scale: facet.scale,
                value: value,
                options: facet.options ?? [],
                range: facet.range,
                isWritable: facet.isWritable,
                writesByDefault: facet.writesByDefault && !(facet.name == "mode" && [
                    "curtain", "window-opener", "airer", "clothes-airer", "garage-door"
                ].contains(facet.serviceName)),
                isHighValue: facet.highValue,
                target: target
            )
            if isFeeding {
                control.arguments = facet.arguments ?? []
                control.portionArgumentIndex = portionArgumentIndex(in: control.arguments)
                // Feeding is a physical action the drawer offers without the advanced-writes
                // opt-in: the second confirmation is the guard, not a hidden setting (#7).
                control.isConfirmedWrite = true
            }
            return control
        }
        attachFedToday(&controls, feeding: override?.feeding)
        return controls
    }

    /// The argument the drawer asks for when dispensing food: the first one with a range.
    static func portionArgumentIndex(in arguments: [MiotFacetArgument]) -> Int? {
        arguments.firstIndex { $0.range != nil }
    }

    /// The portions already dispensed today, formatted for the drawer row and the confirmation.
    private static func attachFedToday(
        _ controls: inout [FacetControl],
        feeding: DeviceProfiles.FeedProfile?
    ) {
        guard let feeding else { return }
        guard let reading = controls.first(where: {
            $0.kind == .reading && $0.value != nil && matches($0.name, anyOf: feeding.fedToday)
        }) else { return }
        let text = MiotFacetText.value(reading)
        guard text != "—" else { return }
        for index in controls.indices where controls[index].portionArgumentIndex != nil {
            controls[index].fedToday = text
        }
    }

    /// The value an action input falls back to when it is not the portion the user picks. An enum
    /// input that names an app/remote source is chosen so a remote dispense is not recorded as a
    /// key press; otherwise the first option stands.
    static func defaultActionArgument(_ argument: MiotFacetArgument) -> JSONValue {
        if let options = argument.options, !options.isEmpty {
            let preferred = ["remote", "app", "net", "cloud", "远程", "应用", "云端"]
            let chosen = options.first { option in
                preferred.contains { option.label.lowercased().contains($0) }
            } ?? options[0]
            return .number(Double(chosen.value))
        }
        return .number(argument.range?.min ?? 0)
    }

    /// The alert line for one reading: the spec's own label, translated when the vendor word names
    /// a common condition. A pump's `Low` is a water level, a feeder's `Low` is a food level, so
    /// the property name disambiguates (#7).
    public static func alertText(title: String, propertyName: String, label: String) -> String {
        "\(title)：\(alertCondition(propertyName: propertyName, label: label))"
    }

    static func alertCondition(propertyName: String, label: String) -> String {
        guard !L10n.isEnglish else { return label }
        let key = label.lowercased()
        if key.contains("upper limit") { return "高于上限" }
        if key.contains("lower limit") { return "低于下限" }
        if propertyName.contains("pump") {
            switch key {
            case "not inserted": return "未插入"
            case "low", "low1": return "低水位"
            case "blocked", "blockage", "bockage": return "堵塞"
            case "fault": return "故障"
            default: break
            }
        }
        if propertyName.contains("food") || propertyName.contains("feed") || propertyName.contains("left-level") {
            switch key {
            case "empty": return "已空"
            case "low": return "余粮不足"
            case "stuck food": return "卡粮"
            case "timeout": return "超时"
            default: break
            }
        }
        switch key {
        case "fault": return "故障"
        case "error": return "错误"
        case "exception": return "异常"
        case "alarm": return "告警"
        case "change": return "需更换"
        case "timeout": return "超时"
        case "empty": return "已空"
        case "offline": return "离线"
        case "not inserted": return "未插入"
        case "blocked", "blockage", "bockage": return "堵塞"
        default: return label
        }
    }

    /// True when an option label describes something wrong ("Low", "Blocked", "Fault", "Not
    /// Inserted"). Whole-word Latin matching keeps `Flow` and `No Faults` out (#7).
    public static func isAlertLabel(_ label: String, profiles: DeviceProfiles = .current) -> Bool {
        let text = label.lowercased()
        return profiles.alerts.words.contains { word in
            let needle = word.lowercased()
            if needle.unicodeScalars.contains(where: { !$0.isASCII }) {
                return text.contains(needle)
            }
            return containsWord(text, needle)
        }
    }

    private static func containsWord(_ text: String, _ needle: String) -> Bool {
        var searchStart = text.startIndex
        while let range = text.range(of: needle, range: searchStart..<text.endIndex) {
            let beforeIsBoundary = range.lowerBound == text.startIndex
                || !isWordCharacter(text[text.index(before: range.lowerBound)])
            let afterIsBoundary = range.upperBound == text.endIndex
                || !isWordCharacter(text[range.upperBound])
            if beforeIsBoundary && afterIsBoundary { return true }
            searchStart = range.upperBound
        }
        return false
    }

    private static func isWordCharacter(_ character: Character) -> Bool {
        character.isLetter || character.isNumber
    }

    /// Matches one short name against a noise or display pattern. Only `*` is special.
    static func matches(_ value: String, anyOf patterns: [String]) -> Bool {
        patterns.contains { matches(value, pattern: $0) }
    }

    static func matches(_ value: String, pattern: String) -> Bool {
        let pattern = Array(pattern)
        let value = Array(value)
        var patternIndex = 0
        var valueIndex = 0
        var starIndex = -1
        var starValueIndex = 0
        while valueIndex < value.count {
            if patternIndex < pattern.count, pattern[patternIndex] == value[valueIndex] {
                patternIndex += 1
                valueIndex += 1
            } else if patternIndex < pattern.count, pattern[patternIndex] == "*" {
                starIndex = patternIndex
                starValueIndex = valueIndex
                patternIndex += 1
            } else if starIndex >= 0 {
                patternIndex = starIndex + 1
                starValueIndex += 1
                valueIndex = starValueIndex
            } else {
                return false
            }
        }
        while patternIndex < pattern.count, pattern[patternIndex] == "*" {
            patternIndex += 1
        }
        return patternIndex == pattern.count
    }
}
