import Foundation
import Testing
@testable import MijiaCore

/// `SafetyMonitor` is driven directly, without a store: snapshots in, alerts out.
struct SafetyMonitorTests {
    private static let policy = SensorAlertPolicy(enabled: [:], onlyWhenAway: false, includesAway: false)

    private func defaults() -> UserDefaults {
        UserDefaults(suiteName: "test.safetymonitor.\(UUID().uuidString)")!
    }

    private func sensor(battery: Int, online: Bool = true) -> SmartDevice {
        SmartDevice(id: "door", name: "门磁", roomId: "r", roomName: "玄关", model: "lumi.sensor", isOnline: online,
                    kind: .sensor(SensorSpec(battery: battery)))
    }

    private func plug(on: Bool, watts: Double) -> SmartDevice {
        SmartDevice(id: "heater", name: "暖风机", roomId: "r", roomName: "书房", model: "cuco.plug", isOnline: true,
                    kind: .plug(PlugSpec(switchEntityId: "switch.heater", isOn: on, powerWatts: watts)))
    }

    @Test("Low battery alerts once per 24 h, and the cooldown survives a relaunch")
    func lowBatteryCooldown() {
        let store = defaults()
        let start = Date(timeIntervalSince1970: 1_000_000)
        var monitor = SafetyMonitor(defaults: store)
        #expect(monitor.evaluate(devices: [sensor(battery: 9)], favorites: [], sensorPolicy: Self.policy, now: start).map(\.id) == ["battery:door"])
        #expect(monitor.evaluate(devices: [sensor(battery: 9)], favorites: [], sensorPolicy: Self.policy, now: start + 3600).isEmpty)

        var relaunched = SafetyMonitor(defaults: store)
        #expect(relaunched.evaluate(devices: [sensor(battery: 9)], favorites: [], sensorPolicy: Self.policy, now: start + 7200).isEmpty)
        #expect(relaunched.evaluate(devices: [sensor(battery: 9)], favorites: [], sensorPolicy: Self.policy, now: start + 86_400).count == 1)
    }

    @Test("A starred device alerts when it goes from online to offline, not while it stays offline")
    func starredOfflineTransition() {
        var monitor = SafetyMonitor(defaults: defaults())
        let now = Date(timeIntervalSince1970: 2_000_000)
        _ = monitor.evaluate(devices: [sensor(battery: 80)], favorites: ["door"], sensorPolicy: Self.policy, now: now)
        let dropped = monitor.evaluate(devices: [sensor(battery: 80, online: false)], favorites: ["door"], sensorPolicy: Self.policy, now: now + 60)
        #expect(dropped.count == 1)
        #expect(dropped.first?.id.hasPrefix("offline:door") == true)
        #expect(monitor.evaluate(devices: [sensor(battery: 80, online: false)], favorites: ["door"], sensorPolicy: Self.policy, now: now + 120).isEmpty)
    }

    @Test("A high-draw plug reminds once after 45 minutes; switching off re-arms it")
    func longRunningPlug() {
        var monitor = SafetyMonitor(defaults: defaults())
        let t0 = Date(timeIntervalSince1970: 3_000_000)
        #expect(monitor.evaluate(devices: [plug(on: true, watts: 900)], favorites: [], sensorPolicy: Self.policy, now: t0).isEmpty)
        let reminded = monitor.evaluate(devices: [plug(on: true, watts: 900)], favorites: [], sensorPolicy: Self.policy, now: t0 + 46 * 60)
        #expect(reminded.map(\.id) == ["longrun:heater"])
        #expect(reminded.first?.turnOffCommand == .setSwitch(entityId: "switch.heater", isOn: false))
        #expect(monitor.evaluate(devices: [plug(on: true, watts: 900)], favorites: [], sensorPolicy: Self.policy, now: t0 + 90 * 60).isEmpty)
        _ = monitor.evaluate(devices: [plug(on: false, watts: 0)], favorites: [], sensorPolicy: Self.policy, now: t0 + 91 * 60)
        _ = monitor.evaluate(devices: [plug(on: true, watts: 900)], favorites: [], sensorPolicy: Self.policy, now: t0 + 92 * 60)
        #expect(monitor.evaluate(devices: [plug(on: true, watts: 900)], favorites: [], sensorPolicy: Self.policy, now: t0 + 140 * 60).count == 1)
    }

    @Test("A low-draw plug never reminds")
    func lowDrawPlugStaysQuiet() {
        var monitor = SafetyMonitor(defaults: defaults())
        let t0 = Date(timeIntervalSince1970: 4_000_000)
        _ = monitor.evaluate(devices: [plug(on: true, watts: 12)], favorites: [], sensorPolicy: Self.policy, now: t0)
        #expect(monitor.evaluate(devices: [plug(on: true, watts: 12)], favorites: [], sensorPolicy: Self.policy, now: t0 + 5 * 3600).isEmpty)
    }
}

struct ReadBackTicketsTests {
    @Test("A claimed device waits; a second claim is refused until the tap is released")
    func claimAndRelease() throws {
        var tickets = ReadBackTickets()
        let claimed_first = tickets.claim("lamp")
        let first = try #require(claimed_first)
        #expect(tickets.waitingDeviceIds == ["lamp"])
        #expect(tickets.claim("lamp") == nil)
        tickets.releaseTap("lamp", ticket: first)
        #expect(tickets.waitingDeviceIds.isEmpty)
        #expect(tickets.isInFlight("lamp"))
        #expect(tickets.isLatest("lamp", ticket: first))
    }

    @Test("A newer toggle supersedes an older echo retry and keeps the lock exemption when the older one finishes")
    func newerTicketSupersedes() throws {
        var tickets = ReadBackTickets()
        let claimed_first = tickets.claim("lamp")
        let first = try #require(claimed_first)
        tickets.releaseTap("lamp", ticket: first)
        let claimed_second = tickets.claim("lamp")
        let second = try #require(claimed_second)
        #expect(!tickets.isLatest("lamp", ticket: first))
        tickets.finish("lamp", ticket: first)
        #expect(tickets.isInFlight("lamp"))
        #expect(tickets.waitingDeviceIds == ["lamp"])
        tickets.finish("lamp", ticket: second)
        #expect(tickets.isEmpty)
    }

    @Test("Reset drops every ticket but never reuses a number")
    func resetKeepsCounting() throws {
        var tickets = ReadBackTickets()
        let claimed_first = tickets.claim("lamp")
        let first = try #require(claimed_first)
        tickets.reset()
        #expect(tickets.isEmpty)
        let claimed_second = tickets.claim("lamp")
        let second = try #require(claimed_second)
        #expect(second != first)
    }
}
