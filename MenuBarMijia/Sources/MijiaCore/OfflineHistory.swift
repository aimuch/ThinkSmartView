import Foundation

/// Days each device was seen offline over the last two weeks (#182), so the weekly report can name
/// devices that keep dropping out even when they are back online at report time. A day counts once
/// however many refreshes saw the device offline.
@MainActor
public final class OfflineHistory {
    public static let retentionDays = 14
    public static let storageKey = "offline_day_history_v1"

    private let defaults: UserDefaults
    private let calendar: Calendar
    /// Device id → start-of-day dates the device was seen offline.
    private var days: [String: Set<Date>]

    public init(defaults: UserDefaults = .standard, calendar: Calendar = .current) {
        self.defaults = defaults
        self.calendar = calendar
        let raw = defaults.dictionary(forKey: Self.storageKey) as? [String: [Double]] ?? [:]
        self.days = raw.mapValues { Set($0.map(Date.init(timeIntervalSince1970:))) }
    }

    public func record(devices: [SmartDevice], now: Date) {
        let today = calendar.startOfDay(for: now)
        var changed = trim(now: now)
        for dev in devices where !dev.isOnline {
            if days[dev.id, default: []].insert(today).inserted { changed = true }
        }
        if changed { persist() }
    }

    /// Offline-day counts for the window ending today, only for devices with at least one.
    public func offlineDays(dayCount: Int = 7, now: Date) -> [String: Int] {
        let today = calendar.startOfDay(for: now)
        guard let first = calendar.date(byAdding: .day, value: -(min(max(dayCount, 1), Self.retentionDays) - 1), to: today) else {
            return [:]
        }
        return days.compactMapValues { set in
            let count = set.filter { $0 >= first && $0 <= today }.count
            return count > 0 ? count : nil
        }
    }

    public func clear() {
        days = [:]
        defaults.removeObject(forKey: Self.storageKey)
    }

    private func trim(now: Date) -> Bool {
        guard let cutoff = calendar.date(byAdding: .day, value: -(Self.retentionDays - 1), to: calendar.startOfDay(for: now)) else {
            return false
        }
        let before = days
        days = days.compactMapValues { set in
            let kept = set.filter { $0 >= cutoff }
            return kept.isEmpty ? nil : kept
        }
        return days != before
    }

    private func persist() {
        defaults.set(days.mapValues { $0.map(\.timeIntervalSince1970).sorted() }, forKey: Self.storageKey)
    }
}
