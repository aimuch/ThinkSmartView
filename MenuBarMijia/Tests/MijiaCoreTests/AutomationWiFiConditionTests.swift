import Foundation
import Testing
@testable import MijiaCore

/// Wi-Fi conditions: the "and" half of a rule, and the safety decisions around not knowing the SSID.
struct AutomationWiFiConditionTests {
    // MARK: Matching

    @Test func anyIsAlwaysSatisfied() {
        #expect(AutomationWiFiCondition.any.evaluate(wifiSSID: nil, isAuthorized: false) == .satisfied)
        #expect(AutomationWiFiCondition.any.evaluate(wifiSSID: "Home", isAuthorized: true) == .satisfied)
    }

    @Test func inAnyMatchesExactNamesOnly() {
        let condition = AutomationWiFiCondition.inAny(["Home", "Office-5G"])
        #expect(condition.evaluate(wifiSSID: "Home", isAuthorized: true) == .satisfied)
        #expect(condition.evaluate(wifiSSID: "Office-5G", isAuthorized: true) == .satisfied)
        // SSIDs are case-sensitive, so this must not match.
        #expect(condition.evaluate(wifiSSID: "home", isAuthorized: true) == .networkNotListed("home"))
        #expect(condition.evaluate(wifiSSID: "Cafe", isAuthorized: true) == .networkNotListed("Cafe"))
    }

    @Test func notInIsSatisfiedOnAnyOtherNetwork() {
        let condition = AutomationWiFiCondition.notIn(["Home"])
        #expect(condition.evaluate(wifiSSID: "Office", isAuthorized: true) == .satisfied)
        #expect(condition.evaluate(wifiSSID: "Home", isAuthorized: true) == .networkExcluded("Home"))
    }

    // MARK: Not knowing the network

    @Test func missingLocationPermissionNeverSatisfies() {
        // Denied permission must not be read as "not on that network".
        #expect(
            AutomationWiFiCondition.inAny(["Home"]).evaluate(wifiSSID: nil, isAuthorized: false)
                == .notAuthorized
        )
        #expect(
            AutomationWiFiCondition.notIn(["Home"]).evaluate(wifiSSID: nil, isAuthorized: false)
                == .notAuthorized
        )
    }

    @Test func noConnectionNeverSatisfies() {
        // Deliberate: an exclusion rule must not fire just because Wi-Fi dropped.
        #expect(AutomationWiFiCondition.notIn(["Home"]).evaluate(wifiSSID: nil, isAuthorized: true) == .noNetwork)
        #expect(AutomationWiFiCondition.inAny(["Home"]).evaluate(wifiSSID: "", isAuthorized: true) == .noNetwork)
        #expect(
            AutomationWiFiCondition.inAny(["Home"]).evaluate(wifiSSID: "   ", isAuthorized: true)
                == .noNetwork
        )
    }

    @Test func emptyListIsUnsatisfiedRatherThanMatchEverything() {
        #expect(AutomationWiFiCondition.inAny([]).evaluate(wifiSSID: "Home", isAuthorized: true) == .emptyList)
        #expect(AutomationWiFiCondition.notIn([]).evaluate(wifiSSID: "Home", isAuthorized: true) == .emptyList)
    }

    @Test func surroundingWhitespaceInAStoredSSIDIsIgnored() {
        #expect(
            AutomationWiFiCondition.inAny(["Home"]).evaluate(wifiSSID: " Home ", isAuthorized: true)
                == .satisfied
        )
    }

    // MARK: Normalisation

    @Test func normalizeTrimsDropsBlanksAndDeduplicates() {
        let normalized = AutomationWiFiCondition.normalize([" Home ", "", "Home", "  ", "Office"])
        #expect(normalized == ["Home", "Office"])
    }

    @Test func normalizeCapsTheListLength() {
        let many = (0..<40).map { "net\($0)" }
        #expect(AutomationWiFiCondition.normalize(many).count == AutomationWiFiCondition.ssidLimit)
        // The cap keeps the user's own order rather than sorting.
        #expect(AutomationWiFiCondition.normalize(many).first == "net0")
    }

    // MARK: Persistence

    @Test func conditionsRoundTripThroughJSON() throws {
        let rule = AutomationRule(
            trigger: .lidClosedSleep,
            action: .quickAction(.lightsOff),
            conditions: AutomationConditions(wifi: .inAny(["Home", "Office"]))
        )
        let data = try JSONEncoder().encode(rule)
        let decoded = try JSONDecoder().decode(AutomationRule.self, from: data)
        #expect(decoded == rule)
        #expect(decoded.conditions.wifi == .inAny(["Home", "Office"]))
    }

    @Test func notInAlsoRoundTrips() throws {
        let rule = AutomationRule(
            trigger: .screenLocked,
            action: .quickAction(.lightsOff),
            conditions: AutomationConditions(wifi: .notIn(["Home"]))
        )
        let decoded = try JSONDecoder().decode(AutomationRule.self, from: try JSONEncoder().encode(rule))
        #expect(decoded.conditions.wifi == .notIn(["Home"]))
    }

    @Test func ruleStoredBeforeConditionsDecodesAsUnconditional() throws {
        // A rule persisted by 1.1.7 has no `conditions` key at all.
        let legacy = """
        {
          "id": "9F1F0D3C-8E1B-4A55-9C0E-1B2C3D4E5F60",
          "isEnabled": true,
          "trigger": "away",
          "action": { "kind": "quickAction", "quickAction": "lightsOff" },
          "triggerOptions": { "batteryThresholdPercent": 20, "lateNightStartHour": 23, "lateNightEndHour": 6, "calendarLeadMinutes": 1 }
        }
        """
        let rule = try JSONDecoder().decode(AutomationRule.self, from: Data(legacy.utf8))
        #expect(rule.conditions.wifi == .any)
        #expect(rule.conditions.isUnconditional)
        #expect(rule.trigger == .away)
    }

    @Test func summaryDescribesTheConditionForTheList() {
        #expect(AutomationConditions(wifi: .any).summary() == nil)
        #expect(AutomationConditions(wifi: .inAny(["Home"])).summary()?.contains("Home") == true)
        #expect(AutomationConditions(wifi: .notIn(["Home"])).summary()?.contains("Home") == true)
        #expect(AutomationConditions(wifi: .inAny([])).summary() == nil)
    }

    // MARK: Permissions

    @Test func aNetworkConditionRequestsLocationEvenWithoutAWifiTrigger() {
        let rule = AutomationRule(
            trigger: .lidClosedSleep,
            action: .quickAction(.lightsOff),
            conditions: AutomationConditions(wifi: .inAny(["Office"]))
        )
        #expect(MacContextAutomation.requiredPermissions(for: [rule]).contains(.location))

        let unconditional = AutomationRule(trigger: .lidClosedSleep, action: .quickAction(.lightsOff))
        #expect(MacContextAutomation.requiredPermissions(for: [unconditional]).isEmpty)
    }

    @Test func disabledRulesRequestNothing() {
        let rule = AutomationRule(
            trigger: .away,
            action: .quickAction(.lightsOff),
            isEnabled: false,
            conditions: AutomationConditions(wifi: .inAny(["Office"]))
        )
        #expect(MacContextAutomation.requiredPermissions(for: [rule]).isEmpty)
    }

    // MARK: Precise triggers are not polled

    @Test func preciseAwayTriggersProduceNoPolledEvents() {
        let current = MacContextSnapshot(wifiSSID: "Home")
        for trigger in [AutomationTrigger.screenLocked, .lidClosedSleep, .systemSleep] {
            let rule = AutomationRule(trigger: trigger, action: .quickAction(.lightsOff))
            let events = MacContextAutomation.events(
                for: rule,
                previous: MacContextSnapshot(wifiSSID: "Office"),
                current: current,
                at: Date()
            )
            #expect(events.isEmpty)
        }
    }
}
