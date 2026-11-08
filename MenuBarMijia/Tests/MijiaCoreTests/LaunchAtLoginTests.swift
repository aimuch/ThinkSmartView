import Foundation
import Testing
@testable import MijiaCore

/// #281: the first-run guide turns launch-at-login on by default, upgraded installs get one
/// skippable checklist row, and Settings only shows what macOS actually reports.
struct LaunchAtLoginTests {
    @MainActor
    final class FakeSystem: LaunchAtLoginSystem {
        var status: LaunchAtLoginStatus = .notRegistered
        var registerCalls = 0
        var unregisterCalls = 0
        var openSettingsCalls = 0
        var registerError: Error?
        var unregisterError: Error?

        func register() throws {
            registerCalls += 1
            if let registerError { throw registerError }
            status = .enabled
        }

        func unregister() throws {
            unregisterCalls += 1
            if let unregisterError { throw unregisterError }
            status = .notRegistered
        }

        func openSystemSettings() {
            openSettingsCalls += 1
        }
    }

    enum FakeError: Error {
        case rejected
    }

    @MainActor
    static func makeStore(_ name: String) -> HomeStore {
        HomeStore(
            provider: MockHomeProvider(snapshot: SynthesizedHome(devices: [], scenes: [])),
            defaultsSuiteName: "test.launchAtLogin.\(name).\(UUID().uuidString)"
        )
    }

    @Test("The connect-step checkbox starts checked and applies exactly what the user chose")
    func onboardingChoiceDefaultsOn() {
        var policy = LaunchAtLoginPolicy()
        #expect(policy.connectStepToggleIsOn)
        #expect(policy.leaveConnectStepAction == .register)
        #expect(!policy.showsChecklistSuggestion(hasCompletedOnboarding: false))

        policy.onboardingChoice = false
        #expect(!policy.connectStepToggleIsOn)
        #expect(policy.leaveConnectStepAction == .unregister)
    }

    @Test("Settings only shows the toggle on when macOS reports the login item enabled")
    func settingsToggleFollowsSystem() {
        for status in [LaunchAtLoginStatus.notRegistered, .requiresApproval, .notFound] {
            #expect(!LaunchAtLoginPolicy(status: status).settingsToggleIsOn)
        }
        #expect(LaunchAtLoginPolicy(status: .enabled).settingsToggleIsOn)
    }

    @Test("A failed registration shows the manual hint and a checklist row")
    func failedRegistrationOffersSystemSettings() {
        let policy = LaunchAtLoginPolicy(
            status: .notRegistered,
            onboardingChoice: true,
            appliedOnboardingChoice: true,
            registrationFailed: true
        )
        #expect(policy.needsManualApproval)
        #expect(policy.showsChecklistSuggestion(hasCompletedOnboarding: false))
        #expect(policy.showsChecklistSuggestion(hasCompletedOnboarding: true))
    }

    @Test("Existing installs get one skippable checklist row, and enabling it clears the row")
    func existingInstallSuggestionIsSkippable() {
        let old = LaunchAtLoginPolicy(status: .notRegistered)
        #expect(old.showsChecklistSuggestion(hasCompletedOnboarding: true))

        var dismissed = old
        dismissed.dismissedSuggestion = true
        #expect(!dismissed.showsChecklistSuggestion(hasCompletedOnboarding: true))

        let enabled = LaunchAtLoginPolicy(status: .enabled)
        #expect(!enabled.showsChecklistSuggestion(hasCompletedOnboarding: true))

        var optedOut = old
        optedOut.onboardingChoice = false
        optedOut.appliedOnboardingChoice = true
        #expect(!optedOut.showsChecklistSuggestion(hasCompletedOnboarding: true))
    }

    @Test("New users register when they leave the connect step unless they cleared the checkbox")
    @MainActor
    func leavingConnectStepAppliesTheCheckbox() {
        let system = FakeSystem()
        let checkedStore = Self.makeStore("checked")
        LaunchAtLoginCoordinator(system: system, store: checkedStore).applyOnboardingChoiceIfNeeded()
        #expect(system.registerCalls == 1)
        #expect(system.unregisterCalls == 0)
        #expect(checkedStore.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(checkedStore.launchAtLoginStatus == .enabled)
        #expect(!checkedStore.shouldShowLaunchAtLoginSuggestion)

        let uncheckedSystem = FakeSystem()
        let uncheckedStore = Self.makeStore("unchecked")
        uncheckedStore.launchAtLoginOnboardingChoice = false
        LaunchAtLoginCoordinator(system: uncheckedSystem, store: uncheckedStore).applyOnboardingChoiceIfNeeded()
        #expect(uncheckedSystem.registerCalls == 0)
        #expect(uncheckedStore.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(uncheckedStore.launchAtLoginStatus == .notRegistered)
        #expect(!uncheckedStore.shouldShowLaunchAtLoginSuggestion)
    }

    @Test("A registration failure turns into an inline hint instead of a thrown error")
    @MainActor
    func registrationFailureIsReportedInline() {
        let system = FakeSystem()
        system.registerError = FakeError.rejected
        let store = Self.makeStore("failure")

        LaunchAtLoginCoordinator(system: system, store: store).applyOnboardingChoiceIfNeeded()

        #expect(system.registerCalls == 1)
        #expect(store.launchAtLoginRegistrationFailed)
        #expect(store.launchAtLoginPolicy.needsManualApproval)
        #expect(store.shouldShowLaunchAtLoginSuggestion)
        #expect(!store.launchAtLoginPolicy.settingsToggleIsOn)
    }

    @Test("Settings follows the system after both successful and failed changes")
    @MainActor
    func settingsFollowsTheSystem() {
        let system = FakeSystem()
        system.status = .enabled
        let store = Self.makeStore("settings")
        let coordinator = LaunchAtLoginCoordinator(system: system, store: store)
        coordinator.refresh()
        #expect(store.launchAtLoginPolicy.settingsToggleIsOn)

        coordinator.setEnabled(false)
        #expect(system.unregisterCalls == 1)
        #expect(!store.launchAtLoginPolicy.settingsToggleIsOn)

        system.registerError = FakeError.rejected
        coordinator.setEnabled(true)
        #expect(system.registerCalls == 1)
        #expect(!store.launchAtLoginPolicy.settingsToggleIsOn)
        #expect(store.launchAtLoginPolicy.needsManualApproval)
    }

    @Test("The store persists the onboarding choice and dismissal but never guesses the system state")
    @MainActor
    func storePersistsChoiceAndDismissal() {
        let suite = "test.launchAtLogin.persist.\(UUID().uuidString)"
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [], scenes: []))
        let first = HomeStore(provider: mock, defaultsSuiteName: suite)
        #expect(first.launchAtLoginOnboardingChoice)
        #expect(!first.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(!first.hasDismissedLaunchAtLoginSuggestion)
        #expect(first.launchAtLoginStatus == .notRegistered)

        first.launchAtLoginOnboardingChoice = false
        first.markLaunchAtLoginOnboardingChoiceApplied()
        first.dismissLaunchAtLoginSuggestion()
        first.launchAtLoginStatus = .enabled

        let second = HomeStore(provider: mock, defaultsSuiteName: suite)
        #expect(!second.launchAtLoginOnboardingChoice)
        #expect(second.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(second.hasDismissedLaunchAtLoginSuggestion)
        #expect(second.launchAtLoginStatus == .notRegistered)
    }

    @Test("A real Mi login registers the default-checked launch at login")
    @MainActor
    func realMiLoginRegistersLaunchAtLogin() {
        let suite = "test.launchAtLogin.mi-login.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let system = FakeSystem()
        let store = HomeStore(provider: MockHomeProvider(), defaultsSuiteName: suite)
        store.launchAtLoginSystem = system
        store.homeBackend = .xiaomiCloud
        store.completeMiLogin(MiSession(
            userId: "28100001",
            cUserId: "c",
            ssecurity: "s",
            serviceToken: "t",
            region: "cn"
        ))
        defer { store.logoutMiAccount() }

        #expect(system.registerCalls == 1)
        #expect(store.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(store.hasCompletedOnboarding)
        #expect(store.launchAtLoginStatus == .enabled)
    }

    @Test("A real Home Assistant connection registers the default-checked launch at login")
    @MainActor
    func realHAConnectionRegistersLaunchAtLogin() {
        let suite = "test.launchAtLogin.ha-connect.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let system = FakeSystem()
        let store = HomeStore(provider: MockHomeProvider(), defaultsSuiteName: suite)
        store.launchAtLoginSystem = system

        store.updateConnection(serverURL: "http://127.0.0.1:1", token: "test-token")

        #expect(system.registerCalls == 1)
        #expect(store.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(store.hasCompletedOnboarding)
        #expect(store.launchAtLoginStatus == .enabled)
    }

    @Test("A real Mi login does not register after the checkbox was cleared")
    @MainActor
    func uncheckedRealMiLoginDoesNotRegister() {
        let suite = "test.launchAtLogin.mi-login-unchecked.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let system = FakeSystem()
        let store = HomeStore(provider: MockHomeProvider(), defaultsSuiteName: suite)
        store.launchAtLoginSystem = system
        store.launchAtLoginOnboardingChoice = false

        store.completeMiLogin(MiSession(
            userId: "28100002",
            cUserId: "c",
            ssecurity: "s",
            serviceToken: "t",
            region: "cn"
        ))
        defer { store.logoutMiAccount() }

        #expect(system.registerCalls == 0)
        #expect(store.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(store.launchAtLoginStatus == .notRegistered)
    }

    @Test("A demo visit does not burn the one-shot onboarding choice")
    @MainActor
    func demoVisitDoesNotApplyLaunchAtLogin() {
        let suite = "test.launchAtLogin.demo.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let system = FakeSystem()
        let store = HomeStore(provider: MockHomeProvider(), defaultsSuiteName: suite)
        store.launchAtLoginSystem = system

        store.switchBackend(.demo)
        #expect(store.hasConfiguredHomeSource)
        #expect(!store.hasAppliedLaunchAtLoginOnboardingChoice)
        #expect(system.registerCalls == 0)

        store.switchBackend(.xiaomiCloud)
        store.launchAtLoginOnboardingChoice = false
        store.completeMiLogin(MiSession(
            userId: "28100003",
            cUserId: "c",
            ssecurity: "s",
            serviceToken: "t",
            region: "cn"
        ))
        defer { store.logoutMiAccount() }

        #expect(system.registerCalls == 0)
        #expect(system.unregisterCalls == 1)
        #expect(!store.launchAtLoginStatus.isEnabled)
    }

    @Test("Closing an old install's checklist does not register by itself")
    @MainActor
    func oldInstallCompletionDoesNotRegister() {
        let system = FakeSystem()
        let store = Self.makeStore("old-completion")
        store.launchAtLoginSystem = system
        store.hasCompletedOnboarding = true

        store.completeOnboarding()

        #expect(system.registerCalls == 0)
        #expect(!store.hasAppliedLaunchAtLoginOnboardingChoice)
    }

    @Test("A failed onboarding registration stays visible after a relaunch")
    @MainActor
    func registrationFailurePersists() {
        let suite = "test.launchAtLogin.failure-persist.\(UUID().uuidString)"
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }
        let mock = MockHomeProvider(snapshot: SynthesizedHome(devices: [], scenes: []))
        let system = FakeSystem()
        system.registerError = FakeError.rejected
        let first = HomeStore(provider: mock, defaultsSuiteName: suite)
        first.launchAtLoginSystem = system

        first.completeOnboarding()

        #expect(first.launchAtLoginRegistrationFailed)
        #expect(first.launchAtLoginPolicy.needsManualApproval)

        let second = HomeStore(provider: mock, defaultsSuiteName: suite)
        #expect(second.launchAtLoginRegistrationFailed)
        #expect(second.launchAtLoginPolicy.needsManualApproval)
    }

    @Test("An upgraded install sees the checklist card without the guide popping open")
    @MainActor
    func oldInstallGetsChecklistOnly() {
        let store = Self.makeStore("upgrade")
        store.hasCompletedOnboarding = true
        store.launchAtLoginStatus = .notRegistered

        #expect(!store.shouldShowOnboarding)
        #expect(!store.showsFullOnboardingGuide)
        #expect(store.shouldShowLaunchAtLoginSuggestion)
        #expect(store.showsOnboardingChecklistCard)

        store.dismissLaunchAtLoginSuggestion()
        #expect(!store.shouldShowLaunchAtLoginSuggestion)
        #expect(!store.showsOnboardingChecklistCard)

        store.launchAtLoginStatus = .enabled
        #expect(!store.shouldShowLaunchAtLoginSuggestion)
    }
}
