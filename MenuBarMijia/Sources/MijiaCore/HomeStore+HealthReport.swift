import Foundation

// Weekly home health report delivery (#182).
extension HomeStore {
    static let weeklyHealthReportDeliveredAtKey = "weekly_health_report_delivered_at_v1"

    /// This week's report from the devices on screen and the local histories.
    public func weeklyHealthReport(now: Date = Date()) -> WeeklyHealthReport {
        WeeklyHealthReport.build(
            devices: devices, energy: energyHistory, sensors: sensorHistory, offline: offlineHistory, now: now
        )
    }

    /// The report to notify about now, if the weekly report is on and this week's has not gone out.
    /// Taking it marks the week delivered.
    public func takeDueWeeklyHealthReport(now: Date = Date(), calendar: Calendar = .current) -> WeeklyHealthReport? {
        guard enableWeeklyHealthReport else { return nil }
        let last = userDefaults.object(forKey: Self.weeklyHealthReportDeliveredAtKey) as? Date
        guard WeeklyHealthReportSchedule.isDue(now: now, lastDeliveredAt: last, calendar: calendar) else { return nil }
        userDefaults.set(now, forKey: Self.weeklyHealthReportDeliveredAtKey)
        return weeklyHealthReport(now: now)
    }
}
