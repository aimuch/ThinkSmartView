import Foundation

// Menu-bar metrics and the derived device lists the panel renders (#48).
extension HomeStore {
    // MARK: - Computed Views of State

    public var activeDeviceCount: Int {
        devices.filter(\.isActiveDevice).count
    }

    public var onlineDeviceCount: Int {
        devices.filter(\.isOnline).count
    }

    public var totalPowerWatts: Double {
        devices.reduce(0.0) { acc, dev in
            guard dev.isOnline, case .plug(let s) = dev.kind, s.isOn, s.countsInTotalPower else { return acc }
            return acc + (s.powerWatts ?? 0)
        }
    }

    public var availableMenuBarOptions: [MenuBarMetricOption] {
        let _ = effectiveLanguage
        var opts: [MenuBarMetricOption] = [
            MenuBarMetricOption(id: "auto:primary", title: L10n.tr("自动（第一个温度传感器）", "Auto (First Temp Sensor)"))
        ]
        let readings = environmentReadings.filter { $0.temperature != nil }
        for env in sortedByRoom(readings, roomName: \.roomName) {
            let place = Self.roomQualifiedLabel(env.label, room: env.roomName)
            opts.append(MenuBarMetricOption(id: "temp:\(env.id)", title: L10n.tr("\(place) · 温度", "\(place) · Temp")))
            if env.humidity != nil {
                opts.append(MenuBarMetricOption(id: "temp_hum:\(env.id)", title: L10n.tr("\(place) · 温湿度", "\(place) · Temp & Humidity")))
            }
        }
        opts.append(MenuBarMetricOption(id: "power:total", title: L10n.tr("全屋实时总功率 (W)", "Total Home Power (W)")))
        // Every metered device on its own, mains breakers included: they stay out of the total
        // above, so this is the only way to show a whole-home meter's reading (#331).
        for dev in sortedByRoom(devices.filter { Self.livePowerWatts($0) != nil }, roomName: \.roomName) {
            let place = Self.roomQualifiedLabel(dev.name, room: dev.roomName)
            opts.append(MenuBarMetricOption(id: "power:\(dev.id)", title: L10n.tr("\(place) · 功率", "\(place) · Power")))
        }
        opts.append(MenuBarMetricOption(id: "active:count", title: L10n.tr("开启设备数量", "Active Device Count")))
        opts.append(MenuBarMetricOption(id: "icon:only", title: L10n.tr("仅显示图标", "Icon Only")))
        return opts
    }

    /// `房间 · 设备`, or just the room when the device carries no name of its own (a cleared
    /// sensor name already falls back to the room).
    static func roomQualifiedLabel(_ label: String, room: String) -> String {
        let label = label.trimmingCharacters(in: .whitespaces)
        let room = room.trimmingCharacters(in: .whitespaces)
        if room.isEmpty { return label }
        if label.isEmpty || label == room { return room }
        return "\(room) · \(label)"
    }

    /// The live reading of an online plug or meter; nil when it is offline or reports no power.
    /// A switched-off plug draws nothing, matching how `totalPowerWatts` ignores a stale reading.
    static func livePowerWatts(_ device: SmartDevice) -> Double? {
        guard device.isOnline, case .plug(let spec) = device.kind, let watts = spec.powerWatts else { return nil }
        return spec.isOn ? watts : 0
    }

    /// Stable sort in panel room order; rooms the store does not know go last.
    private func sortedByRoom<T>(_ items: [T], roomName: (T) -> String) -> [T] {
        let ordered = rooms.enumerated()
            .sorted { ($0.element.sortOrder, $0.offset) < ($1.element.sortOrder, $1.offset) }
        var rank: [String: Int] = [:]
        for (index, entry) in ordered.enumerated() where rank[entry.element.name] == nil {
            rank[entry.element.name] = index
        }
        return items.enumerated()
            .sorted { (rank[roomName($0.element)] ?? .max, $0.offset) < (rank[roomName($1.element)] ?? .max, $1.offset) }
            .map(\.element)
    }

    /// The environment reading selected for `menuBarTargetId` (`temp:<id>` / `temp_hum:<id>`),
    /// falling back to the first reading with a temperature, or the first reading overall (#103).
    public var preferredEnvironmentReading: EnvironmentReading? {
        for prefix in ["temp:", "temp_hum:"] where menuBarTargetId.hasPrefix(prefix) {
            let targetId = String(menuBarTargetId.dropFirst(prefix.count))
            if let match = environmentReadings.first(where: { $0.id == targetId && $0.temperature != nil }) {
                return match
            }
        }
        return environmentReadings.first(where: { $0.temperature != nil }) ?? environmentReadings.first
    }

    public var hiddenDevices: [SmartDevice] {
        devices.filter { hiddenDeviceIds.contains($0.id) }
    }

    private func passesVisibility(_ dev: SmartDevice) -> Bool {
        if hiddenDeviceIds.contains(dev.id) { return false }
        if hideOfflineDevices && !dev.isOnline { return false }
        return true
    }

    public var filteredDevices: [SmartDevice] {
        devices.filter { passesVisibility($0) && listCut.matches($0) }
    }

    /// Member device ids hidden because their own light-group card is currently on screen and the
    /// user asked to fold grouped lights (#189). `filteredDevices` keeps the full set so all-off,
    /// scenes, search and assistant targeting still see every member.
    public var foldedDeviceIds: Set<String> {
        guard hideGroupedLights, !lightGroupOwners.isEmpty else { return [] }
        let listed = Dictionary(filteredDevices.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var folded: Set<String> = []
        for device in filteredDevices {
            // An offline group card cannot drive its members, so never fold them behind it.
            guard let groupDid = lightGroupOwners[device.id],
                  let group = listed[groupDid],
                  group.isOnline else { continue }
            folded.insert(device.id)
        }
        return folded
    }

    /// The panel list: `filteredDevices` with member lights folded into their visible group card.
    public var visibleDevices: [SmartDevice] {
        let folded = foldedDeviceIds
        guard !folded.isEmpty else { return filteredDevices }
        return filteredDevices.filter { !folded.contains($0.id) }
    }

    public var kindChips: [DeviceKindTag] {
        var tags = Set(devices.filter(passesVisibility).map { $0.kind.tag })
        if case .kind(let selected) = listCut { tags.insert(selected) }
        return tags.sorted { $0.rank < $1.rank }
    }

    public var favoriteDevices: [SmartDevice] {
        let favs = visibleDevices.filter { favoriteDeviceIds.contains($0.id) }
        guard !favoriteOrder.isEmpty else { return favs }
        let orderIndex = Dictionary(uniqueKeysWithValues: favoriteOrder.enumerated().map { ($1, $0) })
        return favs.sorted { a, b in
            let ia = orderIndex[a.id] ?? Int.max
            let ib = orderIndex[b.id] ?? Int.max
            if ia != ib { return ia < ib }
            return false
        }
    }

    public var nonFavoriteDevices: [SmartDevice] {
        visibleDevices.filter { !favoriteDeviceIds.contains($0.id) }
    }

    public var orderedVisibleDevices: [SmartDevice] {
        if selectedRoomId == nil {
            return favoriteDevices + nonFavoriteDevices
        }
        return visibleDevices
    }

    public var menuBarSymbolName: String {
        if miSessionBlocksControl { return "house.slash" }
        return activeDeviceCount > 0 ? "house.fill" : "house"
    }

    public var menuBarText: String? {
        if deviceValuesAreStale {
            return L10n.tr("可能过期", "Possibly stale")
        }
        let sensorReading = { (prefix: String) -> EnvironmentReading? in
            guard self.menuBarTargetId.hasPrefix(prefix) else { return nil }
            let sensorId = String(self.menuBarTargetId.dropFirst(prefix.count))
            return self.environmentReadings.first(where: { $0.id == sensorId && $0.temperature != nil })
                ?? self.environmentReadings.first(where: { $0.temperature != nil })
                ?? self.environmentReadings.first
        }

        if let env = sensorReading("temp:"), let t = env.temperature {
            return L10n.temperature(t, fractionDigits: 1)
        }
        if let env = sensorReading("temp_hum:"), let t = env.temperature {
            if let h = env.humidity {
                return "\(L10n.temperature(t, fractionDigits: 1)) \(String(format: "%.0f%%", h))"
            }
            return L10n.temperature(t, fractionDigits: 1)
        }
        if menuBarTargetId == "power:total" {
            return String(format: "%.0fW", totalPowerWatts)
        }
        if menuBarTargetId.hasPrefix("power:") {
            // An offline or removed device falls through to the default display, never 0W (#331).
            let deviceId = String(menuBarTargetId.dropFirst("power:".count))
            if let watts = devices.first(where: { $0.id == deviceId }).flatMap(Self.livePowerWatts) {
                return String(format: "%.0fW", watts)
            }
        }
        if menuBarTargetId == "active:count" {
            let _ = effectiveLanguage
            return activeDeviceCount > 0 ? L10n.tr("\(activeDeviceCount)开", "\(activeDeviceCount) On") : nil
        }
        if menuBarTargetId == "icon:only" {
            return nil
        }

        switch menuBarDisplayMode {
        case .iconOnly:
            return nil
        case .sensorTemperature:
            // No room names: the first reading that actually reports a temperature (#36).
            if let sensor = environmentReadings.first(where: { $0.temperature != nil }),
               let t = sensor.temperature {
                return L10n.temperature(t, fractionDigits: 1)
            }
            return nil
        case .activeCount:
            let _ = effectiveLanguage
            return activeDeviceCount > 0 ? L10n.tr("\(activeDeviceCount)开", "\(activeDeviceCount) On") : nil
        }
    }
}
