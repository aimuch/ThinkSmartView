import Foundation

/// The parts of `SMAppService.Status` the app reacts to. Declared here so `MijiaCore` stays free of
/// ServiceManagement and keeps building on Linux (#281, ADR-0003).
public enum LaunchAtLoginStatus: String, CaseIterable, Equatable, Sendable {
    case notRegistered
    case enabled
    case requiresApproval
    case notFound

    public var isEnabled: Bool { self == .enabled }
}

public enum LaunchAtLoginAction: Equatable, Sendable {
    case register
    case unregister
}

/// Pure policy for the connect-step checkbox, the dashboard checklist row and the Settings toggle (#281).
public struct LaunchAtLoginPolicy: Equatable, Sendable {
    public var status: LaunchAtLoginStatus
    public var onboardingChoice: Bool
    public var appliedOnboardingChoice: Bool
    public var dismissedSuggestion: Bool
    public var registrationFailed: Bool

    public init(
        status: LaunchAtLoginStatus = .notRegistered,
        onboardingChoice: Bool = true,
        appliedOnboardingChoice: Bool = false,
        dismissedSuggestion: Bool = false,
        registrationFailed: Bool = false
    ) {
        self.status = status
        self.onboardingChoice = onboardingChoice
        self.appliedOnboardingChoice = appliedOnboardingChoice
        self.dismissedSuggestion = dismissedSuggestion
        self.registrationFailed = registrationFailed
    }

    /// Settings binds its toggle here, so it never shows a state macOS is not in.
    public var settingsToggleIsOn: Bool { status.isEnabled }

    /// The connect-step checkbox is checked until the user clears it.
    public var connectStepToggleIsOn: Bool { onboardingChoice }

    /// Registration failed (common on ad-hoc builds) or macOS waits for manual approval.
    /// Both turn into one inline sentence and the System Settings button.
    public var needsManualApproval: Bool {
        registrationFailed || status == .requiresApproval
    }

    /// Upgraded installs get one row after onboarding. A new user sees it only when applying the
    /// checkbox failed, and clearing the checkbox suppresses it. Dismissing the row is permanent.
    public func showsChecklistSuggestion(hasCompletedOnboarding: Bool) -> Bool {
        guard onboardingChoice, !status.isEnabled, !dismissedSuggestion else { return false }
        return appliedOnboardingChoice || registrationFailed || hasCompletedOnboarding
    }

    /// Leaving the guide's connect step applies the checkbox exactly as shown.
    public var leaveConnectStepAction: LaunchAtLoginAction {
        onboardingChoice ? .register : .unregister
    }
}

@MainActor
public protocol LaunchAtLoginSystem {
    var status: LaunchAtLoginStatus { get }
    func register() throws
    func unregister() throws
    func openSystemSettings()
}

/// Applies the guide's choice to macOS and keeps the store's view of the system status honest.
/// The protocol keeps the ServiceManagement call out of core so tests drive the same paths.
@MainActor
public final class LaunchAtLoginCoordinator {
    public let store: HomeStore
    private let system: LaunchAtLoginSystem

    public init(system: LaunchAtLoginSystem, store: HomeStore) {
        self.system = system
        self.store = store
    }

    /// Reads the real login-item state. Call when a window appears or the app becomes active.
    public func refresh() {
        store.launchAtLoginStatus = system.status
        if system.status.isEnabled {
            store.launchAtLoginRegistrationFailed = false
        }
    }

    /// The guide calls this when the user leaves the connect step.
    public func applyOnboardingChoiceIfNeeded() {
        guard !store.hasAppliedLaunchAtLoginOnboardingChoice else {
            refresh()
            return
        }
        store.markLaunchAtLoginOnboardingChoiceApplied()
        switch store.launchAtLoginPolicy.leaveConnectStepAction {
        case .register: register()
        case .unregister: unregister()
        }
    }

    /// Settings binds its toggle here. The store is updated from the system after the attempt.
    public func setEnabled(_ enabled: Bool) {
        if enabled { register() } else { unregister() }
    }

    public func openSystemSettings() {
        system.openSystemSettings()
    }

    private func register() {
        do {
            try system.register()
        } catch {
            // Some ad-hoc builds throw even though the item is already in place. Trust the status.
            store.launchAtLoginRegistrationFailed = !system.status.isEnabled
            refresh()
            return
        }
        store.launchAtLoginRegistrationFailed = false
        refresh()
    }

    private func unregister() {
        do {
            try system.unregister()
        } catch {
            // Turning off an item that was never on is not a failure.
            store.launchAtLoginRegistrationFailed = system.status.isEnabled
            refresh()
            return
        }
        store.launchAtLoginRegistrationFailed = false
        refresh()
    }
}
