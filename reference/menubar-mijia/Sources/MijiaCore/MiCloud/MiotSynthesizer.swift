import Foundation

/// Entity ids handed to `SmartDevice` specs on the Xiaomi cloud backend: `miot:<did>:<siid>`.
public enum MiotAddress {
    public static func make(did: String, siid: Int) -> String { "miot:\(did):\(siid)" }

    public static func makeAction(did: String, siid: Int, aiid: Int) -> String {
        "miot-action:\(did):\(siid):\(aiid)"
    }

    public static func parseAction(_ entityId: String) -> (did: String, siid: Int, aiid: Int)? {
        guard entityId.hasPrefix("miot-action:") else { return nil }
        let parts = entityId.dropFirst("miot-action:".count).split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count >= 3, let aiid = Int(parts[parts.count - 1]), let siid = Int(parts[parts.count - 2]) else { return nil }
        let did = parts.dropLast(2).joined(separator: ":")
        return did.isEmpty ? nil : (did, siid, aiid)
    }

    public static func parse(_ entityId: String) -> (did: String, siid: Int)? {
        if let action = parseAction(entityId) { return (action.did, action.siid) }
        guard entityId.hasPrefix("miot:") else { return nil }
        let rest = entityId.dropFirst("miot:".count)
        guard let sep = rest.lastIndex(of: ":"), let siid = Int(rest[rest.index(after: sep)...]) else { return nil }
        let did = String(rest[..<sep])
        return did.isEmpty ? nil : (did, siid)
    }
}

// MARK: - Device Synthesis for MIoT Devices

public enum MiotSynthesizer {
    typealias KindBuilder = @Sendable (MiotReader, MiotService) -> DeviceKind

    /// Primary service short name → builder. Order is the fallback priority when the device
    /// type itself (urn segment 3) does not name one of these services.
    static let registry: [(service: String, build: KindBuilder)] = [
        ("air-conditioner", buildClimate),
        ("heater", buildClimate),
        ("thermostat", buildClimate),
        ("electric-blanket", buildClimate),
        ("dehumidifier", buildDehumidifier),
        ("diffuser", buildPlug),
        ("speaker", buildSpeaker),
        ("light", buildLight),
        ("fan", buildFan),
        ("air-purifier", buildAirPurifier),
        ("air-fresh", buildAirPurifier),
        ("humidifier", buildAirPurifier),
        ("vacuum", buildVacuum),
        ("curtain", buildCover),
        ("window-opener", buildCover),
        ("airer", buildCover),
        ("fridge", buildFridge),
        ("camera-control", buildCamera),
        ("video-doorbell", buildCamera),
        ("outlet", buildPlug),
        ("switch", buildPlug),
        ("electronic-valve", buildBreaker),
        ("kettle", buildKitchenAppliance),
        ("ble-kettle", buildKitchenAppliance),
        ("water-dispenser", buildKitchenAppliance),
        ("cooker", buildKitchenAppliance),
        ("air-fryer", buildKitchenAppliance),
        ("oven", buildKitchenAppliance),
        ("microwave-oven", buildKitchenAppliance),
        ("health-pot", buildKitchenAppliance),
        ("water-heater", buildApplianceCard),
        ("washer", buildApplianceCard),
        ("dryer", buildApplianceCard),
        ("clothes-dryer", buildApplianceCard),
        ("dishwasher", buildApplianceCard),
        ("lock", buildSensor),
        ("motion-sensor", buildSensor),
        ("occupancy-sensor", buildSensor),
        ("magnet-sensor", buildSensor),
        ("submersion-sensor", buildSensor),
        ("illumination-sensor", buildSensor),
        ("air-monitor", buildSensor),
        ("temperature-humidity-sensor", buildSensor),
    ]

    public static func synthesize(
        devices: [MiDeviceRecord],
        rooms: [MiRoom],
        specs: [String: MiotDeviceSpec],
        values: [MiotPropKey: JSONValue]
    ) -> SynthesizedHome {
        var roomByDid: [String: MiRoom] = [:]
        for room in rooms {
            for did in room.dids where roomByDid[did] == nil { roomByDid[did] = room }
        }

        // A panel whose keys are listed as split devices is shown through those keys only.
        let splitParents = Set(devices.compactMap(\.splitParentDid))

        var smartDevices: [SmartDevice] = []
        var readings: [EnvironmentReading] = []
        var identities: [String: DeviceSourceIdentity] = [:]
        var facetsByDevice: [String: [FacetControl]] = [:]

        for record in devices where !splitParents.contains(record.did) {
            guard let spec = specs[record.model] else { continue }
            let scopedSpec = specForRecord(record, spec: spec)

            let room = roomByDid[record.did] ?? record.splitParentDid.flatMap { roomByDid[$0] }
            let roomName = DeviceSynthesizer.normalizeRoomName(room?.name ?? "")
            let roomId = DeviceSynthesizer.normalizedRoomId(for: roomName, fallback: room.map { "mi_room_\($0.id)" } ?? "other")
            let name = DeviceSynthesizer.cleanDeviceName(record.name, roomName: roomName, model: record.model)

            let plans = plans(for: record, spec: spec)
            // The last rung of the resolution ladder (#6): no dedicated builder, but the generic
            // facet layer still finds a control or a reading. The card keeps the device on screen
            // instead of dropping it into "devices not shown".
            if plans.isEmpty {
                guard !Self.isMainsBreaker(spec),
                      let generic = genericCard(record: record, spec: scopedSpec, values: values) else { continue }
                let device = SmartDevice(
                    id: record.did,
                    name: name,
                    roomId: roomId,
                    roomName: roomName,
                    model: record.model,
                    isOnline: record.isOnline,
                    kind: .generic(generic.spec),
                    isSharedHome: room?.isSharedHome ?? false
                )
                smartDevices.append(device)
                identities[device.id] = DeviceSourceIdentity(
                    dids: [record.did],
                    macAddresses: record.splitParentDid == nil ? [record.mac].compactMap { $0 } : [],
                    model: record.model,
                    name: device.name,
                    roomName: roomName,
                    kindTag: .generic
                )
                facetsByDevice[device.id] = generic.facets
                continue
            }

            let reader = MiotReader(did: record.miotDid, spec: scopedSpec, values: values)
            let splitPerService = plans.count > 1

            for (service, build) in plans {
                let kind = build(reader, service)
                let device = SmartDevice(
                    id: splitPerService ? trackID(record: record, siid: service.iid) : record.did,
                    name: splitPerService ? trackName(service: service, fallback: name) : name,
                    roomId: roomId,
                    roomName: roomName,
                    model: record.model,
                    isOnline: record.isOnline,
                    kind: record.isOnline ? kind : Self.redactOfflineLockClaim(kind),
                    isSharedHome: room?.isSharedHome ?? false
                )
                smartDevices.append(device)
                identities[device.id] = DeviceSourceIdentity(
                    dids: [record.did],
                    macAddresses: record.splitParentDid == nil ? [record.mac].compactMap { $0 } : [],
                    model: record.model,
                    name: device.name,
                    roomName: roomName,
                    kindTag: kind.tag
                )

                if let reading = device.environmentReading {
                    readings.append(reading)
                }

                // A split key or a dual-track motor gets only its own service; everything else
                // may offer controls from sibling services (fresh air, floor heat, …).
                let facetSpec = splitPerService
                    ? MiotDeviceSpec(type: spec.type, services: spec.services.filter { $0.iid == service.iid })
                    : scopedSpec
                // A mains breaker stays read-only even with advanced writes on (#265).
                let facets = Self.isMainsBreaker(spec) ? [] : MiotFacetEngine.controls(
                    for: facetSpec,
                    did: record.miotDid,
                    values: values,
                    primaryServiceId: service.iid,
                    excluding: reader.consumed,
                    model: record.model
                )
                if !facets.isEmpty {
                    facetsByDevice[device.id] = facets
                }
            }
        }

        var home = DeviceSynthesizer.assembleHome(
            devices: smartDevices,
            readings: readings,
            scenes: [],
            roomNameOrder: DeviceSynthesizer.roomNameOrder(from: rooms.map(\.name))
        )
        home.deviceIdentities = identities
        home.deviceFacets = facetsByDevice
        return home
    }

    /// The (did, siid, piid) triples the synthesizer reads for this device; poll exactly these.
    public static func requiredProps(for device: MiDeviceRecord, spec: MiotDeviceSpec) -> [MiotPropKey] {
        let plans = plans(for: device, spec: spec)
        guard !plans.isEmpty else { return [] }
        let scopedSpec = specForRecord(device, spec: spec)
        let reader = MiotReader(did: device.miotDid, spec: scopedSpec, values: [:])
        for (service, build) in plans {
            _ = build(reader, service)
        }
        return reader.touched
    }

    /// Readable properties behind the 更多设置 rows (#5), polled so the drawer's values arrive with
    /// the snapshot instead of showing empty rows until a write. Card properties are excluded here
    /// because `requiredProps` already covers them.
    public static func facetProps(for device: MiDeviceRecord, spec: MiotDeviceSpec) -> [MiotPropKey] {
        guard !Self.isMainsBreaker(spec) else { return [] }
        let scopedSpec = specForRecord(device, spec: spec)
        let plans = plans(for: device, spec: spec)
        if plans.isEmpty {
            // A generic card has no dedicated builder, so every readable facet property is its
            // state: readings and the main switch both arrive with the same poll (#6).
            var keys: [MiotPropKey] = []
            var seen: Set<MiotPropKey> = []
            for facet in MiotFacetEngine.facets(for: scopedSpec, model: device.model) {
                guard let piid = facet.propertyId,
                      let property = scopedSpec.service(iid: facet.serviceId)?
                          .properties.first(where: { $0.iid == piid }),
                      property.isReadable else { continue }
                let key = MiotPropKey(did: device.miotDid, siid: facet.serviceId, piid: piid)
                if seen.insert(key).inserted { keys.append(key) }
            }
            return keys
        }
        let reader = MiotReader(did: device.miotDid, spec: scopedSpec, values: [:])
        var keys: [MiotPropKey] = []
        var seen: Set<MiotPropKey> = []
        for (service, build) in plans {
            _ = build(reader, service)
            let touched = Set(reader.touched)
            let facetSpec = plans.count > 1
                ? MiotDeviceSpec(type: spec.type, services: spec.services.filter { $0.iid == service.iid })
                : scopedSpec
            for facet in MiotFacetEngine.facets(for: facetSpec, model: device.model) {
                guard let piid = facet.propertyId,
                      let property = facetSpec.service(iid: facet.serviceId)?
                          .properties.first(where: { $0.iid == piid }),
                      property.isReadable else { continue }
                let key = MiotPropKey(did: device.miotDid, siid: facet.serviceId, piid: piid)
                guard !touched.contains(key), seen.insert(key).inserted else { continue }
                keys.append(key)
            }
        }
        return keys
    }

    /// MIoT `<siid>.<piid>` keys for a sensor's temperature and relative-humidity properties,
    /// used when querying Xiaomi Cloud's statistics / history endpoints.
    public static func sensorPropertyKeys(for device: MiDeviceRecord, spec: MiotDeviceSpec) -> (temperature: String?, humidity: String?) {
        guard let (service, _) = primary(for: device, spec: spec) else { return (nil, nil) }
        let scopedSpec = specForRecord(device, spec: spec)
        let reader = MiotReader(did: device.miotDid, spec: scopedSpec, values: [:])
        let readingServices = [service] + scopedSpec.services(named: "environment") + scopedSpec.services(named: "temperature-humidity-sensor")
        let temp = reader.first("temperature", in: readingServices)
        let hum = reader.first("relative-humidity", in: readingServices)
        let tempKey = temp.map { "\($0.service.iid).\($0.property.iid)" }
        let humKey = hum.map { "\($0.service.iid).\($0.property.iid)" }
        return (tempKey, humKey)
    }

    /// Narrows a multi-key panel spec to the switch service controlled by a split key.
    static func specForRecord(_ record: MiDeviceRecord, spec: MiotDeviceSpec) -> MiotDeviceSpec {
        guard record.splitParentDid != nil, let siid = record.splitSiid else { return spec }
        return MiotDeviceSpec(type: spec.type, services: spec.services.filter { $0.iid == siid })
    }

    /// Registry key of the builder that will handle this spec, or nil when unsupported.
    public static func supportedServiceName(for spec: MiotDeviceSpec) -> String? {
        primary(for: spec)?.0.shortName
    }

    /// The fallback card content (#6): every generic facet, the first low-risk `on` as the main
    /// switch, and up to two high-value readings. Nil when the facet layer finds nothing.
    static func genericCard(
        record: MiDeviceRecord,
        spec: MiotDeviceSpec,
        values: [MiotPropKey: JSONValue]
    ) -> (spec: GenericSpec, facets: [FacetControl])? {
        let controls = MiotFacetEngine.controls(
            for: spec,
            did: record.miotDid,
            values: values,
            primaryServiceId: nil,
            model: record.model
        )
        guard !controls.isEmpty else { return nil }
        let power = controls.enumerated()
            .filter { candidate in
                guard candidate.element.kind == .toggle,
                      candidate.element.name == "on",
                      candidate.element.isWritable,
                      candidate.element.writesByDefault,
                      candidate.element.target != nil else { return false }
                // Only the device's own main service may supply the tile's switch. A companion
                // indicator, night light or ambient light must never impersonate device power.
                return isMainPowerService(candidate.element.serviceName, deviceType: spec.shortName)
            }
            .sorted { lhs, rhs in
                let lhsOwn = lhs.element.serviceName == spec.shortName
                let rhsOwn = rhs.element.serviceName == spec.shortName
                if lhsOwn != rhsOwn { return lhsOwn }
                return lhs.offset < rhs.offset
            }
            .first?.element
        // The tile's main switch is not repeated under 更多设置.
        var drawerFacets = controls
        if let power, let index = drawerFacets.firstIndex(where: { $0.id == power.id }) {
            drawerFacets.remove(at: index)
        }
        let override = MiotFacetEngine.profileOverride(for: spec, model: record.model)
        return (
            GenericSpec(
                deviceType: spec.shortName,
                typeTitle: genericTypeTitle(for: spec),
                isOn: power?.isOn == true,
                powerControl: power,
                isReadOnly: !controls.contains { $0.isWritable },
                readouts: genericReadouts(controls, preferred: override?.readouts ?? []),
                alerts: genericAlerts(controls),
                symbolName: genericSymbolName(for: spec.shortName)
            ),
            drawerFacets
        )
    }

    /// Whether a service can carry the generic tile's main switch. The device's own service is
    /// the only candidate; light-like types may also use their `light` service. Companion
    /// services (`indicator-light`, `night-light`, `ambient-light`, `screen`, `led`) never qualify.
    static func isMainPowerService(_ serviceName: String, deviceType: String) -> Bool {
        if serviceName == deviceType { return true }
        if deviceType.hasPrefix(serviceName + "-") || deviceType.hasSuffix("-" + serviceName) {
            return true
        }
        if deviceType.contains("light") || deviceType.contains("lamp") {
            return serviceName == "light"
        }
        return false
    }

    /// At most two readings. A family's preferred names come first (water temperature for a fish
    /// tank, food level for a feeder), then high-value facets and spec order fill any free slot.
    static func genericReadouts(
        _ controls: [FacetControl],
        preferred: [String] = []
    ) -> [GenericSpec.Readout] {
        var picked: [FacetControl] = []
        for pattern in preferred {
            guard picked.count < 2 else { break }
            guard let match = controls.first(where: { control in
                control.kind == .reading
                    && control.value != nil
                    && MiotFacetEngine.matches(control.name, pattern: pattern)
                    && !picked.contains { $0.id == control.id }
            }) else { continue }
            picked.append(match)
        }
        if picked.isEmpty {
            picked = Array(
                controls.enumerated()
                    .filter { $0.element.kind == .reading && $0.element.value != nil }
                    .sorted { lhs, rhs in
                        if lhs.element.isHighValue != rhs.element.isHighValue {
                            return lhs.element.isHighValue
                        }
                        return lhs.offset < rhs.offset
                    }
                    .prefix(2)
                    .map(\.element)
            )
        }
        return picked.map { GenericSpec.Readout(value: MiotFacetText.value($0)) }
    }

    /// Readings whose current option says something is wrong: a pump on low water, a blocked or
    /// faulted pump, an empty food bin, a filter that needs changing (#7). The card and the drawer
    /// show these instead of leaving the only trace inside 更多设置.
    static func genericAlerts(_ controls: [FacetControl]) -> [String] {
        var alerts: [String] = []
        var seen: Set<String> = []
        for control in controls {
            guard control.kind == .reading,
                  let raw = control.value?.intValue,
                  let option = control.options.first(where: { $0.value == raw }),
                  MiotFacetEngine.isAlertLabel(option.label) else { continue }
            let title = MiotFacetText.title(name: control.name, title: control.title)
            let text = MiotFacetEngine.alertText(
                title: title,
                propertyName: control.name,
                label: option.label
            )
            if seen.insert(text).inserted { alerts.append(text) }
        }
        return alerts
    }

    /// Device-type label for a generic card. Known MIoT types get a Chinese name; otherwise the
    /// spec's own description or a humanized short name keeps the card honest.
    static func genericTypeTitle(for spec: MiotDeviceSpec) -> String {
        let known: [String: String] = [
            "fish-tank": "鱼缸",
            "pet-feeder": "宠物喂食器",
            "router": "路由器",
            "gateway": "网关",
            "repeater": "信号放大器",
            "lock": "门锁",
            "scale": "体重秤",
            "body-fat-scale": "体脂秤",
            "bracelet": "手环",
            "watch": "手表",
            "toothbrush": "牙刷",
            "treadmill": "跑步机",
            "massage": "按摩设备",
            "bed": "床",
            "mattress": "床垫",
            "pillow": "枕头",
            "sofa": "沙发",
            "mirror": "镜子",
            "shower": "淋浴",
            "toilet": "马桶",
            "towel-rack": "毛巾架",
            "car-seat": "儿童安全座椅",
            "tracker": "定位器",
            "projector": "投影仪",
            "television": "电视",
            "stb": "机顶盒",
            "stove": "燃气灶",
            "boiler": "壁挂炉",
            "clock": "时钟",
            "coffee-machine": "咖啡机",
        ]
        if !L10n.isEnglish, let chinese = known[spec.shortName] { return chinese }
        let description = spec.description?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return description.isEmpty ? MiotFacetText.humanized(spec.shortName) : description
    }

    static func genericSymbolName(for deviceType: String) -> String {
        switch deviceType {
        case "fish-tank": return "fish.fill"
        case "pet-feeder": return "pawprint.fill"
        case "router", "gateway", "repeater": return "wifi"
        case "lock": return "lock.fill"
        case "scale", "body-fat-scale": return "scalemass.fill"
        case "bracelet", "watch": return "applewatch"
        case "toothbrush": return "mouth.fill"
        case "treadmill": return "figure.run"
        case "bed", "mattress", "pillow": return "bed.double.fill"
        case "sofa", "chair", "massage": return "chair.lounge.fill"
        case "projector": return "projector.fill"
        case "television", "stb": return "tv"
        case "boiler": return "water.waves"
        case "shower": return "drop.fill"
        case "mirror": return "rectangle.portrait.fill"
        case "car-seat": return "car.fill"
        case "tracker": return "location.fill"
        case "bike": return "bicycle"
        case "clock": return "clock.fill"
        case "coffee-machine": return "cup.and.saucer.fill"
        default: return "square.grid.2x2"
        }
    }

    /// The cards a record produces: normally one. A dual-track curtain motor publishes two
    /// `curtain` services (drape + sheer) and gets one card per track, the same split shape as a
    /// multi-key panel's keys.
    static func plans(for record: MiDeviceRecord, spec: MiotDeviceSpec) -> [(MiotService, KindBuilder)] {
        let single = primary(for: record, spec: spec).map { [$0] } ?? []
        guard record.splitParentDid == nil,
              spec.shortName == "curtain",
              let build = registry.first(where: { $0.service == "curtain" })?.build else {
            return single
        }
        let tracks = spec.services(named: "curtain").filter { service in
            ["motor-control", "current-position", "target-position"].contains { service.property(named: $0) != nil }
        }
        return tracks.count > 1 ? tracks.map { ($0, build) } : single
    }

    /// Card id for one track of a multi-track motor, shaped like the cloud's split-key dids.
    static func trackID(record: MiDeviceRecord, siid: Int) -> String {
        "\(record.did).s\(siid)"
    }

    /// A track card takes its service's description ("Drape Curtain" / "Lace Curtain").
    static func trackName(service: MiotService, fallback: String) -> String {
        let description = service.description?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        switch description.lowercased() {
        case "drape curtain": return L10n.tr("布帘", description)
        case "lace curtain": return L10n.tr("纱帘", description)
        case "left curtain": return L10n.tr("左帘", description)
        case "right curtain": return L10n.tr("右帘", description)
        case "curtain": return L10n.tr("窗帘", description)
        case "": return fallback
        default: return description
        }
    }

    /// Split keys use their own switch service instead of the panel's first one.
    static func primary(for record: MiDeviceRecord, spec: MiotDeviceSpec) -> (MiotService, KindBuilder)? {
        guard record.splitParentDid != nil, let siid = record.splitSiid else { return primary(for: spec) }
        guard let service = spec.service(iid: siid),
              let entry = registry.first(where: { $0.service == service.shortName }) else { return nil }
        return (service, entry.build)
    }

    /// Services that count as this device type's own primary service.
    static func preferredServices(forDeviceType deviceType: String) -> [String] {
        if let profile = DeviceProfiles.current.appliances[deviceType] { return profile.primary }
        switch deviceType {
        case "outlet": return ["outlet", "switch"]
        case "camera", "cateye", "video-doorbell": return ["camera-control", "video-doorbell"]
        case "kettle", "ble-kettle": return ["kettle", "ble-kettle", "water-dispenser"]
        default: return [deviceType]
        }
    }

    static func primary(for spec: MiotDeviceSpec) -> (MiotService, KindBuilder)? {
        let deviceType = spec.shortName
        // A device type the profile table lists as an appliance takes the first service the table
        // names, even though the type itself has no same-name service (#4).
        if let profile = DeviceProfiles.current.appliances[deviceType] {
            for serviceName in profile.primary {
                if let service = spec.services(named: serviceName).first {
                    return (service, buildApplianceCard)
                }
            }
            return nil
        }
        for serviceName in preferredServices(forDeviceType: deviceType) {
            if let entry = registry.first(where: { $0.service == serviceName }),
               claims(entry.service, spec: spec),
               let service = spec.services(named: entry.service).first {
                return (service, entry.build)
            }
        }
        for entry in registry {
            guard fallbackAllows(deviceType: deviceType, service: entry.service),
                  claims(entry.service, spec: spec) else { continue }
            if let service = spec.services(named: entry.service).first {
                return (service, entry.build)
            }
        }
        // Sensors that publish readings under a generic `environment` service.
        if deviceType.hasSuffix("sensor"),
           let env = spec.services(named: "environment").first,
           env.property(named: "temperature") != nil || env.property(named: "relative-humidity") != nil {
            return (env, buildSensor)
        }
        return nil
    }

    /// Whether a device whose type names none of its own services may be drawn from `service` (#296).
    /// Only the pairs in `deviceProfiles.json`'s `fallbackClaims` may, so a projector is no longer a
    /// speaker and an insect killer no longer a camera or vacuum. A `light` service may still claim
    /// light-like types (`*light*`, `*lamp*`); it must not claim a hood, airer, bath heater or
    /// ceiling fan, whose cards would only toggle their lamp while the main function stays hidden.
    static func fallbackAllows(deviceType: String, service: String) -> Bool {
        if service == "light", deviceType.contains("light") || deviceType.contains("lamp") { return true }
        return DeviceProfiles.current.fallbackClaims[deviceType]?.contains(service) == true
    }

    /// An `electronic-valve` is only a mains breaker when it meters electric power (#265). A water
    /// or gas shut-off valve uses the same service and stays unsupported rather than becoming a plug.
    static func claims(_ serviceName: String, spec: MiotDeviceSpec) -> Bool {
        if serviceName == "water-heater" { return spec.shortName == "water-heater" }
        guard serviceName == "electronic-valve" else { return true }
        return isMainsBreaker(spec)
    }

    public static func isMainsBreaker(_ spec: MiotDeviceSpec) -> Bool {
        !spec.services(named: "electronic-valve").isEmpty
            && spec.services(named: "power-consumption").contains { $0.property(named: "electric-power") != nil }
    }

    // MARK: Builders

    static func buildLight(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let bri = r.ref("brightness", in: s)
        let ct = r.ref("color-temperature", in: s)
        let mode = r.ref("mode", in: s)
        let isLightGroup = r.did.hasPrefix("group.")
        let effects = (!isLightGroup && mode?.property.isWritable == true)
            ? (mode?.property.valueList?.map(\.description) ?? [])
            : []
        return .light(LightSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: on?.value?.miotBool ?? false,
            brightnessPercent: bri.flatMap { ref in ref.value?.doubleValue.map { MiotPercentScale.percent(fromRaw: $0, property: ref.property, curve: .proportional) } },
            colorTempKelvin: ct?.value?.intValue,
            minKelvin: ct?.property.rangeMin.map { Int($0) } ?? 2700,
            maxKelvin: ct?.property.rangeMax.map { Int($0) } ?? 6500,
            currentEffect: isLightGroup ? nil : mode?.valueDescription,
            availableEffects: effects,
            powerWritable: on?.property.isWritable == true,
            brightnessWritable: bri?.property.isWritable ?? true,
            colorWritable: ct?.property.isWritable ?? true
        ))
    }

    static func buildClimate(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let mode = s.shortName == "electric-blanket" ? nil : r.ref("mode", in: s)
        let target = r.ref("target-temperature", in: s)
        let current = s.shortName == "electric-blanket" ? nil : r.first("temperature", in: [s] + r.spec.services(named: "environment") + r.spec.services(named: "temperature-humidity-sensor"))
        let fanLevel = r.first("fan-level", in: r.spec.services(named: "fan-control") + [s])

        let isOn = on?.value?.miotBool ?? false
        let onWritable = on?.property.isWritable == true
        let modeWritable = mode?.property.isWritable == true
        let exposeModes = modeWritable || (mode == nil && onWritable)
        var haModes: [String] = []
        if exposeModes {
            for item in mode?.property.valueList ?? [] {
                if let m = hvacMode(forDescription: item.description), !haModes.contains(m) { haModes.append(m) }
            }
            if haModes.isEmpty { haModes = [s.shortName == "air-conditioner" ? "cool" : "heat"] }
        }
        let activeMode = mode?.valueDescription.flatMap(hvacMode(forDescription:))
            ?? haModes.first
            ?? (s.shortName == "air-conditioner" ? "cool" : "heat")

        return .climate(ClimateSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: isOn,
            hvacMode: isOn ? activeMode : "off",
            availableModes: exposeModes ? haModes + ["off"] : [],
            targetTemperature: target?.value?.doubleValue ?? 26,
            currentTemperature: current?.value?.doubleValue,
            minTemp: target?.property.rangeMin ?? 16,
            maxTemp: target?.property.rangeMax ?? 30,
            step: target?.property.rangeStep ?? 1,
            fanMode: fanLevel?.valueDescription,
            availableFanModes: fanLevel?.property.isWritable == true ? (fanLevel?.property.valueList?.map(\.description) ?? []) : [],
            powerWritable: onWritable,
            temperatureWritable: target?.property.isWritable ?? true
        ))
    }

    static func buildFan(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let mode = r.ref("mode", in: s)
        let levels = r.refs("fan-level", in: s)
        let swing = r.ref("horizontal-swing", in: s)
        let percentRef = levels.first { $0.property.valueRange != nil }
        let presetRef = (mode?.property.valueList?.isEmpty == false) ? mode : levels.first { $0.property.valueList?.isEmpty == false }

        return .fan(FanSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: on?.value?.miotBool ?? false,
            percentage: percentRef.flatMap { ref in ref.value?.doubleValue.map { MiotPercentScale.percent(fromRaw: $0, property: ref.property, curve: .speed) } },
            presetMode: presetRef?.valueDescription,
            availablePresetModes: presetRef?.property.isWritable == true ? (presetRef?.property.valueList?.map(\.description) ?? []) : [],
            isOscillating: swing?.value?.miotBool,
            powerWritable: on?.property.isWritable == true,
            speedWritable: percentRef?.property.isWritable ?? true,
            swingWritable: swing?.property.isWritable ?? true
        ))
    }

    static func buildAirPurifier(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let mode = r.ref("mode", in: s)
        let levels = r.refs("fan-level", in: s)
        let favServices = r.spec.services(named: "air-purifier-favorite")
            + r.spec.services(named: "air-fresh-favorite")
            + r.spec.services(named: "motor-speed")
            + r.spec.services(named: "custom-service")
            + r.spec.services(named: "fan-control")
        let favSpeed = r.first("fan-level", in: favServices)
            ?? r.first("favorite-fan-level", in: favServices)
            ?? r.first("favorite-level", in: favServices)
            ?? r.first("favorite-speed", in: favServices)
        let speedRef = favSpeed
            ?? levels.first { $0.property.valueRange != nil }
            ?? levels.first { $0.property.valueList?.isEmpty == false }
        let presetRef = (mode?.property.valueList?.isEmpty == false)
            ? mode
            : levels.first { $0.property.valueList?.isEmpty == false }

        let envServices = r.spec.services(named: "environment")
            + r.spec.services(named: "temperature-humidity-sensor")
            + [s]
        let pm25 = r.first("pm2.5-density", in: envServices)
        let airQuality = r.first("air-quality", in: envServices)
        let temp = r.first("temperature", in: envServices)
        let hum = r.first("relative-humidity", in: envServices)
        let filterServices = r.spec.services(named: "filter") + [s]
        let filterLife = r.first("filter-life-level", in: filterServices)

        return .fan(FanSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: on?.value?.miotBool ?? false,
            percentage: speedRef.flatMap { ref in ref.value?.doubleValue.map { MiotPercentScale.percent(fromRaw: $0, property: ref.property, curve: .speed) } },
            presetMode: presetRef?.valueDescription,
            availablePresetModes: presetRef?.property.isWritable == true ? (presetRef?.property.valueList?.map(\.description) ?? []) : [],
            isOscillating: nil,
            pm25Density: pm25?.value?.intValue,
            airQuality: airQuality?.valueDescription,
            temperature: temp?.value?.doubleValue,
            humidity: hum?.value?.doubleValue,
            filterLifePercent: filterLife?.value?.intValue,
            powerWritable: on?.property.isWritable == true,
            speedWritable: speedRef?.property.isWritable ?? true
        ))
    }

    static func buildDehumidifier(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let mode = r.ref("mode", in: s)
        let environment = r.spec.services(named: "environment")
        let temperature = r.first("temperature", in: environment)
        let humidity = r.first("relative-humidity", in: environment)
        return .fan(FanSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: on?.value?.miotBool ?? false,
            presetMode: mode?.valueDescription,
            availablePresetModes: mode?.property.isWritable == true ? mode?.property.valueList?.map(\.description) ?? [] : [],
            temperature: temperature?.value?.doubleValue,
            humidity: humidity?.value?.doubleValue,
            powerWritable: on?.property.isWritable == true
        ))
    }

    static func buildSpeaker(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let volume = r.ref("volume", in: s)
        let mute = r.ref("mute", in: s)
        let playing = r.first("playing-state", in: r.spec.services(named: "play-control"))
        let intelligent = r.spec.services(named: "intelligent-speaker").first
        let sleep = intelligent.flatMap { r.ref("sleep-mode", in: $0) }
        func actionAddress(_ service: MiotService?, _ name: String, inputs: [String]) -> String? {
            guard let service, let action = service.action(named: name),
                  action.inputProperties.count == inputs.count,
                  zip(action.inputProperties, inputs).allSatisfy({ id, name in
                      service.properties.contains { $0.iid == id && $0.shortName == name }
                  }) else { return nil }
            return MiotAddress.makeAction(did: r.did, siid: service.iid, aiid: action.iid)
        }
        let state: String = switch playing?.valueDescription?.lowercased() {
        case "playing", "play": "playing"
        case "paused", "pause": "paused"
        default: "idle"
        }
        return .speaker(SpeakerSpec(
            mediaEntityId: MiotAddress.make(did: r.did, siid: s.iid),
            state: state,
            volumePercent: volume.flatMap { ref in ref.value?.doubleValue.map { MiotPercentScale.percent(fromRaw: $0, property: ref.property, curve: .proportional) } } ?? 0,
            isMuted: mute?.value?.miotBool ?? false,
            muteSwitchEntityId: mute?.property.isWritable == true ? MiotAddress.make(did: r.did, siid: s.iid) : nil,
            sleepModeEntityId: sleep?.property.isWritable == true ? intelligent.map { MiotAddress.make(did: r.did, siid: $0.iid) } : nil,
            isSleepMode: sleep?.value?.miotBool ?? false,
            ttsNotifyEntityId: actionAddress(intelligent, "play-text", inputs: ["text-content"]),
            directiveNotifyEntityId: actionAddress(intelligent, "execute-text-directive", inputs: ["text-content", "silent-execution"]),
            playMusicButtonId: actionAddress(intelligent, "play-music", inputs: []),
            stopAlarmButtonId: actionAddress(r.spec.services(named: "clock").first, "stop-alarm", inputs: [])
        ))
    }

    static func buildPlug(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let consumptionServices = r.spec.services(named: "power-consumption") + r.spec.services
        let power = r.first("electric-power", in: consumptionServices)
        let energy = r.first("power-consumption", in: consumptionServices, where: { energyScale(for: $0) != nil })
        let energyKwh = energy.flatMap { ref -> Double? in
            guard let raw = ref.value?.doubleValue, raw.isFinite, raw >= 0,
                  let scale = energyScale(for: ref.property) else { return nil }
            return (raw * scale * 1000).rounded() / 1000
        }
        let lockService = r.spec.services(named: "physical-controls-locked").first
        let lock = lockService.flatMap { r.ref("physical-controls-locked", in: $0) }

        return .plug(PlugSpec(
            switchEntityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: on?.value?.miotBool ?? false,
            powerWatts: power.flatMap { ref in
                guard let raw = ref.value?.doubleValue, raw.isFinite, raw >= 0,
                      let scale = powerScale(for: ref.property) else { return nil }
                let watts = raw * scale
                return watts.isFinite ? watts : nil
            },
            energyKwh: energyKwh,
            childLockEntityId: lock.map { MiotAddress.make(did: r.did, siid: $0.service.iid) },
            isChildLocked: lock?.value?.miotBool ?? false,
            powerWritable: on?.property.isWritable == true
        ))
    }

    /// A mains breaker / recloser (e.g. `sanmei.valve.s1`, #265) is a read-only meter: switching it
    /// cuts power to the whole circuit, so the card never offers a toggle and the command mapper
    /// refuses every write. Its power covers the downstream plugs, so it stays out of the home total.
    static func buildBreaker(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let kind = buildPlug(r, s)
        guard case .plug(var spec) = kind else { return kind }
        spec.childLockEntityId = nil
        spec.isChildLocked = false
        spec.powerWritable = false
        spec.countsInTotalPower = false
        return .plug(spec)
    }

    static func buildVacuum(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let status = r.ref("status", in: s)
        let mode = r.ref("mode", in: s)
        let battery = r.first("battery-level", in: r.spec.services(named: "battery") + [s])

        let vacuumStartActions = ["start-sweep", "start-clean", "start-sweep-mop", "start-mop"]
        let actionsWritable = r.spec.services.contains { service in
            vacuumStartActions.contains { service.action(named: $0) != nil }
        }
        return .vacuum(VacuumSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            status: vacuumStatus(forDescription: status?.valueDescription),
            battery: battery?.value?.intValue,
            modeEntityId: mode.map { MiotAddress.make(did: r.did, siid: $0.service.iid) },
            currentMode: mode?.valueDescription,
            availableModes: mode?.property.valueList?.map(\.description) ?? [],
            actionsWritable: actionsWritable
        ))
    }
    static func buildCover(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let current = r.ref("current-position", in: s)
        let target = r.ref("target-position", in: s)
        let status = r.ref("status", in: s)
        let fault = r.ref("device-fault", in: s) ?? r.ref("fault", in: s)
        let battery = r.first("battery-level", in: r.spec.services(named: "battery"))
        let motor = r.ref("motor-control", in: s, includingWriteOnly: true)
        let items = motor?.property.isWritable == true ? motor?.property.valueList ?? [] : []
        // An airer reports how far it has lowered (0 at the top); the card shows height (#299).
        let inverted = CoverSubtype(miotServiceShortName: s.shortName).invertsMiotPosition
        let currentPosition = coverPosition(current).map { inverted ? 100 - $0 : $0 }
        let targetPosition = coverPosition(target).map { inverted ? 100 - $0 : $0 }
        let statusWord = CoverStatusWord(status?.valueDescription)
        let statusDefinesTravel = coverStatusDefinesTravel(status)
        let motion = coverMotion(
            statusDefinesTravel: statusDefinesTravel,
            word: statusWord,
            current: currentPosition,
            target: targetPosition
        )
        let faultCode = fault?.value?.intValue
        let faultText = faultDescription(
            code: faultCode,
            description: faultCode.flatMap { fault?.property.description(forValue: $0) }
        )
        return .cover(CoverSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            subtype: CoverSubtype(miotServiceShortName: s.shortName),
            positionPercent: coverDisplayPosition(
                current: currentPosition,
                target: targetPosition,
                motion: motion
            ) ?? coverStatusFallbackPosition(statusWord),
            reportedPositionPercent: currentPosition.map(CoverSpec.snappedPosition)
                ?? targetPosition.map(CoverSpec.snappedPosition),
            positionWritable: s.property(named: "target-position")?.isWritable == true,
            openDescription: coverMotorDescription(in: items, word: .open),
            closeDescription: coverMotorDescription(in: items, word: .close),
            pauseDescription: coverMotorDescription(in: items, word: .pause),
            batteryPercent: battery?.value?.intValue,
            faultText: faultText,
            motion: motion,
            motionIsInferred: motion != .stopped && !statusDefinesTravel
        ))
    }

    /// Some specs use `fault`, and some publish a healthy code of 1 named "No Fault".
    static func faultDescription(code: Int?, description: String?) -> String? {
        guard let code, code != 0 else { return nil }
        if let description, description.lowercased().contains("no fault") { return nil }
        return description ?? "\(code)"
    }

    /// Reads one position property as a 0…100 percentage.
    static func coverPosition(_ ref: MiotReader.Ref?) -> Int? {
        guard let ref, let raw = ref.value?.doubleValue else { return nil }
        return MiotPercentScale.percent(fromRaw: raw, property: ref.property, curve: .proportional)
    }

    /// The target is the card's position while the motor travels, so intermediate current-position
    /// reports cannot pull the card back. At rest the real current position wins; a target-only
    /// spec shows its target because no current reading exists.
    static func coverDisplayPosition(
        current: Int?,
        target: Int?,
        motion: CoverMotion
    ) -> Int? {
        let snappedCurrent = current.map(CoverSpec.snappedPosition)
        let snappedTarget = target.map(CoverSpec.snappedPosition)
        let resting = snappedCurrent ?? snappedTarget
        switch motion {
        case .stopped:
            return resting
        case .opening, .closing:
            // A target read-back can lag the status word. Progress toward the commanded end is the
            // furthest of the two positions, so a stale sample never pulls the card backwards.
            guard let current = snappedCurrent, let target = snappedTarget else { return resting }
            return motion == .opening ? max(current, target) : min(current, target)
        }
    }

    /// A status value that names travel wins. When the device publishes a status field whose list
    /// has no travel words, the target/current difference is the only motion signal. The direction
    /// is recomputed from every snapshot, so a changed position cannot leave a stale direction.
    static func coverStatusDefinesTravel(_ status: MiotReader.Ref?) -> Bool {
        status?.property.valueList?.contains { item in
            guard let itemWord = CoverStatusWord(item.description) else { return false }
            return itemWord == .opening || itemWord == .closing
        } ?? false
    }

    /// A dedicated travel word (`Opening`, `Rising`, `Closing`, `Falling`) is authoritative.
    /// Bare `Open`/`Close` are terminal end words in the released library, so they only become
    /// travel when the status list has no dedicated travel word and the position still differs
    /// from the target. Explicit rest words (`Stop`, `Pause`, `Idle`) never travel.
    static func coverMotion(
        statusDefinesTravel: Bool,
        word: CoverStatusWord?,
        current: Int?,
        target: Int?
    ) -> CoverMotion {
        if let word {
            switch word {
            case .opening, .closing:
                return word.motion
            case .stopped:
                return .stopped
            case .open, .closed:
                return statusDefinesTravel ? .stopped : inferredCoverMotion(current: current, target: target)
            }
        }
        if statusDefinesTravel { return .stopped }
        return inferredCoverMotion(current: current, target: target)
    }

    /// The only motion signal for a spec without travel words. Snap before comparing, or a parked
    /// 99 against a target of 100 would read as travel forever (#298).
    static func inferredCoverMotion(current: Int?, target: Int?) -> CoverMotion {
        let current = current.map(CoverSpec.snappedPosition)
        let target = target.map(CoverSpec.snappedPosition)
        guard let current, let target, current != target else { return .stopped }
        return target > current ? .opening : .closing
    }

    static func coverStatusFallbackPosition(_ word: CoverStatusWord?) -> Int? {
        switch word {
        case .open: 100
        case .closed: 0
        default: nil
        }
    }

    enum CoverMotorWord { case open, close, pause }

    /// Exact value-list words only. A substring match could turn "Not Open" into an open command.
    /// Airers and roller blinds say `Up` / `Down`: raising is opening, lowering is closing (#299).
    static func coverMotorDescription(in items: [MiotValueListItem], word: CoverMotorWord) -> String? {
        let aliases: Set<String> = switch word {
        case .open: ["open", "打开", "up", "raise"]
        case .close: ["close", "关闭", "down", "lower"]
        case .pause: ["pause", "stop", "暂停"]
        }
        return items.first { item in
            aliases.contains(item.description.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
        }?.description
    }


    static func buildCamera(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let on = r.ref("on", in: s)
        let nightShot = r.ref("night-shot", in: s)
        let motionService = r.spec.services(named: "motion-detection").first
        let motion = motionService.flatMap { r.ref("motion-detection", in: $0) }
        let streamService = r.spec.services(named: "camera-stream-for-google-home")
            .first { $0.action(named: "start-hls-stream") != nil }

        return .camera(CameraSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: on?.value?.miotBool ?? false,
            nightShot: nightShot?.valueDescription,
            availableNightShots: nightShot?.property.isWritable == true ? (nightShot?.property.valueList?.map(\.description) ?? []) : [],
            motionDetectionEntityId: motion?.property.isWritable == true ? motion.map { MiotAddress.make(did: r.did, siid: $0.service.iid) } : nil,
            isMotionDetectionOn: motion?.value?.miotBool,
            streamEntityId: streamService.map { MiotAddress.make(did: r.did, siid: $0.iid) },
            powerWritable: on?.property.isWritable == true
        ))
    }

    static func buildFridge(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let mode = r.ref("mode", in: s)
        let chambers = r.spec.services(named: "fridge-chamber")
        let fridgeChamber = chambers.first(where: { ($0.property(named: "target-temperature")?.rangeMin ?? 0) >= 0 }) ?? chambers.first
        let freezerChamber = chambers.first(where: { ($0.property(named: "target-temperature")?.rangeMin ?? 0) < 0 }) ?? (chambers.count > 1 ? chambers[1] : nil)

        let fridgeTemp = fridgeChamber.flatMap { r.ref("temperature", in: $0) }
        let fridgeTarget = fridgeChamber.flatMap { r.ref("target-temperature", in: $0) }
        let freezerTemp = freezerChamber.flatMap { r.ref("temperature", in: $0) }
        let freezerTarget = freezerChamber.flatMap { r.ref("target-temperature", in: $0) }

        return .fridge(FridgeSpec(
            modeEntityId: mode?.property.isWritable == true ? MiotAddress.make(did: r.did, siid: s.iid) : nil,
            currentMode: mode?.valueDescription ?? "None",
            availableModes: mode?.property.isWritable == true ? (mode?.property.valueList?.map(\.description) ?? []) : [],
            fridgeCurrentTemp: fridgeTemp?.value?.doubleValue,
            fridgeTargetTemp: fridgeTarget?.value?.doubleValue ?? 4,
            fridgeTargetEntityId: fridgeTarget?.property.isWritable == true ? fridgeChamber.map { MiotAddress.make(did: r.did, siid: $0.iid) } : nil,
            freezerCurrentTemp: freezerTemp?.value?.doubleValue,
            freezerTargetTemp: freezerTarget?.value?.doubleValue ?? -18,
            freezerTargetEntityId: freezerTarget?.property.isWritable == true ? freezerChamber.map { MiotAddress.make(did: r.did, siid: $0.iid) } : nil
        ))
    }

    static func buildKitchenAppliance(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        if s.properties.contains(where: { $0.shortName == "on" && $0.isWritable }) {
            return buildPlug(r, s)
        }
        let status = r.ref("status", in: s)
        let hasActionControl = s.actions.contains {
            ["start-cook", "cancel-cooking", "pause"].contains($0.shortName) && $0.inputProperties.isEmpty
        }
        if hasActionControl {
            let desc = (status?.valueDescription ?? "").lowercased()
            let isActive = desc.contains("running") || desc.contains("cooking") || desc.contains("heating")
                || desc.contains("boiling") || desc.contains("preheat") || desc.contains("keep warm")
                || desc.contains("warming") || desc.contains("busy")
            return .plug(PlugSpec(
                switchEntityId: MiotAddress.make(did: r.did, siid: s.iid),
                isOn: isActive
            ))
        }
        let temp = r.ref("temperature", in: s) ?? r.ref("target-temperature", in: s)
        let leftTime = r.ref("left-time", in: s)
        _ = r.ref("ble-keep-warm-time", in: s)

        var parts: [String] = []
        if let desc = status?.valueDescription {
            parts.append(genericApplianceStatusText(forDescription: desc))
        }
        if let t = temp?.value?.doubleValue, t.isFinite {
            parts.append("\(Int(t.rounded()))°C")
        }
        if let left = leftTime?.value?.intValue, left > 0 {
            parts.append(L10n.tr("剩余 \(left) 分", "\(left) min left"))
        }
        let fallbackText: String = switch s.shortName {
        case "kettle", "ble-kettle", "water-dispenser":
            L10n.tr("保温监控", "Water Monitor")
        default:
            L10n.tr("待机", "Standby")
        }
        return .sensor(SensorSpec(
            temperature: nil,
            humidity: nil,
            battery: nil,
            statusText: parts.isEmpty ? fallbackText : parts.joined(separator: " · ")
        ))
    }

    /// Services that make up an appliance card, in read order. A spec may publish a richer
    /// companion service (a gas boiler's switch, a cleanfly dryer's remaining time) beside the one
    /// its device type names. Widening this list can only fill a gap: the primary service is always
    /// read first and duplicate services are skipped. The command mapper also uses it to accept a
    /// no-argument start/pause only on a service this card builds (see
    /// `WaterHeaterDeviceTests.ignoresUnrelatedActions`).
    static let applianceServiceNames = [
        "water-heater", "boiler", "washer", "dryer", "clothes-dryer", "cleanfly-dryer", "dishwasher",
    ]

    /// The shared appliance card (#300). Power, mode, target temperature, status, fault, remaining
    /// time and start/pause actions are each exposed only when the spec publishes them. A device
    /// type listed in `deviceProfiles.json`'s `appliances` table merges its primary service with
    /// every companion service, so a bath heater or hood keeps its light and fan speed (#4).
    static func buildApplianceCard(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let profile = DeviceProfiles.current.appliances[r.spec.shortName]
        let services = applianceServices(primary: s, profile: profile, spec: r.spec)
        // A `light` companion contributes its own switch only; its `on`/`mode` must never be
        // mistaken for the appliance's main function.
        let lightService = services.first { $0.shortName == "light" && $0.property(named: "on") != nil }
        let controls = services.filter { $0.iid != lightService?.iid }
        // The main function's own service owns power whenever it publishes it. A companion can
        // carry the only writable copy while the primary publishes the same name read-only
        // (#300), so writable companions still come before readable fallbacks. This ordering
        // keeps a bath heater's `heating` from being replaced by a companion fan switch (#4).
        let companionControls = controls.filter { $0.iid != s.iid }
        let power = firstWritablePower(in: [s], reader: r)
            ?? firstWritablePower(in: companionControls, reader: r)
            ?? r.first("on", in: [s]) ?? r.first("heating", in: [s])
            ?? r.first("on", in: companionControls) ?? r.first("heating", in: companionControls)
        let mode = r.first("mode", in: controls, where: { $0.isWritable && $0.valueList?.isEmpty == false })
            ?? r.first("mode", in: controls, where: { $0.valueList?.isEmpty == false })
        let target = r.first("target-temperature", in: controls, where: { $0.isWritable })
            ?? r.first("target-temperature", in: controls)
        let readingServices = services + r.spec.services(named: "environment")
            + r.spec.services(named: "temperature-humidity-sensor")
        let current = r.first("temperature", in: readingServices)
        let humidity = r.first("relative-humidity", in: readingServices)
            ?? r.first("humidity", in: readingServices)
        let status = r.first("status", in: controls)
        let runStatus = r.first("run-status", in: controls)
        let fault = r.first("fault", in: controls) ?? r.first("device-fault", in: controls)
        let remaining = r.first("left-time", in: controls)
        let faultCode = fault?.value?.intValue
        let lightOn = lightService.flatMap { r.ref("on", in: $0) }
        let fan = r.first("fan-level", in: controls, where: { $0.isWritable && $0.valueList?.isEmpty == false })
            ?? r.first("fan-level", in: controls, where: { $0.valueList?.isEmpty == false })

        return .appliance(ApplianceSpec(
            entityId: MiotAddress.make(did: r.did, siid: s.iid),
            isOn: power?.value?.miotBool ?? false,
            powerPresent: power != nil,
            powerWritable: power?.property.isWritable == true,
            currentMode: mode?.valueDescription,
            availableModes: mode?.property.valueList?.map(\.description) ?? [],
            modeWritable: mode?.property.isWritable == true,
            targetTemperature: target.flatMap { $0.value?.doubleValue },
            currentTemperature: current?.value?.doubleValue,
            minTemp: target?.property.rangeMin ?? 0,
            maxTemp: target?.property.rangeMax ?? 100,
            step: target?.property.rangeStep ?? 1,
            temperatureWritable: target?.property.isWritable == true,
            statusText: applianceStatusText(
                service: s.shortName,
                status: status?.valueDescription,
                runStatus: runStatus?.valueDescription
            ),
            faultText: faultDescription(
                code: faultCode,
                description: faultCode.flatMap { fault?.property.description(forValue: $0) }
            ),
            remainingMinutes: remainingMinutes(for: remaining),
            startAction: applianceAction(.start, services: controls, reader: r),
            pauseAction: applianceAction(.pause, services: controls, reader: r),
            powerEntityId: power.map { MiotAddress.make(did: r.did, siid: $0.service.iid) },
            modeEntityId: mode.map { MiotAddress.make(did: r.did, siid: $0.service.iid) },
            temperatureEntityId: target.map { MiotAddress.make(did: r.did, siid: $0.service.iid) },
            light: lightOn.map {
                ApplianceLight(
                    entityId: MiotAddress.make(did: r.did, siid: $0.service.iid),
                    isOn: $0.value?.miotBool ?? false,
                    writable: $0.property.isWritable
                )
            },
            fanLevel: fan.map {
                ApplianceFanLevel(
                    entityId: MiotAddress.make(did: r.did, siid: $0.service.iid),
                    current: $0.valueDescription,
                    options: $0.property.valueList?.map(\.description) ?? [],
                    writable: $0.property.isWritable
                )
            },
            currentHumidity: humidity?.value?.doubleValue,
            symbolName: profile?.symbol,
            roomReading: profile?.roomReading == true,
            heats: profile?.heats ?? (r.spec.shortName == "water-heater"),
            stopAction: applianceAction(.stop, services: controls, reader: r)
        ))
    }

    static func firstWritablePower(in services: [MiotService], reader r: MiotReader) -> MiotReader.Ref? {
        r.first("on", in: services, where: { $0.isWritable })
            ?? r.first("heating", in: services, where: { $0.isWritable })
    }

    /// The primary service followed by the profile's companion services, in profile order.
    static func applianceServices(
        primary: MiotService,
        profile: DeviceProfiles.ApplianceProfile?,
        spec: MiotDeviceSpec
    ) -> [MiotService] {
        let companions = profile?.companions ?? applianceServiceNames
        var services = [primary]
        for name in companions {
            services.append(contentsOf: spec.services(named: name))
        }
        var seen: Set<Int> = []
        return services.filter { seen.insert($0.iid).inserted }
    }

    /// `left-time` is minutes in most specs; a few released models publish seconds (#3 review).
    static func remainingMinutes(for ref: MiotReader.Ref?) -> Int? {
        guard let ref, let raw = ref.value?.doubleValue, raw.isFinite, raw > 0 else { return nil }
        let minutes: Double
        switch ref.property.unit?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "seconds", "second", "sec", "s": minutes = (raw / 60).rounded(.up)
        case "hours", "hour", "h": minutes = raw * 60
        default: minutes = raw.rounded()
        }
        guard let value = Int(exactly: minutes), value > 0 else { return nil }
        return value
    }

    /// Families whose `status` and `run-status` words belong to the shared activity vocabulary.
    /// Every other family keeps its own reading: the water heater and a bath heater or hood use
    /// `applianceStatusText(forDescription:)` (#300, #4), so a hood's `Closed` or `Clean` is never
    /// reinterpreted as a wash phase (#3).
    static let activityServices: Set<String> = [
        "washer", "dryer", "clothes-dryer", "cleanfly-dryer", "dishwasher",
    ]

    static func applianceStatusText(service: String, status: String?, runStatus: String?) -> String? {
        if activityServices.contains(service) {
            return applianceActivityText(status: status, runStatus: runStatus)
        }
        return status.map(applianceStatusText(forDescription:))
    }

    /// One activity line. The coarse `status` owns a terminal word, because a washer keeps naming
    /// the phase it was paused in; while the machine works, a `run-status` phase is more specific
    /// than `status` and wins (#3). A word this vocabulary does not know is shown as the spec
    /// wrote it, so an unmapped status never blanks the line.
    static func applianceActivityText(status: String?, runStatus: String?) -> String? {
        let coarse = ApplianceActivity(status)
        if let coarse, coarse.isTerminal { return coarse.localizedLabel }
        if let stage = ApplianceActivity(runStatus), stage.isStage { return stage.localizedLabel }
        if let coarse { return coarse.localizedLabel }
        if let stage = ApplianceActivity(runStatus) { return stage.localizedLabel }
        return status ?? runStatus
    }

    static func applianceAction(
        _ kind: ApplianceActionKind,
        services: [MiotService],
        reader r: MiotReader
    ) -> ApplianceAction? {
        var seen: Set<Int> = []
        for service in services where seen.insert(service.iid).inserted {
            for action in service.actions
            where action.inputProperties.isEmpty && kind.aliases.contains(action.shortName.lowercased()) {
                return ApplianceAction(
                    kind: kind,
                    entityId: MiotAddress.makeAction(did: r.did, siid: service.iid, aiid: action.iid)
                )
            }
        }
        return nil
    }

    static func buildSensor(_ r: MiotReader, _ s: MiotService) -> DeviceKind {
        let readingServices = [s] + r.spec.services(named: "environment") + r.spec.services(named: "temperature-humidity-sensor")
        let temp = r.first("temperature", in: readingServices)
        let hum = r.first("relative-humidity", in: readingServices)
        let battery = r.first("battery-level", in: r.spec.services(named: "battery") + [s])

        let observations = safetyObservations(r, s)
        var statusText: String?
        if temp == nil && hum == nil {
            switch s.shortName {
            case "lock":
                statusText = lockStatusText(r, s)
            case "motion-sensor", "occupancy-sensor":
                let motion = r.ref("motion-state", in: s) ?? r.ref("occupancy-status", in: s)
                let lux = r.first("illumination", in: [s] + r.spec.services(named: "illumination-sensor"))
                var parts: [String] = []
                if let moving = motion?.value?.miotBool {
                    parts.append(moving ? L10n.tr("有人移动", "Motion") : L10n.tr("无人移动", "Clear"))
                } else {
                    parts.append(L10n.tr("移动监测", "Motion Sensor"))
                }
                if let lx = lux?.value?.doubleValue, lx.isFinite {
                    parts.append("\(Int(lx.rounded())) lx")
                }
                statusText = parts.joined(separator: " · ")
            case "magnet-sensor":
                let contact = r.ref("contact-state", in: s)
                if let closed = contact?.value?.miotBool {
                    statusText = closed ? L10n.tr("已闭合", "Closed") : L10n.tr("已打开", "Open")
                } else {
                    statusText = L10n.tr("门窗监测", "Contact Sensor")
                }
            case "submersion-sensor":
                let sub = r.ref("submersion-state", in: s)
                if let wet = sub?.value?.miotBool {
                    statusText = wet ? L10n.tr("浸水告警", "Leak Detected") : L10n.tr("正常", "Dry")
                } else {
                    statusText = L10n.tr("水浸监测", "Leak Sensor")
                }
            case "illumination-sensor":
                let lux = r.ref("illumination", in: s)
                if let lx = lux?.value?.doubleValue, lx.isFinite {
                    statusText = "\(Int(lx.rounded())) lx"
                } else {
                    statusText = L10n.tr("光照监测", "Light Sensor")
                }
            default:
                break
            }
        }

        return .sensor(SensorSpec(
            temperature: temp?.value?.doubleValue,
            humidity: hum?.value?.doubleValue,
            battery: battery?.value?.intValue,
            statusText: statusText,
            observations: observations
        ))
    }

    private static func safetyObservations(_ r: MiotReader, _ s: MiotService) -> [SensorObservation] {
        let sourceId = "\(r.did)#miot:\(s.iid)"
        switch s.shortName {
        case "motion-sensor", "occupancy-sensor":
            let motion = r.ref("motion-state", in: s) ?? r.ref("occupancy-status", in: s)
            guard let moving = motion?.value?.miotBool else { return [] }
            return [SensorObservation(sourceId: sourceId, kind: .motion, alarming: moving)]
        case "magnet-sensor":
            guard let closed = r.ref("contact-state", in: s)?.value?.miotBool else { return [] }
            return [SensorObservation(sourceId: sourceId, kind: .contact, alarming: !closed)]
        case "submersion-sensor":
            guard let wet = r.ref("submersion-state", in: s)?.value?.miotBool else { return [] }
            return [SensorObservation(sourceId: sourceId, kind: .leak, alarming: wet)]
        default:
            return []
        }
    }

    /// Readable `door-state` properties the poll must refresh. A missing one is a failed read.
    static func doorStateKeys(for device: MiDeviceRecord, spec: MiotDeviceSpec) -> [MiotPropKey] {
        let scoped = specForRecord(device, spec: spec)
        var keys: [MiotPropKey] = []
        for service in scoped.services where service.shortName == "lock" || service.shortName == "door" {
            for property in service.properties where property.shortName == "door-state" && property.isReadable {
                let key = MiotPropKey(did: device.miotDid, siid: service.iid, piid: property.iid)
                if !keys.contains(key) { keys.append(key) }
            }
        }
        return keys
    }

    /// `door-state` is the only property treated as the current lock condition.
    /// Event fields (`lock-action`, `abnormal-condition`) and toggles (`child-lock`, `anti-lock`)
    /// are not a fresh locked/unlocked reading. An unreadable or unmapped value stays unknown
    /// instead of claiming the door is guarding (#149).
    static func lockStatusText(_ r: MiotReader, _ s: MiotService) -> String {
        let doors = [s] + r.spec.services(named: "door")
        if let door = r.first("door-state", in: doors),
           let description = door.valueDescription,
           let text = interpretedLockStatus(description) {
            return text
        }
        return L10n.tr("锁状态未知", "Lock status unknown")
    }

    static func interpretedLockStatus(_ description: String) -> String? {
        let lower = description.lowercased()
        if lower.contains("child") || lower.contains("童锁") || lower.contains("反锁")
            || lower.contains("anti-lock") || lower.contains("antilock") {
            return nil
        }
        if lower.contains("unlock") || lower.contains("已开锁") || lower.contains("未上锁")
            || lower.contains("not locked") || lower.contains("未锁") {
            return L10n.tr("已开锁", "Unlocked")
        }
        if (lower.contains("ajar") || lower.contains("虚掩")),
           !lower.contains("解除"), !lower.contains("clear"), !lower.contains("release") {
            return L10n.tr("门虚掩", "Ajar")
        }
        if lower.contains("not closed") || lower.contains("not close") || lower.contains("未关") || lower.contains("没关") {
            return L10n.tr("门未关", "Not Closed")
        }
        if lower.contains("jam") || lower.contains("卡住") {
            return L10n.tr("门锁卡住", "Jammed")
        }
        if lower.contains("locked") || lower.contains("已上锁") || lower.contains("已锁") {
            return L10n.tr("已上锁", "Locked")
        }
        return nil
    }

    /// A cached door reading must not keep claiming a security state after the device goes offline.
    static func redactOfflineLockClaim(_ kind: DeviceKind) -> DeviceKind {
        guard case .sensor(var sensor) = kind,
              let text = sensor.statusText,
              affirmativeLockStatuses.contains(text) else { return kind }
        sensor.statusText = L10n.tr("锁状态未知", "Lock status unknown")
        return .sensor(sensor)
    }

    private static var affirmativeLockStatuses: Set<String> {
        [
            L10n.tr("已上锁", "Locked"),
            L10n.tr("已开锁", "Unlocked"),
            L10n.tr("门虚掩", "Ajar"),
            L10n.tr("门未关", "Not Closed"),
            L10n.tr("门锁卡住", "Jammed"),
            L10n.tr("正在上锁", "Locking"),
            L10n.tr("正在开锁", "Unlocking"),
            L10n.tr("守护中", "Guarding"),
        ]
    }

    // MARK: Value Translation

    private static func hasScaleToken(_ comment: String, _ token: String) -> Bool {
        var searchRange = comment.startIndex..<comment.endIndex
        while let range = comment.range(of: token, range: searchRange) {
            let prevIndex = range.lowerBound > comment.startIndex ? comment.index(before: range.lowerBound) : nil
            let prevChar = prevIndex.map { comment[$0] }
            let isPartOfOtherNumber = prevChar.map { $0.isNumber || $0 == "." } ?? false
            if !isPartOfOtherNumber {
                return true
            }
            searchRange = range.upperBound..<comment.endIndex
        }
        return false
    }

    /// Some released specs declare watt but document centiwatts in the comment (#129).
    /// Only explicit metadata changes the scale; magnitude and range-step never do.
    static func powerScale(for property: MiotProperty) -> Double? {
        let unit = (property.unit ?? "").lowercased().replacingOccurrences(of: " ", with: "")
        let comment = (property.comment ?? "").lowercased()
            .replacingOccurrences(of: ",", with: ".").replacingOccurrences(of: " ", with: "")
        for (token, scale) in [("0.001", 0.001), ("0.01", 0.01), ("0.1", 0.1)] {
            let pattern = "(?<![0-9.])" + NSRegularExpression.escapedPattern(for: token) + "(?:watt|w)(?![a-z])"
            if comment.range(of: pattern, options: .regularExpression) != nil || unit == token + "w" || unit == token + "watt" {
                return scale
            }
        }
        switch unit {
        case "", "none", "w", "watt": return 1
        case "kw", "kilowatt": return 1000
        case "mw", "milliwatt": return 0.001
        default: return nil
        }
    }

    /// Scale factor that converts a MIoT `power-consumption` reading into kWh (#62).
    /// Returns `nil` when an integer property has neither a recognized `unit` nor an explicit
    /// scale in `comment` (e.g. "0.01kwh"), so ambiguous raw integers are never guessed.
    static func energyScale(for property: MiotProperty) -> Double? {
        let unit = (property.unit ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .replacingOccurrences(of: ",", with: ".")
            .replacingOccurrences(of: " ", with: "")
        let comment = (property.comment ?? "")
            .lowercased()
            .replacingOccurrences(of: ",", with: ".")
            .replacingOccurrences(of: " ", with: "")
        if unit == "wh" || unit == "watt-hour" || unit == "0.001kwh" || hasScaleToken(comment, "0.001kwh") {
            return 0.001
        }
        if unit == "0.01kwh" || hasScaleToken(comment, "0.01kwh") {
            return 0.01
        }
        if unit == "kwh" || unit == "kilowatt-hour" || unit == "1kwh" || hasScaleToken(comment, "1kwh") {
            return 1.0
        }
        if property.format == "float", unit.isEmpty || unit == "none" {
            return 1.0
        }
        return nil
    }

    /// MIoT mode descriptions → the HA hvac vocabulary the UI already localizes.
    static func hvacMode(forDescription description: String) -> String? {
        switch description.lowercased().trimmingCharacters(in: .whitespaces) {
        case "cool", "cooling", "cold": return "cool"
        case "heat", "heating", "warm": return "heat"
        case "dry", "dehumidify", "dehumidification", "dehumidifier": return "dry"
        case "fan", "fan only", "fan-only", "fan_only", "wind", "ventilate": return "fan_only"
        case "auto", "automatic", "smart": return "auto"
        default: return nil
        }
    }

    static func vacuumStatus(forDescription description: String?) -> String {
        guard let d = description?.lowercased() else { return "idle" }
        if d.contains("go charging") || d.contains("returning") || d.contains("go home") || d.contains("back to") || d.contains("recharge") {
            return "returning"
        }
        if d.contains("paus") { return "paused" }
        if d.contains("sweep") || d.contains("clean") || d.contains("mop") || d.contains("working") || d.contains("busy") {
            return "cleaning"
        }
        if d.contains("charg") || d.contains("dock") || d.contains("full") { return "docked" }
        return "idle"
    }

    /// Shared appliance-card statuses: `Close` means standby and `Wash` means bathing, words the
    /// kitchen mapping would reinterpret. `genericApplianceStatusText` handles every other word.
    static func applianceStatusText(forDescription description: String) -> String {
        switch description.lowercased().trimmingCharacters(in: .whitespacesAndNewlines) {
        case "close", "closed", "off", "shutdown":
            return L10n.tr("待机", "Standby")
        case "open", "on":
            return L10n.tr("已开启", "On")
        case "wash", "washing":
            return L10n.tr("洗浴中", "Washing")
        default:
            return genericApplianceStatusText(forDescription: description)
        }
    }

    static func genericApplianceStatusText(forDescription description: String) -> String {
        let lower = description.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        if lower.contains("idle") || lower.contains("standby") || lower.contains("off") || lower.contains("shutdown") {
            return L10n.tr("待机", "Standby")
        }
        if lower.contains("keep warm") || lower.contains("keep-warm") || lower.contains("warm") || lower.contains("insulation") {
            return L10n.tr("保温中", "Keep Warm")
        }
        if lower.contains("cook") || lower.contains("work") || lower.contains("busy") || lower.contains("run") || lower.contains("boil") || lower.contains("heat") {
            return L10n.tr("运行中", "Running")
        }
        if lower.contains("preheat") {
            return L10n.tr("预热中", "Preheating")
        }
        if lower.contains("delayed") || lower.contains("delay-off") || lower.contains("延时") {
            return L10n.tr("延时关机", "Delayed Off")
        }
        if lower.contains("delay") || lower.contains("appoint") || lower.contains("reservation") {
            return L10n.tr("预约中", "Scheduled")
        }
        if lower.contains("clean") || lower.contains("清洗") {
            return L10n.tr("清洗中", "Cleaning")
        }
        if lower.contains("paus") {
            return L10n.tr("已暂停", "Paused")
        }
        if lower.contains("finish") || lower.contains("complete") || lower.contains("cooked") {
            return L10n.tr("已完成", "Completed")
        }
        if lower.contains("error") || lower.contains("fault") {
            return L10n.tr("故障", "Fault")
        }
        return description
    }
}

// MARK: - Recording Property Reader

/// Resolves properties by short name and records every readable (did, siid, piid) it touches,
/// so `requiredProps` is derived from the builders themselves instead of a parallel list.
final class MiotReader {
    struct Ref {
        let service: MiotService
        let property: MiotProperty
        let value: JSONValue?

        var valueDescription: String? {
            value?.intValue.flatMap { property.description(forValue: $0) }
        }
    }

    let did: String
    let spec: MiotDeviceSpec
    private let values: [MiotPropKey: JSONValue]
    private(set) var touched: [MiotPropKey] = []
    private(set) var consumed: Set<MiotPropKey> = []

    init(did: String, spec: MiotDeviceSpec, values: [MiotPropKey: JSONValue]) {
        self.did = did
        self.spec = spec
        self.values = values
    }

    func ref(_ name: String, in service: MiotService, includingWriteOnly: Bool = false) -> Ref? {
        guard includingWriteOnly else { return refs(name, in: service).first }
        return service.properties.first(where: { $0.shortName == name }).map { make(service, $0) }
    }

    func refs(_ name: String, in service: MiotService) -> [Ref] {
        service.properties.filter { $0.shortName == name && $0.isReadable }.map { make(service, $0) }
    }

    /// First readable match across `services` in order (duplicates ignored).
    func first(
        _ name: String,
        in services: [MiotService],
        where predicate: (MiotProperty) -> Bool = { _ in true }
    ) -> Ref? {
        var seen: Set<Int> = []
        for service in services where seen.insert(service.iid).inserted {
            if let p = service.properties.first(where: { $0.shortName == name && $0.isReadable && predicate($0) }) {
                return make(service, p)
            }
        }
        return nil
    }

    private func make(_ service: MiotService, _ property: MiotProperty) -> Ref {
        let key = MiotPropKey(did: did, siid: service.iid, piid: property.iid)
        consumed.insert(key)
        if property.isReadable, !touched.contains(key) { touched.append(key) }
        return Ref(service: service, property: property, value: values[key])
    }
}

extension JSONValue {
    /// MIoT booleans occasionally arrive as 0/1.
    var miotBool: Bool? {
        if let b = boolValue { return b }
        return doubleValue.map { $0 != 0 }
    }
}
