import Foundation

// Safety alerts: the store's preferences and actions around `SafetyMonitor` (#48).
extension HomeStore {

    public func isSensorAlertEnabled(_ kind: SensorAlertKind) -> Bool {
        sensorAlertPreferences[kind] ?? kind.enabledByDefault
    }

    public func setSensorAlertEnabled(_ kind: SensorAlertKind, _ enabled: Bool) {
        sensorAlertPreferences[kind] = enabled
        userDefaults.set(enabled, forKey: kind.defaultsKey)
    }

    public var menuBarShowsHazard: Bool {
        guard enableSafetyAlerts else { return false }
        for device in devices {
            guard case .sensor(let spec) = device.kind else { continue }
            for observation in spec.observations
            where observation.alarming && observation.kind.colorsMenuBar && isSensorAlertEnabled(observation.kind) {
                return true
            }
        }
        return false
    }

    @discardableResult
    public func evaluateSafetyAlerts(now: Date = Date()) -> [SafetyAlert] {
        guard enableSafetyAlerts else { return [] }
        let enabled = Dictionary(uniqueKeysWithValues: SensorAlertKind.allCases.map { ($0, isSensorAlertEnabled($0)) })
        return safetyMonitor.evaluate(
            devices: devices,
            favorites: favoriteDeviceIds,
            sensorPolicy: SensorAlertPolicy(
                enabled: enabled,
                onlyWhenAway: sensorAlertsOnlyWhenAway,
                includesAway: awayGap.includesAway
            ),
            now: now
        )
    }

    public func executeSafetyAlertAction(_ alert: SafetyAlert) {
        guard let cmd = alert.turnOffCommand else { return }
        Task {
            try? await provider.execute(cmd)
            await provider.refreshNow()
        }
    }
}
