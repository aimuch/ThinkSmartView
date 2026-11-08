import Foundation

/// Domain-level auxiliary controls for Home Assistant (#307). Every active entity the primary
/// card did not claim becomes a toggle, select, slider, button or reading under 更多设置.
///
/// Writes address the entity in its own domain, so they never take the MIoT path. The default
/// write whitelist reuses `deviceProfiles.json`: a high-risk device type or a name outside the
/// low-risk set stays closed until the user allows advanced facet writes.
public enum HAFacetEngine {
    /// `switch.*`, `select.*`, `number.*`, `button.*`, `sensor.*` and `binary_sensor.*` entities
    /// that survived `claimed` and the registry's disabled/hidden filters. `text.*` and every
    /// domain handled by a primary card stay out.
    public static func controls(
        entities: [HAEntityEntry],
        states: [String: HAEntityState],
        excluding claimed: Set<String>,
        device: SmartDevice
    ) -> [FacetControl] {
        var controls: [FacetControl] = []
        let profiles = DeviceProfiles.current
        for entity in entities {
            let entityId = entity.entity_id
            guard !claimed.contains(entityId),
                  entity.disabled_by == nil,
                  entity.hidden_by == nil,
                  let state = states[entityId],
                  let control = control(entity: entity, state: state, device: device, profiles: profiles) else {
                continue
            }
            controls.append(control)
        }
        markHighValue(&controls, profiles: profiles)
        return controls
    }

    private static func control(
        entity: HAEntityEntry,
        state: HAEntityState,
        device: SmartDevice,
        profiles: DeviceProfiles
    ) -> FacetControl? {
        guard let dot = entity.entity_id.firstIndex(of: ".") else { return nil }
        let domain = String(entity.entity_id[..<dot])
        let objectId = String(entity.entity_id[entity.entity_id.index(after: dot)...])
        // A translation key is the integration's stable semantic name; the object id is the
        // fallback for YAML entities and older integrations.
        let semantic = entity.translation_key?.trimmingCharacters(in: .whitespacesAndNewlines)
        let rawName = (semantic?.isEmpty == false ? semantic! : objectId)
        let name = rawName.replacingOccurrences(of: "_", with: "-")
        let objectName = objectId.replacingOccurrences(of: "_", with: "-")
        let category = entity.entity_category?.lowercased()
        // Config entities are device plumbing (reboot, calibration), never drawer controls.
        guard category != "config" else { return nil }
        guard !MiotFacetEngine.matches(name, anyOf: profiles.noise.properties),
              !MiotFacetEngine.matches(objectName, anyOf: profiles.noise.properties) else { return nil }

        let stateLower = state.state.lowercased()
        let writable = ["switch", "select", "number", "button"].contains(domain)
        guard writable || domain == "sensor" || domain == "binary_sensor" else { return nil }
        // A diagnostic switch or button is plumbing too; diagnostic readings such as battery or
        // signal strength remain useful.
        if writable, category == "diagnostic" { return nil }
        if domain == "button" {
            // A stateless button is `unknown` before its first press; that one stays usable.
            guard stateLower != "unavailable" else { return nil }
            let deviceClass = state.attributes["device_class"]?.stringValue?.lowercased() ?? ""
            guard !["restart", "update", "identify"].contains(deviceClass) else { return nil }
        } else if !state.isAvailable {
            return nil
        }

        let highRiskDevice = isHighRiskDevice(device)
        let highRiskType = MiotFacetEngine.matches(device.model, anyOf: profiles.facets.highRiskTypes)
        let writesByDefault = writable
            && !highRiskDevice
            && !highRiskType
            && isLowRiskName(name, profiles: profiles.facets)

        let rawFriendly = state.attributes["friendly_name"]?.stringValue
        let friendly = rawFriendly?.trimmingCharacters(in: .whitespacesAndNewlines)
        let title = friendly.flatMap { $0.isEmpty ? nil : $0 }

        func make(
            kind: MiotFacetKind,
            value: JSONValue?,
            options: [MiotFacetOption] = [],
            range: MiotFacetRange? = nil,
            unit: String? = nil,
            target: FacetTarget? = nil
        ) -> FacetControl {
            FacetControl(
                id: entity.entity_id,
                kind: kind,
                name: name,
                title: title,
                serviceName: domain,
                unit: unit,
                value: value,
                options: options,
                range: range,
                isWritable: writable,
                writesByDefault: writesByDefault,
                isHighValue: false,
                target: target
            )
        }

        switch domain {
        case "switch":
            let isOn: Bool
            switch stateLower {
            case "on": isOn = true
            case "off": isOn = false
            default: return nil
            }
            return make(kind: .toggle, value: .bool(isOn), target: .haProperty(entityId: entity.entity_id))

        case "select":
            let options = state.attributes["options"]?.stringArrayValue ?? []
            guard !options.isEmpty else { return nil }
            let selected = options.firstIndex(of: state.state) ?? 0
            return make(
                kind: .select,
                value: .number(Double(selected)),
                options: options.enumerated().map { MiotFacetOption(value: $0.offset, label: $0.element) },
                target: .haProperty(entityId: entity.entity_id)
            )

        case "number":
            let min = state.attributes["min"]?.doubleValue ?? 0
            let max = state.attributes["max"]?.doubleValue ?? 100
            let step = state.attributes["step"]?.doubleValue ?? 1
            guard max > min else { return nil }
            let value = Double(state.state) ?? min
            return make(
                kind: .slider,
                value: .number(value),
                range: MiotFacetRange(min: min, max: max, step: step > 0 ? step : 1),
                target: .haProperty(entityId: entity.entity_id)
            )

        case "button":
            return make(kind: .button, value: nil, target: .haAction(entityId: entity.entity_id))

        case "sensor":
            let value: JSONValue
            if let number = Double(state.state), number.isFinite {
                value = .number(number)
            } else {
                value = .string(state.state)
            }
            return make(
                kind: .reading,
                value: value,
                unit: state.attributes["unit_of_measurement"]?.stringValue
            )

        case "binary_sensor":
            let value: JSONValue = switch stateLower {
            case "on": .bool(true)
            case "off": .bool(false)
            default: .string(state.state)
            }
            return make(kind: .reading, value: value)

        default:
            return nil
        }
    }

    /// The same device-level hazards the manual confirmation table uses. A plug, valve, heater,
    /// heating appliance or non-curtain cover may move a load or open a hazard, so its auxiliary
    /// writes stay closed by default.
    private static func isHighRiskDevice(_ device: SmartDevice) -> Bool {
        switch IntentDeviceCategory(device) {
        case .heater, .plug, .valve:
            return true
        case .appliance:
            guard case .appliance(let spec) = device.kind else { return false }
            return spec.heats
        case .cover:
            guard case .cover(let spec) = device.kind else { return false }
            return spec.subtype != .curtain
        case .climate:
            return device.name.contains("热水器")
        default:
            return false
        }
    }

    /// MIoT's low-risk names (`brightness`, `mode`, `fan-level`) plus the Home Assistant switch
    /// tokens that never move the appliance's main function. A full object id matches a pattern as
    /// a suffix (`living-fan-mode` matches `*-mode`).
    private static func isLowRiskName(_ name: String, profiles: DeviceProfiles.Facets) -> Bool {
        if profiles.lowRiskWrites.contains(where: { pattern in
            MiotFacetEngine.matches(name, pattern: pattern)
                || MiotFacetEngine.matches(name, pattern: "*-\(pattern)")
        }) {
            return true
        }
        let tokens = Set(name.split(separator: "-").map(String.init))
        if !tokens.isDisjoint(with: ["led", "indicator", "buzzer", "mute", "display", "screen"]) {
            return true
        }
        return ["night-light", "child-lock", "physical-controls-locked", "auto-off"]
            .contains { name.contains($0) }
    }

    /// The same high-value list and cap the MIoT drawer uses, matched against the normalized
    /// entity object id (`target_humidity` → `target-humidity`).
    private static func markHighValue(_ controls: inout [FacetControl], profiles: DeviceProfiles) {
        var remaining = max(0, profiles.facets.maxVisible)
        for index in controls.indices where remaining > 0 {
            guard controls[index].isWritable,
                  MiotFacetEngine.matches(controls[index].name, anyOf: profiles.facets.highValue) else { continue }
            controls[index].isHighValue = true
            remaining -= 1
        }
    }
}
