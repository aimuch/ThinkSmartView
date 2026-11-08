import Foundation
import Testing
@testable import MijiaCore

private let day: TimeInterval = 86_400
private let now = Date(timeIntervalSince1970: 1_800_000_000)

private func device(
    _ id: String,
    _ name: String,
    room: String = "客厅",
    online: Bool = true,
    kind: DeviceKind
) -> SmartDevice {
    SmartDevice(id: id, name: name, roomId: room, roomName: room, model: "test.\(id)", isOnline: online, kind: kind)
}

/// 14 daily totals, oldest first: the first 7 are last week, the last 7 this week.
private func fortnight(lastWeek: [Double?], thisWeek: [Double?]) -> [EnergyDayTotal] {
    (lastWeek + thisWeek).enumerated().map { offset, kwh in
        EnergyDayTotal(day: now.addingTimeInterval(Double(offset - 13) * day), kwh: kwh ?? 0, isObserved: kwh != nil)
    }
}

private func summary(min: Double, max: Double, average: Double) -> SensorMetricSummary {
    SensorMetricSummary(min: min, max: max, average: average, latest: average, observedCount: 7)
}

/// #182: a weekly, locally computed home health report.
struct WeeklyHealthReportTests {
    @Test("Compares this week's energy with last week and ranks the top consumers")
    func energyComparison() throws {
        let report = WeeklyHealthReport.build(
            devices: [],
            dailyEnergy: fortnight(lastWeek: Array(repeating: 1.0, count: 7), thisWeek: Array(repeating: 1.5, count: 7)),
            energyByDevice: [
                EnergyDeviceTotal(deviceId: "a", name: "热水器插座", kwh: 6),
                EnergyDeviceTotal(deviceId: "b", name: "电脑插座", kwh: 3),
                EnergyDeviceTotal(deviceId: "c", name: "台灯插座", kwh: 1),
                EnergyDeviceTotal(deviceId: "d", name: "路由器插座", kwh: 0.5),
            ],
            climate: [:],
            now: now
        )
        let energy = try #require(report.energy)
        #expect(abs(energy.thisWeekKwh - 10.5) < 1e-9)
        #expect(abs(try #require(energy.lastWeekKwh) - 7) < 1e-9)
        #expect(abs(try #require(energy.changePercent) - 50) < 1e-9)
        #expect(energy.topConsumers.map(\.name) == ["热水器插座", "电脑插座", "台灯插座"])
    }

    @Test("Leaves out the energy section, and the comparison, when no day was measured")
    func energyNeedsObservedDays() throws {
        let none = WeeklyHealthReport.build(
            devices: [],
            dailyEnergy: fortnight(lastWeek: Array(repeating: nil, count: 7), thisWeek: Array(repeating: nil, count: 7)),
            energyByDevice: [],
            climate: [:],
            now: now
        )
        #expect(none.energy == nil)

        // Recording started this week: no invented baseline for last week.
        let fresh = WeeklyHealthReport.build(
            devices: [],
            dailyEnergy: fortnight(lastWeek: Array(repeating: nil, count: 7), thisWeek: [nil, nil, nil, 2, 2, 2, 2]),
            energyByDevice: [],
            climate: [:],
            now: now
        )
        let energy = try #require(fresh.energy)
        #expect(energy.lastWeekKwh == nil)
        #expect(energy.changePercent == nil)
    }

    @Test("Reports each sensor's weekly range and flags humidity and temperature outside the comfort band")
    func climateRanges() throws {
        let devices = [
            device("t1", "温湿度计", room: "卧室", kind: .sensor(SensorSpec(temperature: 24, humidity: 55))),
            device("t2", "温湿度计", room: "卫生间", kind: .sensor(SensorSpec(temperature: 26, humidity: 80))),
        ]
        let report = WeeklyHealthReport.build(
            devices: devices,
            dailyEnergy: [],
            energyByDevice: [],
            climate: [
                "t1": .init(temperature: summary(min: 21, max: 26, average: 23.5), humidity: summary(min: 40, max: 60, average: 50)),
                "t2": .init(temperature: summary(min: 22, max: 31, average: 25), humidity: summary(min: 55, max: 88, average: 72)),
            ],
            now: now
        )
        #expect(report.climate.map(\.roomName) == ["卧室", "卫生间"])
        #expect(report.climate[0].warnings.isEmpty)
        #expect(report.climate[1].warnings == [.tooHumid(88), .tooHot(31)])
    }

    @Test("Lists filters and batteries that are about to run out, lowest first")
    func maintenance() {
        let devices = [
            device("p", "净化器", kind: .fan(FanSpec(entityId: "fan.p", isOn: true, filterLifePercent: 8))),
            device("p2", "新风机", kind: .fan(FanSpec(entityId: "fan.p2", isOn: true, filterLifePercent: 60))),
            device("s", "门磁", room: "玄关", kind: .sensor(SensorSpec(battery: 5))),
            device("s2", "人体传感器", kind: .sensor(SensorSpec(battery: 18))),
            device("s3", "温湿度计", kind: .sensor(SensorSpec(battery: 90))),
            device("s4", "离线门磁", online: false, kind: .sensor(SensorSpec(battery: 3))),
        ]
        let report = WeeklyHealthReport.build(devices: devices, dailyEnergy: [], energyByDevice: [], climate: [:], now: now)
        #expect(report.maintenance.map(\.deviceName) == ["门磁", "净化器", "人体传感器"])
        #expect(report.maintenance.map(\.item) == [.battery(5), .filter(8), .battery(18)])
    }

    @Test("Lists devices that are offline when the report is built")
    func offline() {
        let devices = [
            device("a", "台灯", room: "书房", online: false, kind: .sensor(SensorSpec())),
            device("b", "空调", online: true, kind: .sensor(SensorSpec())),
        ]
        let report = WeeklyHealthReport.build(devices: devices, dailyEnergy: [], energyByDevice: [], climate: [:], now: now)
        #expect(report.offlineDevices == [.init(deviceName: "台灯", roomName: "书房")])
    }

    @Test("Renders Markdown with one section per finding and a one-line notification summary")
    func rendering() {
        let devices = [
            device("s", "门磁", room: "玄关", kind: .sensor(SensorSpec(battery: 5))),
            device("a", "台灯", room: "书房", online: false, kind: .sensor(SensorSpec())),
        ]
        let report = WeeklyHealthReport.build(
            devices: devices,
            dailyEnergy: fortnight(lastWeek: Array(repeating: 1.0, count: 7), thisWeek: Array(repeating: 0.5, count: 7)),
            energyByDevice: [EnergyDeviceTotal(deviceId: "x", name: "电脑插座", kwh: 3.5)],
            climate: [:],
            now: now
        )
        let markdown = report.markdown(language: .chinese)
        #expect(markdown.contains("## 用电"))
        #expect(markdown.contains("3.5 kWh"))
        #expect(markdown.contains("比上周 -50%"))
        #expect(markdown.contains("- 电脑插座：3.5 kWh"))
        #expect(markdown.contains("## 需要更换"))
        #expect(markdown.contains("- 玄关 · 门磁：电池 5%"))
        #expect(markdown.contains("## 离线设备"))
        #expect(markdown.contains("- 书房 · 台灯"))
        #expect(!markdown.contains("## 温湿度"))
        #expect(report.notificationSummary(language: .chinese) == "本周用电 3.5 kWh（比上周 -50%） · 1 台待更换 · 1 台离线")

        let english = report.markdown(language: .english)
        #expect(english.contains("## Energy"))
        #expect(english.contains("- 玄关 · 门磁: battery 5%"))
    }

    @Test("Says the home looks fine when nothing needs attention")
    func allClear() {
        let report = WeeklyHealthReport.build(devices: [], dailyEnergy: [], energyByDevice: [], climate: [:], now: now)
        #expect(report.notificationSummary(language: .chinese) == "本周一切正常")
        #expect(report.markdown(language: .chinese).contains("本周一切正常"))
    }

    @Test("Flags a vacuum whose battery is about to run out")
    func vacuumBattery() {
        let devices = [
            device("v", "扫地机", kind: .vacuum(VacuumSpec(entityId: "vacuum.v", status: "docked", battery: 12))),
            device("v0", "亏电扫地机", kind: .vacuum(VacuumSpec(entityId: "vacuum.v0", status: "docked", battery: 0))),
            device("v2", "备用扫地机", kind: .vacuum(VacuumSpec(entityId: "vacuum.v2", status: "cleaning", battery: 75))),
            device("v3", "离线扫地机", online: false, kind: .vacuum(VacuumSpec(entityId: "vacuum.v3", status: "docked", battery: 8))),
        ]
        let report = WeeklyHealthReport.build(devices: devices, dailyEnergy: [], energyByDevice: [], climate: [:], now: now)
        #expect(report.maintenance.map(\.deviceName) == ["亏电扫地机", "扫地机"])
        #expect(report.maintenance.map(\.item) == [.battery(0), .battery(12)])
    }

    @Test("Flags plugs that draw power while switched off, highest draw first, in both languages")
    func standbyConsumers() {
        let devices = [
            device("p1", "电视插座", room: "客厅", kind: .plug(PlugSpec(switchEntityId: "switch.p1", isOn: false, powerWatts: 3.5))),
            device("p2", "路由器插座", room: "书房", kind: .plug(PlugSpec(switchEntityId: "switch.p2", isOn: false, powerWatts: 6))),
            device("p3", "台灯插座", room: "书房", kind: .plug(PlugSpec(switchEntityId: "switch.p3", isOn: false, powerWatts: 0.4))),
            device("p4", "电脑插座", kind: .plug(PlugSpec(switchEntityId: "switch.p4", isOn: true, powerWatts: 60))),
            device("p5", "离线插座", online: false, kind: .plug(PlugSpec(switchEntityId: "switch.p5", isOn: false, powerWatts: 2))),
            device("p6", "车库插座", room: "", kind: .plug(PlugSpec(switchEntityId: "switch.p6", isOn: false, powerWatts: 2.5))),
        ]
        let report = WeeklyHealthReport.build(devices: devices, dailyEnergy: [], energyByDevice: [], climate: [:], now: now)
        #expect(report.standbyConsumers.map(\.deviceName) == ["路由器插座", "电视插座", "车库插座"])
        #expect(report.standbyConsumers.map(\.powerWatts) == [6, 3.5, 2.5])

        let markdown = report.markdown(language: .chinese)
        #expect(markdown.contains("## 用电"))
        #expect(markdown.contains("- 待机耗电提醒：书房 · 路由器插座 (关闭状态仍消耗 6.0 W)"))
        #expect(markdown.contains("- 待机耗电提醒：客厅 · 电视插座 (关闭状态仍消耗 3.5 W)"))
        // A plug without a room keeps its name only, with no dangling separator.
        #expect(markdown.contains("- 待机耗电提醒：车库插座 (关闭状态仍消耗 2.5 W)"))
        #expect(!markdown.contains(" · 车库插座"))
        #expect(!markdown.contains("本周一切正常"))

        let english = report.markdown(language: .english)
        #expect(english.contains("- Standby power alert: 客厅 · 电视插座 (drawing 3.5 W while off)"))
        #expect(english.contains("- Standby power alert: 车库插座 (drawing 2.5 W while off)"))

        // A standby alert must show up in the notification instead of being read as an all-clear.
        #expect(report.notificationSummary(language: .chinese) == "1 台离线 · 待机耗电 3 台")
        #expect(report.notificationSummary(language: .english) == "1 offline · 3 on standby")

        let standbyOnly = WeeklyHealthReport.build(
            devices: [device("p7", "微波炉插座", room: "厨房", kind: .plug(PlugSpec(switchEntityId: "switch.p7", isOn: false, powerWatts: 2)))],
            dailyEnergy: [],
            energyByDevice: [],
            climate: [:],
            now: now
        )
        #expect(standbyOnly.notificationSummary(language: .chinese) == "待机耗电 1 台")
        #expect(standbyOnly.notificationSummary(language: .english) == "1 on standby")
    }

    @Test("Suggests checking Wi-Fi or the gateway for rooms with several unreliable devices")
    func unreliableRooms() {
        let devices = [
            device("a1", "台灯", room: "客厅", kind: .sensor(SensorSpec())),
            device("a2", "门磁", room: "客厅", kind: .sensor(SensorSpec())),
            device("b1", "空调", room: "卧室", kind: .sensor(SensorSpec())),
            device("c1", "摄像头", room: "书房", online: false, kind: .sensor(SensorSpec())),
            device("c2", "路由器", room: "书房", online: false, kind: .sensor(SensorSpec())),
            device("d1", "人体传感器", room: "卫生间", online: false, kind: .sensor(SensorSpec())),
        ]
        let report = WeeklyHealthReport.build(
            devices: devices,
            dailyEnergy: [],
            energyByDevice: [],
            climate: [:],
            offlineDays: ["a1": 3, "a2": 4, "b1": 2],
            now: now
        )
        // 客厅: two devices dropped out on 3+ days. 书房: two offline now. 卧室 and 卫生间: one each.
        #expect(report.unreliableRooms == ["书房", "客厅"])

        let markdown = report.markdown(language: .chinese)
        #expect(markdown.contains("## 离线设备"))
        #expect(markdown.contains("- 建议排查网络/网关信号的房间：书房、客厅"))

        let english = report.markdown(language: .english)
        #expect(english.contains("- Rooms recommended for Wi-Fi/gateway check: 书房, 客厅"))
    }
}
