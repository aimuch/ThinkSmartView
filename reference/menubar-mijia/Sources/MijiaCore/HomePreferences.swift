import Foundation

/// One persisted setting: its defaults key, its default and how it is read and written.
///
/// `HomeStore` keeps each setting as an observable stored property (views bind to
/// `store.hideOfflineDevices`), but the key, the default and the coding used to be written twice:
/// once in `init` and once in the property's `didSet`, with defaults living only in `init`.
/// A `HomePreference` is the single description both sides use.
public struct HomePreference<Value>: Sendable {
    public let key: String
    private let read: @Sendable (UserDefaults) -> Value
    private let write: @Sendable (Value, UserDefaults) -> Void

    init(key: String, read: @escaping @Sendable (UserDefaults) -> Value, write: @escaping @Sendable (Value, UserDefaults) -> Void) {
        self.key = key
        self.read = read
        self.write = write
    }

    public func load(from defaults: UserDefaults) -> Value { read(defaults) }
    public func save(_ value: Value, to defaults: UserDefaults) { write(value, defaults) }
}

extension HomePreference where Value == Bool {
    /// A switch; a missing key reads as `defaultValue`. A stored non-Bool value reads the way
    /// `UserDefaults.bool(forKey:)` does, matching earlier builds.
    static func flag(_ key: String, default defaultValue: Bool) -> Self {
        .init(
            key: key,
            read: { $0.object(forKey: key) == nil ? defaultValue : $0.bool(forKey: key) },
            write: { $1.set($0, forKey: key) }
        )
    }
}

extension HomePreference where Value == Int {
    static func integer(_ key: String, default defaultValue: Int) -> Self {
        .init(key: key, read: { ($0.object(forKey: key) as? Int) ?? defaultValue }, write: { $1.set($0, forKey: key) })
    }
}

extension HomePreference where Value == String {
    static func text(_ key: String, default defaultValue: @escaping @Sendable () -> String) -> Self {
        .init(key: key, read: { $0.string(forKey: key) ?? defaultValue() }, write: { $1.set($0, forKey: key) })
    }
}

extension HomePreference where Value == Set<String> {
    static func idSet(_ key: String) -> Self {
        .init(key: key, read: { Set($0.stringArray(forKey: key) ?? []) }, write: { $1.set(Array($0), forKey: key) })
    }
}

extension HomePreference where Value == [String] {
    static func idList(_ key: String) -> Self {
        .init(key: key, read: { $0.stringArray(forKey: key) ?? [] }, write: { $1.set($0, forKey: key) })
    }
}

extension HomePreference where Value == [String: Bool] {
    static func flagMap(_ key: String) -> Self {
        .init(key: key, read: { $0.dictionary(forKey: key) as? [String: Bool] ?? [:] }, write: { $1.set($0, forKey: key) })
    }
}

/// Every simple setting `HomeStore` persists, with its key and default in one row.
public enum HomePreferences {
    public static let hideOfflineDevices = HomePreference.flag("hide_offline_devices", default: false)
    /// Hide lights that belong to a Mi light group while the group card is on screen (#189).
    public static let hideGroupedLights = HomePreference.flag("hide_grouped_lights", default: true)
    public static let allowAdvancedFacetWrites = HomePreference.flag("allow_advanced_facet_writes", default: false)
    public static let enableGlobalHotkey = HomePreference.flag("enable_global_hotkey", default: true)
    public static let isCompactGridMode = HomePreference.flag("compact_grid_mode", default: true)
    public static let showQuickActionsBar = HomePreference.flag("show_quick_actions_bar", default: true)
    public static let enableGrokBall = HomePreference.flag("enable_grok_ball", default: false)
    public static let enableSafetyAlerts = HomePreference.flag("enable_safety_alerts", default: true)
    /// Off by default: a new recurring notification (#182).
    public static let enableWeeklyHealthReport = HomePreference.flag("weekly_health_report_enabled_v1", default: false)
    /// Off by default: privacy and interruption (#308).
    public static let doorbellAutoPopup = HomePreference.flag("doorbell_auto_popup", default: false)
    public static let doorbellChime = HomePreference.flag("doorbell_chime", default: false)
    public static let doorbellPopupSeconds = HomePreference.integer("doorbell_popup_seconds", default: 60)
    /// On by default (#143): only external paths can still fire while the screen is locked.
    public static let blockControlWhileLocked = HomePreference.flag("block_control_while_locked", default: true)
    public static let sensorAlertsOnlyWhenAway = HomePreference.flag("safety_alert_only_when_away_v1", default: false)
    public static let menuBarTargetId = HomePreference.text(ViewPreferenceKeys.menuBarTargetId, default: { "auto:primary" })
    public static let homeName = HomePreference.text("home_name", default: { KeychainHelper.localizedDefaultHomeName })
    public static let favoriteDeviceIds = HomePreference.idSet(ViewPreferenceKeys.favoriteDeviceIds)
    public static let favoriteOrder = HomePreference.idList(ViewPreferenceKeys.favoriteOrder)
    public static let hiddenDeviceIds = HomePreference.idSet(ViewPreferenceKeys.hiddenDeviceIds)
    /// Per-device choice for 全关; a missing entry means `joinsAllOffByDefault` (#23).
    public static let allOffOverrides = HomePreference.flagMap(ViewPreferenceKeys.allOffOverrides)
}
