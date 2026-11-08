import Foundation

/// The safety-alert engine and the state it remembers between evaluations: who was online,
/// when a low battery last alerted (persisted, 24 h cooldown), when a device switched on, which
/// long runs already alerted this session, and the sensor alarm edges (persisted).
///
/// `HomeStore` owns one and feeds it every snapshot; tests drive it directly without a store.
struct SafetyMonitor {
    /// #35: a plug joins the long-running reminder only when it reports power above this threshold.
    /// Plugs without a power reading (router, fridge, fish tank) never trigger it; 300 W keeps
    /// low-draw infrastructure quiet while a coffee machine or heater still alerts after 45 min.
    static let longRunningPlugMinimumWatts: Double = 300
    static let lowBatteryAlertedAtKey = "safety_low_battery_alerted_at_v1"
    static let sensorEdgeKey = "safety_sensor_edge_v1"

    private let defaults: UserDefaults
    private var previousOnlineDeviceIds: Set<String> = []
    private var lowBatteryAlertedAt: [String: Date]
    private var activeSinceByDeviceId: [String: Date] = [:]
    private var longRunAlertedForSession: Set<String> = []
    private var sensorEdge: [String: Bool]

    init(defaults: UserDefaults) {
        self.defaults = defaults
        let storedBattery = defaults.dictionary(forKey: Self.lowBatteryAlertedAtKey) ?? [:]
        self.lowBatteryAlertedAt = storedBattery.compactMapValues { ($0 as? NSNumber)?.doubleValue }
            .mapValues { Date(timeIntervalSince1970: $0) }
        var edge: [String: Bool] = [:]
        for (key, value) in defaults.dictionary(forKey: Self.sensorEdgeKey) ?? [:] {
            if let number = value as? NSNumber { edge[key] = number.boolValue }
        }
        self.sensorEdge = edge
    }

    /// The alerts this snapshot raises. Remembers what it alerted so the next call does not repeat it.
    mutating func evaluate(
        devices: [SmartDevice],
        favorites: Set<String>,
        sensorPolicy: SensorAlertPolicy,
        now: Date,
        calendar: Calendar = .current
    ) -> [SafetyAlert] {
        var emitted: [SafetyAlert] = []

        for dev in devices {
            // 1. Starred device offline transition check
            if favorites.contains(dev.id) {
                if previousOnlineDeviceIds.contains(dev.id) && !dev.isOnline {
                    emitted.append(
                        SafetyAlert(
                            id: "offline:\(dev.id):\(Int(now.timeIntervalSince1970))",
                            deviceId: dev.id,
                            title: L10n.tr("\(dev.name) 已离线", "\(dev.name) Offline"),
                            body: L10n.tr(
                                "\(dev.roomName)的「\(dev.name)」刚刚断开连接或断电。",
                                "\"\(dev.name)\" in \(dev.roomName) just went offline or lost power."
                            )
                        )
                    )
                }
            }

            // 2. Low battery check (<15% for sensors/locks, 24h cooldown)
            if dev.isOnline, case .sensor(let s) = dev.kind, let bat = s.battery, bat > 0 && bat < 15 {
                let lastAlert = lowBatteryAlertedAt[dev.id]
                if lastAlert == nil || now.timeIntervalSince(lastAlert!) >= 86_400 {
                    lowBatteryAlertedAt[dev.id] = now
                    persistLowBatteryAlertedAt()
                    emitted.append(
                        SafetyAlert(
                            id: "battery:\(dev.id)",
                            deviceId: dev.id,
                            title: L10n.tr("\(dev.name) 电量低 (\(bat)%)", "\(dev.name) Low Battery (\(bat)%)"),
                            body: L10n.tr(
                                "请及时为\(dev.roomName)的「\(dev.name)」更换电池或充电。",
                                "Please replace or recharge the battery for \"\(dev.name)\" (\(dev.roomName))."
                            )
                        )
                    )
                }
            }

            // 3. Long-running plug (>=45m) or climate (>=4h) check
            if dev.isActiveOn {
                let startedAt = activeSinceByDeviceId[dev.id] ?? {
                    activeSinceByDeviceId[dev.id] = now
                    return now
                }()
                let elapsed = now.timeIntervalSince(startedAt)

                switch dev.kind {
                // A read-only plug (unverified spec, mains breaker #265) gets no reminder: its
                // notification button would send the very switch command the card refuses.
                case .plug(let s) where s.powerWritable
                    && elapsed >= 45 * 60
                    && (s.powerWatts ?? 0) > Self.longRunningPlugMinimumWatts
                    && !longRunAlertedForSession.contains(dev.id):
                    longRunAlertedForSession.insert(dev.id)
                    emitted.append(
                        SafetyAlert(
                            id: "longrun:\(dev.id)",
                            deviceId: dev.id,
                            title: L10n.tr("\(dev.name) 已开启超过 45 分钟", "\(dev.name) On for Over 45m"),
                            body: L10n.tr(
                                "当前功率 \(Int((s.powerWatts ?? 0).rounded()))W，如需关闭可直接点击通知按钮。",
                                "Drawing \(Int((s.powerWatts ?? 0).rounded()))W. Click the notification action to turn it off."
                            ),
                            turnOffCommand: .setSwitch(entityId: s.switchEntityId, isOn: false)
                        )
                    )
                case .climate(let s) where elapsed >= 4 * 3600 && !longRunAlertedForSession.contains(dev.id):
                    longRunAlertedForSession.insert(dev.id)
                    emitted.append(
                        SafetyAlert(
                            id: "longrun:\(dev.id)",
                            deviceId: dev.id,
                            title: L10n.tr("\(dev.name) 已连续运行 4 小时", "\(dev.name) Running for 4 Hours"),
                            body: L10n.tr(
                                "当前设定 \(L10n.temperature(s.targetTemperature))，请确认是否仍需开启。",
                                "Target temp \(L10n.temperature(s.targetTemperature)). Check if you still need it running."
                            ),
                            turnOffCommand: .setClimatePower(entityId: s.entityId, isOn: false, fallbackMode: s.hvacMode)
                        )
                    )
                default:
                    break
                }
            } else {
                activeSinceByDeviceId.removeValue(forKey: dev.id)
                longRunAlertedForSession.remove(dev.id)
            }
        }

        let evaluated = SensorAlerts.evaluate(
            devices: devices,
            ledger: sensorEdge,
            now: now,
            calendar: calendar,
            policy: sensorPolicy
        )
        if evaluated.ledger != sensorEdge {
            sensorEdge = evaluated.ledger
            defaults.set(sensorEdge, forKey: Self.sensorEdgeKey)
        }
        emitted.append(contentsOf: evaluated.alerts)

        previousOnlineDeviceIds = Set(devices.filter(\.isOnline).map(\.id))
        return emitted
    }

    /// Remembers the 24 h low-battery cooldown across launches (#35).
    private func persistLowBatteryAlertedAt() {
        defaults.set(lowBatteryAlertedAt.mapValues { $0.timeIntervalSince1970 }, forKey: Self.lowBatteryAlertedAtKey)
    }
}
