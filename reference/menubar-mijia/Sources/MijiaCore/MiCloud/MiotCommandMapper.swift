import Foundation

public enum MiotWrite: Equatable, Sendable {
    case setProps([MiotPropWrite])
    case action(MiotActionCall)

    public var did: String {
        switch self {
        case .setProps(let writes): return writes.first?.did ?? ""
        case .action(let call): return call.did
        }
    }
}

public enum MiotCommandError: LocalizedError, Equatable, Sendable {
    case invalidAddress(String)
    case unsupported(String)

    public var errorDescription: String? {
        switch self {
        case .invalidAddress(let id): return "无法识别的米家设备地址：\(id)"
        case .unsupported(let what): return "该设备暂不支持此操作：\(what)"
        }
    }
}

/// Pure translation of the backend-neutral `DeviceCommand` into MIoT prop/set or action calls,
/// resolved by property/action short names inside the addressed service.
public enum MiotCommandMapper {
    public static func map(_ command: DeviceCommand, spec: MiotDeviceSpec) throws -> MiotWrite {
        // A mains breaker is read-only whatever the spec allows: one write can black out the home,
        // so no path (card, assistant, alert action, URL, scene) may reach it (#265).
        if MiotSynthesizer.isMainsBreaker(spec) {
            throw MiotCommandError.unsupported("electronic-valve")
        }
        if case .setMiotProperty(let entityId, let piid, let value) = command {
            guard let address = MiotAddress.parse(entityId) else {
                throw MiotCommandError.invalidAddress(entityId)
            }
            guard let service = spec.service(iid: address.siid),
                  let property = service.properties.first(where: { $0.iid == piid }),
                  property.isWritable else {
                throw MiotCommandError.unsupported("property \(piid)")
            }
            let ctx = Context(did: address.did, spec: spec, service: service)
            return .setProps([MiotPropWrite(
                did: address.did,
                siid: address.siid,
                piid: piid,
                value: ctx.coerce(value, for: property)
            )])
        }
        if case .callMiotAction(let entityId, let arguments) = command {
            guard let address = MiotAddress.parseAction(entityId),
                  let service = spec.service(iid: address.siid),
                  let action = service.actions.first(where: { $0.iid == address.aiid }),
                  action.inputProperties.count == arguments.count else {
                throw MiotCommandError.unsupported("action")
            }
            let ctx = Context(did: address.did, spec: spec, service: service)
            var inputs: [JSONValue] = []
            for (index, piid) in action.inputProperties.enumerated() {
                guard let property = service.properties.first(where: { $0.iid == piid }) else {
                    throw MiotCommandError.unsupported("action")
                }
                // An enum input only accepts one of its published options (#7).
                if let list = property.valueList, !list.isEmpty {
                    guard let raw = arguments[index].intValue,
                          list.contains(where: { $0.value == raw }) else {
                        throw MiotCommandError.unsupported("action")
                    }
                }
                inputs.append(ctx.coerce(arguments[index], for: property))
            }
            return .action(MiotActionCall(
                did: address.did,
                siid: address.siid,
                aiid: action.iid,
                inputs: inputs
            ))
        }
        let entityId = command.targetEntityId
        // The services the shared appliance card reads. A control it exposes may live on a
        // companion service while the card addresses the primary one (#300, laundry in #3).
        let applianceServices = MiotSynthesizer.applianceServiceNames
        if let address = MiotAddress.parseAction(entityId) {
            guard let service = spec.service(iid: address.siid),
                  let action = service.actions.first(where: { $0.iid == address.aiid }) else {
                throw MiotCommandError.unsupported("action")
            }
            // A no-argument start or pause is accepted only on a service the appliance card builds
            // from, keyed by its own short name (#300 review, laundry added in #3).
            let isApplianceAction = applianceServices.contains(service.shortName)
                && ApplianceActionKind.matching(action.shortName) != nil
            let inputs: [JSONValue]
            switch command {
            case .pressButton where action.inputProperties.isEmpty
                && (["play-music", "stop-alarm"].contains(action.shortName) || isApplianceAction):
                inputs = []
            case .sendNotifyMessage(_, let text):
                let expected = action.shortName == "play-text" ? ["text-content"]
                    : action.shortName == "execute-text-directive" ? ["text-content", "silent-execution"] : []
                guard !expected.isEmpty, action.inputProperties.count == expected.count,
                      zip(action.inputProperties, expected).allSatisfy({ id, name in
                          service.properties.contains { $0.iid == id && $0.shortName == name }
                      }) else { throw MiotCommandError.unsupported("speaker action inputs") }
                inputs = expected.count == 1 ? [.string(text)] : [.string(text), .number(0)]
            default:
                throw MiotCommandError.unsupported("speaker action")
            }
            return .action(MiotActionCall(did: address.did, siid: address.siid, aiid: address.aiid, inputs: inputs))
        }
        guard let (did, siid) = MiotAddress.parse(entityId) else { throw MiotCommandError.invalidAddress(entityId) }
        guard let service = spec.service(iid: siid) else { throw MiotCommandError.unsupported("siid \(siid)") }
        let ctx = Context(did: did, spec: spec, service: service)

        switch command {
        case .setLightPower(_, let isOn), .setClimatePower(_, let isOn, _), .setFanPower(_, let isOn):
            return .setProps([try ctx.write("on", .bool(isOn))])

        case .setLightBrightness(_, let percent):
            if percent <= 0 { return .setProps([try ctx.write("on", .bool(false))]) }
            let bri = try ctx.require("brightness")
            let raw = MiotPercentScale.raw(fromPercent: Double(percent), property: bri.property, curve: .proportional)
            // HA's light.turn_on(brightness) also powers the light on; MIoT needs an explicit `on`.
            return .setProps(ctx.powerOnPrefix() + [ctx.write(bri, .number(raw))])

        case .setLightColorTemp(_, let kelvin):
            return .setProps(ctx.powerOnPrefix() + [try ctx.write("color-temperature", .number(Double(kelvin)))])

        case .setLightEffect(_, let effect):
            return .setProps(ctx.powerOnPrefix() + [try ctx.writeListValue("mode", description: effect)])

        case .setClimateMode(_, let hvacMode):
            if hvacMode == "off" { return .setProps([try ctx.write("on", .bool(false))]) }
            var writes = [try ctx.write("on", .bool(true))]
            if let mode = ctx.listProperty("mode"),
               let item = mode.property.valueList?.first(where: { MiotSynthesizer.hvacMode(forDescription: $0.description) == hvacMode }) {
                writes.append(ctx.write(mode, .number(Double(item.value))))
            }
            return .setProps(writes)

        case .setClimateTemperature(_, let temperature):
            if let target = ctx.property("target-temperature")
                ?? ctx.writableProperty("target-temperature", in: applianceServices) {
                return .setProps([ctx.write(target, .number(temperature))])
            }
            throw MiotCommandError.unsupported("target-temperature")

        case .setClimateFanMode(_, let fanMode):
            return .setProps([try ctx.writeListValue("fan-level", description: fanMode, searchDevice: true)])

        case .setFanPercentage(_, let percent):
            if percent <= 0 { return .setProps([try ctx.write("on", .bool(false))]) }
            let speed = try ctx.requireFanSpeedProperty()
            var prefix = ctx.powerOnPrefix()
            if speed.service.iid != service.iid,
               let favMode = try? ctx.writeListValue("mode", description: "Favorite") {
                prefix.append(favMode)
            }
            return .setProps(prefix + [ctx.writePercentage(speed, percent: percent)])

        case .setFanPresetMode(_, let preset):
            if let w = try? ctx.writeListValue("mode", description: preset) { return .setProps([w]) }
            return .setProps([try ctx.writeListValue("fan-level", description: preset, searchDevice: true)])

        case .setFanOscillating(_, let oscillating):
            return .setProps([try ctx.write("horizontal-swing", .bool(oscillating))])

        case .setSwitch(_, let isOn):
            // A gas water heater publishes its power switch on the companion `boiler` service
            // while the card addresses the `water-heater` service (#300); a cleanfly dryer
            // likewise (#3). A bath heater publishes its heat as `heating` instead of `on` (#4).
            if let power = ctx.property("on")
                ?? ctx.writableProperty("on", in: applianceServices) {
                return .setProps([ctx.write(power, .bool(isOn))])
            }
            if let heating = ctx.property("heating") {
                return .setProps([ctx.write(heating, .bool(isOn))])
            }
            let name: String
            if service.shortName == "speaker", ctx.property("mute") != nil {
                name = "mute"
            } else if service.shortName == "intelligent-speaker", ctx.property("sleep-mode") != nil {
                name = "sleep-mode"
            } else if ctx.property("motion-detection") != nil {
                name = "motion-detection"
            } else if ctx.property("physical-controls-locked") != nil {
                name = "physical-controls-locked"
            } else {
                let candidates = isOn ? ["start-cook", "resume-cook"] : ["cancel-cooking", "pause"]
                if let call = try? ctx.action(candidates, requireEmptyInputs: true) {
                    return .action(call)
                }
                name = "on"
            }
            return .setProps([try ctx.write(name, .bool(isOn))])

        case .setSelectOption(_, let option):
            if ctx.property("motor-control") != nil {
                return .setProps([try ctx.writeListValue("motor-control", description: option)])
            }
            if let w = try? ctx.writeListValue("mode", description: option) {
                return .setProps([w])
            }
            if let match = ctx.listValue("mode", description: option, in: applianceServices) {
                return .setProps([ctx.write(match.found, .number(Double(match.item.value)))])
            }
            return .setProps([try ctx.writeListValue("night-shot", description: option)])

        case .vacuumAction(_, let action):
            let candidates: [String] = switch action {
            case .start: ["start-sweep", "start-clean", "start-sweep-mop", "start-mop"]
            case .pause: ["pause-sweeping", "pause", "stop-sweeping", "stop-clean"]
            case .returnToBase: ["start-charge", "stop-and-gocharge", "return-to-charge", "go-charging"]
            }
            return .action(try ctx.action(candidates))

        case .mediaSetVolume(_, let level):
            guard service.shortName == "speaker", level.isFinite else { throw MiotCommandError.unsupported("volume") }
            let volume = try ctx.require("volume")
            let raw = MiotPercentScale.raw(fromPercent: level * 100, property: volume.property, curve: .proportional)
            return .setProps([ctx.write(volume, .number(raw))])

        case .setNumberValue(_, let value):
            if let target = ctx.property("target-position") {
                // The card speaks 0…100; flip for an airer whose raw position is distance lowered (#299).
                let subtype = CoverSubtype(miotServiceShortName: ctx.service.shortName)
                let percent = subtype.invertsMiotPosition ? 100 - value : value
                let raw = MiotPercentScale.raw(fromPercent: percent, property: target.property, curve: .proportional)
                return .setProps([ctx.write(target, .number(raw))])
            }
            return .setProps([try ctx.write("target-temperature", .number(value))])

        case .setClimateSwingMode, .mediaSelectSource,
             .pressButton, .sendNotifyMessage, .activateScene,
             .setMiotProperty, .callMiotAction, .setHumidity:
            throw MiotCommandError.unsupported(String(describing: command).components(separatedBy: "(").first ?? "")
        }
    }

    private struct Context {
        let did: String
        let spec: MiotDeviceSpec
        let service: MiotService

        struct Found {
            let service: MiotService
            let property: MiotProperty
        }

        func property(_ name: String, searchDevice: Bool = false) -> Found? {
            if let p = service.properties.first(where: { $0.shortName == name && $0.isWritable }) {
                return Found(service: service, property: p)
            }
            guard searchDevice else { return nil }
            // e.g. an AC whose fan-level lives in a separate fan-control service.
            let others = spec.services(named: "fan-control") + spec.services.filter { $0.iid != service.iid }
            for s in others {
                if let p = s.properties.first(where: { $0.shortName == name && $0.isWritable }) {
                    return Found(service: s, property: p)
                }
            }
            return nil
        }

        /// First writable property with a non-empty value list, the same property the synthesizer
        /// binds. A writable `mode` without a list must not shadow the list that follows it.
        func listProperty(_ name: String) -> Found? {
            for p in service.properties
            where p.shortName == name && p.isWritable && p.valueList?.isEmpty == false {
                return Found(service: service, property: p)
            }
            return nil
        }

        func writableProperty(_ name: String, in serviceNames: [String]) -> Found? {
            for serviceName in serviceNames {
                for candidate in spec.services(named: serviceName) {
                    if let p = candidate.properties.first(where: { $0.shortName == name && $0.isWritable }) {
                        return Found(service: candidate, property: p)
                    }
                }
            }
            return nil
        }

        func listValue(
            _ name: String,
            description: String,
            in serviceNames: [String]
        ) -> (found: Found, item: MiotValueListItem)? {
            for serviceName in serviceNames {
                for candidate in spec.services(named: serviceName) {
                    for property in candidate.properties
                    where property.shortName == name && property.isWritable {
                        if let item = property.valueList?.first(where: {
                            $0.description.caseInsensitiveCompare(description) == .orderedSame
                        }) {
                            return (Found(service: candidate, property: property), item)
                        }
                    }
                }
            }
            return nil
        }

        func require(_ name: String, searchDevice: Bool = false) throws -> Found {
            guard let f = property(name, searchDevice: searchDevice) else { throw MiotCommandError.unsupported(name) }
            return f
        }

        func write(_ name: String, _ value: JSONValue) throws -> MiotPropWrite {
            write(try require(name), value)
        }

        func write(_ found: Found, _ value: JSONValue) -> MiotPropWrite {
            MiotPropWrite(did: did, siid: found.service.iid, piid: found.property.iid, value: coerce(value, for: found.property))
        }

        func powerOnPrefix() -> [MiotPropWrite] {
            property("on").map { [write($0, .bool(true))] } ?? []
        }

        func writeListValue(_ name: String, description: String, searchDevice: Bool = false) throws -> MiotPropWrite {
            let extraServices = spec.services(named: "fan-control")
                + spec.services(named: "air-purifier-favorite")
                + spec.services(named: "air-fresh-favorite")
                + spec.services.filter { $0.iid != service.iid }
            let services = [service] + (searchDevice ? extraServices : [])
            for s in services {
                for p in s.properties where p.shortName == name && p.isWritable {
                    if let item = p.valueList?.first(where: { $0.description.caseInsensitiveCompare(description) == .orderedSame }) {
                        return write(Found(service: s, property: p), .number(Double(item.value)))
                    }
                }
            }
            throw MiotCommandError.unsupported("\(name)=\(description)")
        }

        func requireFanSpeedProperty() throws -> Found {
            if let p = service.properties.first(where: { $0.shortName == "fan-level" && $0.isWritable && $0.valueRange != nil }) {
                return Found(service: service, property: p)
            }
            let favServices = spec.services(named: "air-purifier-favorite")
                + spec.services(named: "air-fresh-favorite")
                + spec.services(named: "motor-speed")
                + spec.services(named: "custom-service")
                + spec.services(named: "fan-control")
            let candidates = ["fan-level", "favorite-fan-level", "favorite-level", "favorite-speed"]
            for name in candidates {
                for s in favServices {
                    if let p = s.properties.first(where: { $0.shortName == name && $0.isWritable }) {
                        return Found(service: s, property: p)
                    }
                }
            }
            if let p = service.properties.first(where: { $0.shortName == "fan-level" && $0.isWritable }) {
                return Found(service: service, property: p)
            }
            throw MiotCommandError.unsupported("fan-level")
        }

        func writePercentage(_ found: Found, percent: Int) -> MiotPropWrite {
            write(found, .number(MiotPercentScale.raw(fromPercent: Double(percent), property: found.property, curve: .speed)))
        }

        func action(_ candidates: [String], requireEmptyInputs: Bool = false) throws -> MiotActionCall {
            let ordered = [service] + spec.services.filter { $0.iid != service.iid }
            for name in candidates {
                for s in ordered {
                    if let a = s.action(named: name), !requireEmptyInputs || a.inputProperties.isEmpty {
                        return MiotActionCall(did: did, siid: s.iid, aiid: a.iid)
                    }
                }
            }
            throw MiotCommandError.unsupported(candidates.first ?? "action")
        }

        /// Clamp to the spec's value-range and match the wire format (bool / integer / float).
        func coerce(_ value: JSONValue, for p: MiotProperty) -> JSONValue {
            if p.format == "bool" { return .bool(value.miotBool ?? false) }
            guard var n = value.doubleValue else { return value }
            if let lo = p.rangeMin, let hi = p.rangeMax { n = Swift.min(hi, Swift.max(lo, n)) }
            if p.isIntegerFormat {
                n = n.rounded()
            } else if let step = p.rangeStep, step > 0 {
                let base = p.rangeMin ?? 0
                n = base + ((n - base) / step).rounded() * step
            }
            return .number(n)
        }
    }
}

extension DeviceCommand {
    var targetEntityId: String {
        switch self {
        case .setLightPower(let id, _), .setLightBrightness(let id, _), .setLightColorTemp(let id, _),
             .setLightEffect(let id, _), .setClimatePower(let id, _, _), .setClimateMode(let id, _),
             .setClimateTemperature(let id, _), .setClimateFanMode(let id, _), .setClimateSwingMode(let id, _),
             .setFanPower(let id, _), .setFanPercentage(let id, _), .setFanPresetMode(let id, _), .setFanOscillating(let id, _),
             .setSwitch(let id, _), .setSelectOption(let id, _), .setNumberValue(let id, _),
             .setHumidity(let id, _),
             .vacuumAction(let id, _), .mediaSetVolume(let id, _), .mediaSelectSource(let id, _),
             .pressButton(let id), .sendNotifyMessage(let id, _), .activateScene(let id),
             .setMiotProperty(let id, _, _), .callMiotAction(let id, _):
            return id
        }
    }
}
