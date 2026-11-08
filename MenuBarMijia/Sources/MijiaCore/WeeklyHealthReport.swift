import Foundation

/// #182: a short weekly home health report, computed only from data the app already keeps locally
/// (plug energy history, sensor history, live filter and battery levels, online state).
public struct WeeklyHealthReport: Equatable, Sendable {
    public enum Language: Sendable {
        case chinese, english

        public static var current: Language { L10n.isEnglish ? .english : .chinese }
    }

    public struct Energy: Equatable, Sendable {
        public let thisWeekKwh: Double
        /// `nil` when no day of last week was measured, so no baseline is invented.
        public let lastWeekKwh: Double?
        public let topConsumers: [EnergyDeviceTotal]

        public var changePercent: Double? {
            guard let lastWeekKwh, lastWeekKwh > 0 else { return nil }
            return (thisWeekKwh - lastWeekKwh) / lastWeekKwh * 100
        }
    }

    /// A sensor's weekly temperature and humidity range.
    public struct ClimateRange: Equatable, Sendable {
        public let temperature: SensorMetricSummary?
        public let humidity: SensorMetricSummary?

        public init(temperature: SensorMetricSummary?, humidity: SensorMetricSummary?) {
            self.temperature = temperature
            self.humidity = humidity
        }
    }

    public enum ClimateWarning: Equatable, Sendable {
        case tooHumid(Double), tooDry(Double), tooHot(Double), tooCold(Double)
    }

    public struct RoomClimate: Equatable, Sendable {
        public let deviceName: String
        public let roomName: String
        public let range: ClimateRange
        public let warnings: [ClimateWarning]
    }

    public enum MaintenanceItem: Equatable, Sendable {
        case filter(Int), battery(Int)

        var percent: Int {
            switch self {
            case .filter(let p), .battery(let p): p
            }
        }
    }

    public struct Maintenance: Equatable, Sendable {
        public let deviceName: String
        public let roomName: String
        public let item: MaintenanceItem
    }

    public struct OfflineDevice: Equatable, Sendable {
        public let deviceName: String
        public let roomName: String
        /// Days this week the device was seen offline; `nil` when there is no history for it.
        public let daysOffline: Int?
        /// Offline when the report was built, as opposed to back online after dropping out.
        public let isOfflineNow: Bool

        public init(deviceName: String, roomName: String, daysOffline: Int? = nil, isOfflineNow: Bool = true) {
            self.deviceName = deviceName
            self.roomName = roomName
            self.daysOffline = daysOffline
            self.isOfflineNow = isOfflineNow
        }
    }

    /// A plug still drawing noticeable power while switched off.
    public struct StandbyConsumer: Equatable, Sendable {
        public let deviceId: String
        public let deviceName: String
        public let roomName: String
        public let powerWatts: Double

        public init(deviceId: String, deviceName: String, roomName: String, powerWatts: Double) {
            self.deviceId = deviceId
            self.deviceName = deviceName
            self.roomName = roomName
            self.powerWatts = powerWatts
        }
    }

    public let energy: Energy?
    public let climate: [RoomClimate]
    public let maintenance: [Maintenance]
    public let offlineDevices: [OfflineDevice]
    /// Plugs that keep drawing power while off, highest draw first.
    public let standbyConsumers: [StandbyConsumer]
    /// Rooms where several devices are offline or kept dropping out, so one bad link explains them all.
    public let unreliableRooms: [String]

    /// Comfort band; a weekly extreme outside it becomes a warning.
    static let humidityBand = 30.0...70.0
    static let temperatureBand = 16.0...30.0
    /// Batteries and filters at or below these levels need replacing soon.
    static let batteryThreshold = 20
    static let filterThreshold = 10
    static let topConsumerCount = 3
    /// A device back online still counts as unreliable after dropping out on this many days.
    static let frequentOfflineDays = 2
    /// A plug drawing at least this many watts while switched off is a standby consumer.
    static let standbyWattsThreshold = 1.0
    /// This many devices offline or dropped out on this many days points at the room's Wi-Fi/gateway.
    static let unreliableRoomDeviceCount = 2
    static let unreliableRoomOfflineDays = 3

    /// - Parameters:
    ///   - dailyEnergy: 14 daily totals ending today, oldest first (`EnergyHistory.dailyTotals(dayCount: 14)`).
    ///   - energyByDevice: this week's per-device totals, highest first (`EnergyHistory.deviceTotals(dayCount: 7)`).
    ///   - climate: weekly sensor ranges keyed by device id.
    ///   - offlineDays: days this week each device was seen offline (`OfflineHistory.offlineDays`).
    public static func build(
        devices: [SmartDevice],
        dailyEnergy: [EnergyDayTotal],
        energyByDevice: [EnergyDeviceTotal],
        climate: [String: ClimateRange],
        offlineDays: [String: Int] = [:],
        now: Date = Date()
    ) -> WeeklyHealthReport {
        WeeklyHealthReport(
            energy: energySection(dailyEnergy: dailyEnergy, energyByDevice: energyByDevice),
            climate: devices.compactMap { dev in
                guard let range = climate[dev.id], range.temperature != nil || range.humidity != nil else { return nil }
                return RoomClimate(deviceName: dev.name, roomName: dev.roomName, range: range, warnings: warnings(for: range))
            },
            maintenance: devices.flatMap(maintenanceItems).enumerated()
                .sorted { ($0.element.item.percent, $0.offset) < ($1.element.item.percent, $1.offset) }
                .map(\.element),
            offlineDevices: devices.compactMap { dev in
                let days = offlineDays[dev.id]
                guard !dev.isOnline || (days ?? 0) >= frequentOfflineDays else { return nil }
                return OfflineDevice(deviceName: dev.name, roomName: dev.roomName, daysOffline: days, isOfflineNow: !dev.isOnline)
            },
            standbyConsumers: devices.compactMap { dev in
                guard dev.isOnline, case .plug(let plug) = dev.kind, !plug.isOn,
                      let watts = plug.powerWatts, watts >= standbyWattsThreshold else { return nil }
                return StandbyConsumer(deviceId: dev.id, deviceName: dev.name, roomName: dev.roomName, powerWatts: watts)
            }.sorted {
                $0.powerWatts != $1.powerWatts
                    ? $0.powerWatts > $1.powerWatts
                    : ($0.roomName, $0.deviceName) < ($1.roomName, $1.deviceName)
            },
            unreliableRooms: unreliableRooms(devices: devices, offlineDays: offlineDays)
        )
    }

    /// A room whose devices share one link: two or more of them offline or repeatedly dropping out
    /// points at that room's Wi-Fi or gateway rather than at the devices themselves.
    private static func unreliableRooms(devices: [SmartDevice], offlineDays: [String: Int]) -> [String] {
        var counts: [String: Int] = [:]
        for dev in devices {
            guard !dev.roomName.isEmpty,
                  !dev.isOnline || (offlineDays[dev.id] ?? 0) >= unreliableRoomOfflineDays else { continue }
            counts[dev.roomName, default: 0] += 1
        }
        return counts.filter { $0.value >= unreliableRoomDeviceCount }.keys.sorted()
    }

    private static func energySection(dailyEnergy: [EnergyDayTotal], energyByDevice: [EnergyDeviceTotal]) -> Energy? {
        let thisWeek = dailyEnergy.suffix(7)
        let lastWeek = dailyEnergy.dropLast(7).suffix(7)
        guard thisWeek.contains(where: \.isObserved) else { return nil }
        return Energy(
            thisWeekKwh: thisWeek.reduce(0) { $0 + $1.kwh },
            lastWeekKwh: lastWeek.contains(where: \.isObserved) ? lastWeek.reduce(0) { $0 + $1.kwh } : nil,
            topConsumers: Array(energyByDevice.prefix(topConsumerCount))
        )
    }

    private static func warnings(for range: ClimateRange) -> [ClimateWarning] {
        var result: [ClimateWarning] = []
        if let h = range.humidity {
            if h.max > humidityBand.upperBound { result.append(.tooHumid(h.max)) }
            if h.min < humidityBand.lowerBound { result.append(.tooDry(h.min)) }
        }
        if let t = range.temperature {
            if t.max > temperatureBand.upperBound { result.append(.tooHot(t.max)) }
            if t.min < temperatureBand.lowerBound { result.append(.tooCold(t.min)) }
        }
        return result
    }

    /// Offline devices report stale levels, so only online ones count.
    private static func maintenanceItems(_ dev: SmartDevice) -> [Maintenance] {
        guard dev.isOnline else { return [] }
        let item: MaintenanceItem? = switch dev.kind {
        case .fan(let s): s.filterLifePercent.flatMap { $0 <= filterThreshold ? .filter($0) : nil }
        case .sensor(let s): s.battery.flatMap { $0 > 0 && $0 <= batteryThreshold ? .battery($0) : nil }
        case .cover(let s): s.batteryPercent.flatMap { $0 > 0 && $0 <= batteryThreshold ? .battery($0) : nil }
        case .vacuum(let s): s.battery.flatMap { (0...batteryThreshold).contains($0) ? .battery($0) : nil }
        default: nil
        }
        return item.map { [Maintenance(deviceName: dev.name, roomName: dev.roomName, item: $0)] } ?? []
    }
}

// MARK: - Rendering

extension WeeklyHealthReport {
    /// One line for the weekly system notification.
    public func notificationSummary(language: Language = .current) -> String {
        let zh = language == .chinese
        var parts: [String] = []
        if let energy {
            let kwh = Self.kwh(energy.thisWeekKwh)
            let change = energy.changePercent.map { zh ? "（比上周 \(Self.signed($0))）" : " (\(Self.signed($0)) vs last week)" } ?? ""
            parts.append(zh ? "本周用电 \(kwh)\(change)" : "Energy \(kwh)\(change)")
        }
        let warnings = climate.filter { !$0.warnings.isEmpty }.count
        if warnings > 0 { parts.append(zh ? "\(warnings) 处温湿度异常" : "\(warnings) climate alerts") }
        if !maintenance.isEmpty { parts.append(zh ? "\(maintenance.count) 台待更换" : "\(maintenance.count) need replacing") }
        if !offlineDevices.isEmpty { parts.append(zh ? "\(offlineDevices.count) 台离线" : "\(offlineDevices.count) offline") }
        if !standbyConsumers.isEmpty { parts.append(zh ? "待机耗电 \(standbyConsumers.count) 台" : "\(standbyConsumers.count) on standby") }
        if parts.isEmpty {
            return zh ? "本周一切正常" : "All clear this week"
        }
        return parts.joined(separator: " · ")
    }

    /// The exportable report.
    public func markdown(language: Language = .current) -> String {
        let zh = language == .chinese
        let colon = zh ? "：" : ": "
        var lines: [String] = [zh ? "# 家庭健康周报" : "# Weekly Home Health Report", ""]

        if let energy {
            lines.append(zh ? "## 用电" : "## Energy")
            var total = (zh ? "本周 " : "This week ") + Self.kwh(energy.thisWeekKwh)
            if let change = energy.changePercent {
                total += zh ? "，比上周 \(Self.signed(change))" : ", \(Self.signed(change)) vs last week"
            }
            lines.append(total)
            if !energy.topConsumers.isEmpty {
                lines.append("")
                lines.append(zh ? "耗电最多：" : "Top consumers:")
                lines += energy.topConsumers.map { "- \($0.name)\(colon)\(Self.kwh($0.kwh))" }
            }
            if !standbyConsumers.isEmpty {
                lines.append("")
                lines += standbyLines(zh: zh)
            }
            lines.append("")
        } else if !standbyConsumers.isEmpty {
            // No metered week to total, but a plug drawing power while off still needs saying.
            lines.append(zh ? "## 用电" : "## Energy")
            lines += standbyLines(zh: zh)
            lines.append("")
        }

        if !climate.isEmpty {
            lines.append(zh ? "## 温湿度" : "## Temperature & Humidity")
            for room in climate {
                var parts: [String] = []
                if let t = room.range.temperature { parts.append("\(Self.number(t.min))–\(Self.number(t.max))°C") }
                if let h = room.range.humidity { parts.append("\(Self.number(h.min))–\(Self.number(h.max))%") }
                var line = "- \(room.roomName) · \(room.deviceName)\(colon)\(parts.joined(separator: zh ? "，" : ", "))"
                if !room.warnings.isEmpty {
                    line += zh ? "（" : " ("
                    line += room.warnings.map { Self.describe($0, zh: zh) }.joined(separator: zh ? "、" : ", ")
                    line += zh ? "）" : ")"
                }
                lines.append(line)
            }
            lines.append("")
        }

        if !maintenance.isEmpty {
            lines.append(zh ? "## 需要更换" : "## Needs Replacing")
            lines += maintenance.map { m in
                let what = switch m.item {
                case .battery(let p): zh ? "电池 \(p)%" : "battery \(p)%"
                case .filter(let p): zh ? "滤芯 \(p)%" : "filter \(p)%"
                }
                return "- \(m.roomName) · \(m.deviceName)\(colon)\(what)"
            }
            lines.append("")
        }

        if !offlineDevices.isEmpty {
            lines.append(zh ? "## 离线设备" : "## Offline Devices")
            lines += offlineDevices.map { device in
                let note: String
                if device.isOfflineNow {
                    note = zh ? "（现在离线）" : " (offline now)"
                } else {
                    let days = device.daysOffline ?? 0
                    note = zh ? "（本周 \(days) 天出现离线）" : " (dropped offline on \(days) days)"
                }
                return "- \(device.roomName) · \(device.deviceName)\(note)"
            }
            if !unreliableRooms.isEmpty {
                let rooms = unreliableRooms.joined(separator: zh ? "、" : ", ")
                lines.append(zh ? "- 建议排查网络/网关信号的房间：\(rooms)" : "- Rooms recommended for Wi-Fi/gateway check: \(rooms)")
            }
            lines.append("")
        }

        if energy == nil && climate.isEmpty && maintenance.isEmpty && offlineDevices.isEmpty
            && standbyConsumers.isEmpty && unreliableRooms.isEmpty {
            lines.append(zh ? "本周一切正常" : "All clear this week")
            lines.append("")
        }
        return lines.joined(separator: "\n")
    }

    /// One line per plug that keeps drawing power while off.
    private func standbyLines(zh: Bool) -> [String] {
        standbyConsumers.map { consumer in
            let location = consumer.roomName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? consumer.deviceName
                : "\(consumer.roomName) · \(consumer.deviceName)"
            let watts = String(format: "%.1f", consumer.powerWatts)
            return zh
                ? "- 待机耗电提醒：\(location) (关闭状态仍消耗 \(watts) W)"
                : "- Standby power alert: \(location) (drawing \(watts) W while off)"
        }
    }

    private static func describe(_ warning: ClimateWarning, zh: Bool) -> String {
        switch warning {
        case .tooHumid(let v): zh ? "最高湿度 \(number(v))%，偏潮" : "humidity peaked at \(number(v))%"
        case .tooDry(let v): zh ? "最低湿度 \(number(v))%，偏干" : "humidity fell to \(number(v))%"
        case .tooHot(let v): zh ? "最高 \(number(v))°C，偏热" : "peaked at \(number(v))°C"
        case .tooCold(let v): zh ? "最低 \(number(v))°C，偏冷" : "fell to \(number(v))°C"
        }
    }

    private static func number(_ value: Double) -> String {
        let rounded = (value * 10).rounded() / 10
        return rounded == rounded.rounded() ? String(Int(rounded)) : String(format: "%.1f", rounded)
    }

    private static func kwh(_ value: Double) -> String { "\(number(value)) kWh" }

    private static func signed(_ percent: Double) -> String {
        let rounded = Int(percent.rounded())
        return rounded > 0 ? "+\(rounded)%" : "\(rounded)%"
    }
}

// MARK: - Local data sources

extension WeeklyHealthReport {
    /// Builds this week's report from the app's own histories.
    @MainActor
    public static func build(
        devices: [SmartDevice],
        energy: EnergyHistory,
        sensors: SensorHistory,
        offline: OfflineHistory? = nil,
        now: Date = Date()
    ) -> WeeklyHealthReport {
        var climate: [String: ClimateRange] = [:]
        for dev in devices {
            guard case .sensor = dev.kind else { continue }
            let range = ClimateRange(
                temperature: sensors.summary(forDevice: dev.id, window: .last7Days, metric: .temperature, now: now),
                humidity: sensors.summary(forDevice: dev.id, window: .last7Days, metric: .humidity, now: now)
            )
            if range.temperature != nil || range.humidity != nil { climate[dev.id] = range }
        }
        return build(
            devices: devices,
            dailyEnergy: energy.dailyTotals(dayCount: 14, now: now),
            energyByDevice: energy.deviceTotals(dayCount: 7, now: now),
            climate: climate,
            offlineDays: offline?.offlineDays(dayCount: 7, now: now) ?? [:],
            now: now
        )
    }
}

// MARK: - Schedule

/// When the weekly report goes out: Sunday 20:00 local time, once per week. A Mac that slept through
/// Sunday evening still gets it within the next two days; later than that it would read as stale.
public enum WeeklyHealthReportSchedule {
    static let weekday = 1 // Sunday in the Gregorian calendar
    static let hour = 20
    static let catchUpWindow: TimeInterval = 2 * 86_400

    /// The most recent Sunday 20:00 at or before `now`.
    static func latestSlot(atOrBefore now: Date, calendar: Calendar) -> Date? {
        let components = DateComponents(hour: hour, minute: 0, second: 0, weekday: weekday)
        if let match = calendar.nextDate(after: now, matching: components, matchingPolicy: .nextTime, direction: .backward) {
            return match
        }
        return nil
    }

    public static func isDue(now: Date, lastDeliveredAt: Date?, calendar: Calendar = .current) -> Bool {
        let probe = now.addingTimeInterval(1) // `nextDate(after:)` is exclusive; include the exact slot
        guard let slot = latestSlot(atOrBefore: probe, calendar: calendar), slot <= now else { return false }
        guard now.timeIntervalSince(slot) < catchUpWindow else { return false }
        return (lastDeliveredAt ?? .distantPast) < slot
    }
}
